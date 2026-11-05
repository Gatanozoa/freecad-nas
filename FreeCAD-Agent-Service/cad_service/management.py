"""Single-owner administration. No operation/audit history is recorded."""
import base64
import hashlib
import hmac
import json
import re
import secrets
import time

from cad_service.controls import read_settings, retention_rows, validate_settings


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600000).hex()


class AdminError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class Management:
    def __init__(self, queue):
        self.queue = queue

    def initialize(self, seed_agents):
        with self.queue.connection() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS management_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS admin_account (
                    id INTEGER PRIMARY KEY CHECK(id=1), username TEXT NOT NULL,
                    salt TEXT NOT NULL,password_hash TEXT NOT NULL,default_password INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS admin_sessions (
                    session_hash TEXT PRIMARY KEY,csrf TEXT NOT NULL,expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS login_limits (ip TEXT PRIMARY KEY,started REAL NOT NULL,attempts INTEGER NOT NULL);
            ''')
            conn.execute('BEGIN IMMEDIATE')
            if not conn.execute("SELECT 1 FROM management_meta WHERE key='agents_imported'").fetchone():
                for name, token in seed_agents.items():
                    conn.execute('INSERT OR IGNORE INTO agents(agent_id,display_name,token_hash,token_tail,created) VALUES(?,?,?,?,?)',
                                 (name, name, digest(token), token[-4:], time.time()))
                conn.execute("INSERT INTO management_meta VALUES('agents_imported','1')")
            if not conn.execute('SELECT 1 FROM admin_account').fetchone():
                salt = secrets.token_hex(16)
                conn.execute('INSERT INTO admin_account VALUES(1,?,?,?,1)', ('admin', salt, password_hash('admin', salt)))
            conn.execute("INSERT OR IGNORE INTO management_meta VALUES('preview_secret',?)", (secrets.token_hex(32),))

    def authenticate(self, token):
        if not token:
            return None
        with self.queue.connection() as conn:
            row = conn.execute('SELECT agent_id FROM agents WHERE enabled=1 AND token_hash=?', (digest(token),)).fetchone()
            if row:
                conn.execute('UPDATE agents SET last_seen=? WHERE agent_id=? AND (last_seen IS NULL OR last_seen<?)',
                             (time.time(), row['agent_id'], time.time()-60))
                return row['agent_id']
        return None

    def list_agents(self):
        with self.queue.connection() as conn:
            return [dict(row) for row in conn.execute('SELECT agent_id,display_name,token_tail,enabled,pending_limit,created,last_seen FROM agents ORDER BY created,agent_id')]

    def create_agent(self, name, display_name='', pending_limit=None):
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', name):
            raise AdminError('Agent ID 仅支持 1–40 位字母、数字、下划线和连字符')
        if not isinstance(display_name, str) or len(display_name) > 80:
            raise AdminError('显示名称最多 80 字符')
        self._quota(pending_limit)
        token = secrets.token_urlsafe(32)
        with self.queue.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute('SELECT 1 FROM agents WHERE agent_id=?', (name,)).fetchone():
                raise AdminError('Agent ID 已存在，不可复用', 409)
            conn.execute('INSERT INTO agents(agent_id,display_name,token_hash,token_tail,pending_limit,created) VALUES(?,?,?,?,?,?)',
                         (name, display_name or name, digest(token), token[-4:], pending_limit, time.time()))
        return token

    def _quota(self, value):
        if value is not None and (type(value) is not int or not 1 <= value <= 1000):
            raise AdminError('Agent 待处理上限须为空或 1–1000 的整数')

    def update_agent(self, name, changes):
        if not isinstance(changes, dict) or not changes or set(changes) - {'display_name', 'enabled', 'pending_limit'}:
            raise AdminError('无效 Agent 修改项')
        if 'display_name' in changes and (not isinstance(changes['display_name'], str) or not 1 <= len(changes['display_name']) <= 80):
            raise AdminError('显示名称须为 1–80 字符')
        if 'enabled' in changes and type(changes['enabled']) is not bool:
            raise AdminError('启用状态必须是布尔值')
        if 'pending_limit' in changes:
            self._quota(changes['pending_limit'])
        with self.queue.connection() as conn:
            result = conn.execute('UPDATE agents SET ' + ','.join(key+'=?' for key in changes) + ' WHERE agent_id=?', [*changes.values(), name])
            if result.rowcount != 1:
                raise AdminError('Agent 不存在', 404)

    def rotate_agent(self, name):
        token = secrets.token_urlsafe(32)
        with self.queue.connection() as conn:
            result = conn.execute('UPDATE agents SET token_hash=?,token_tail=? WHERE agent_id=?', (digest(token), token[-4:], name))
            if result.rowcount != 1:
                raise AdminError('Agent 不存在', 404)
        return token

    def _new_session(self, conn):
        session, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        conn.execute('DELETE FROM admin_sessions WHERE expires<?', (time.time(),))
        conn.execute('INSERT INTO admin_sessions VALUES(?,?,?)', (digest(session), csrf, time.time()+8*3600))
        return session, csrf

    def login(self, username, password, ip):
        if not isinstance(username, str) or not isinstance(password, str) or len(username)>80 or len(password)>128:
            raise AdminError('用户名或密码错误', 401)
        with self.queue.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            old = conn.execute('SELECT * FROM login_limits WHERE ip=?', (ip,)).fetchone()
            now = time.time()
            if old and now-old['started'] < 300 and old['attempts'] >= 5:
                raise AdminError('登录尝试过多，请 5 分钟后再试', 429)
            started, attempts = (old['started'], old['attempts']+1) if old and now-old['started'] < 300 else (now, 1)
            conn.execute('INSERT INTO login_limits VALUES(?,?,?) ON CONFLICT(ip) DO UPDATE SET started=excluded.started,attempts=excluded.attempts', (ip, started, attempts))
            account = dict(conn.execute('SELECT * FROM admin_account WHERE id=1').fetchone())
        match = hmac.compare_digest(password_hash(password, account['salt']), account['password_hash'])
        if not hmac.compare_digest(username.encode(), account['username'].encode()) or not match:
            raise AdminError('用户名或密码错误', 401)
        with self.queue.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            # Password might have been changed during the expensive hash calculation.
            current = conn.execute('SELECT password_hash FROM admin_account WHERE id=1').fetchone()[0]
            if current != account['password_hash']:
                raise AdminError('密码已改变，请重试', 401)
            conn.execute('DELETE FROM login_limits WHERE ip=?', (ip,))
            return self._new_session(conn)

    def session(self, token):
        with self.queue.connection() as conn:
            row = conn.execute('''SELECT s.csrf,s.expires,a.username,a.default_password FROM admin_sessions s
                JOIN admin_account a ON a.id=1 WHERE session_hash=? AND expires>?''', (digest(token or ''), time.time())).fetchone()
        return dict(row) if row else None

    def logout(self, token):
        with self.queue.connection() as conn:
            conn.execute('DELETE FROM admin_sessions WHERE session_hash=?', (digest(token),))

    def change_password(self, old_password, new_password, username=None):
        if not isinstance(new_password, str) or not 8 <= len(new_password) <= 128:
            raise AdminError('新密码须为 8–128 字符')
        if not isinstance(old_password, str) or len(old_password)>128:
            raise AdminError('原密码错误', 403)
        with self.queue.connection() as conn:
            account = dict(conn.execute('SELECT * FROM admin_account').fetchone())
        if not hmac.compare_digest(password_hash(old_password, account['salt']), account['password_hash']):
            raise AdminError('原密码错误', 403)
        username = account['username'] if username is None else username
        if not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,40}', username):
            raise AdminError('用户名须为 1–40 位字母、数字、点、下划线或连字符')
        salt = secrets.token_hex(16)
        hashed = password_hash(new_password, salt)
        with self.queue.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute('SELECT password_hash FROM admin_account').fetchone()[0] != account['password_hash']:
                raise AdminError('密码刚被修改，请重新登录', 409)
            conn.execute('UPDATE admin_account SET username=?,salt=?,password_hash=?,default_password=0 WHERE id=1', (username,salt,hashed))
            conn.execute('DELETE FROM admin_sessions')
            return self._new_session(conn)

    def _sign(self, payload):
        with self.queue.connection() as conn:
            secret = conn.execute("SELECT value FROM management_meta WHERE key='preview_secret'").fetchone()[0]
        raw = base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode()).decode()
        return raw+'.'+hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()

    def _verify(self, token, expected):
        if not isinstance(token, str) or len(token)>2000:
            raise AdminError('无效确认信息，请重新预览',409)
        try:
            raw, signature = token.split('.')
            payload = json.loads(base64.urlsafe_b64decode(raw))
            expected_token = self._sign(payload)
            if not hmac.compare_digest(token, expected_token) or payload['expires'] < time.time() or any(payload.get(key)!=value for key,value in expected.items()):
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise AdminError('预览已过期或任务列表已改变，请重新预览', 409)

    @staticmethod
    def _ids_hash(rows):
        return digest(json.dumps([row['id'] for row in rows]))

    def preview_retention(self, count):
        validate_settings({'retention_count': count})
        with self.queue.connection() as conn:
            rows = [dict(row) for row in retention_rows(conn, count)]
        payload = {'action': 'retention', 'count': count, 'ids': self._ids_hash(rows), 'expires': time.time()+300}
        return {'jobs': rows, 'count': len(rows), 'confirmation': self._sign(payload)}

    def save_settings(self, changes, confirmation=None):
        validate_settings(changes)
        with self.queue.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            old = read_settings(conn)
            updated = {**old, **changes}
            if updated['retention_count'] != old['retention_count']:
                rows = retention_rows(conn, updated['retention_count'])
                if rows:
                    self._verify(confirmation or '', {'action': 'retention', 'count': updated['retention_count'], 'ids': self._ids_hash(rows)})
            conn.execute('UPDATE service_settings SET data=? WHERE id=1', (json.dumps(updated),))
            self.queue._expire_old(conn)
        return updated

    def owner(self, job_id):
        with self.queue.connection() as conn:
            row = conn.execute('SELECT agent FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise AdminError('任务不存在', 404)
        return row['agent']

    def preview_delete(self, job_id):
        row = self.queue.read(self.owner(job_id), job_id)
        if not row['artifacts_available']:
            raise AdminError('任务没有可删除的成功产物', 409)
        return {'job': row, 'confirmation': self._sign({'action':'delete', 'job_id': job_id, 'expires':time.time()+300})}

    def delete_artifacts(self, job_id, confirmation):
        self._verify(confirmation or '', {'action':'delete', 'job_id':job_id})
        with self.queue.connection() as conn:
            result = conn.execute("UPDATE jobs SET artifacts_expired_at=?,artifacts_expiry_reason='manual',cleanup_pending=1 WHERE id=? AND status='succeeded' AND artifacts_expired_at IS NULL", (time.time(),job_id))
            if result.rowcount != 1:
                raise AdminError('产物已过期或任务状态已改变', 409)

    def jobs(self, agent='', status='', limit=30, offset=0):
        if status and status not in {'queued','running','succeeded','failed','cancelled','interrupted'}:
            raise AdminError('无效状态筛选')
        if not 1<=limit<=100 or not 0<=offset<=1000000:
            raise AdminError('无效分页')
        terms, values = [], []
        for key,value in [('agent',agent),('status',status)]:
            if value:
                terms.append(key+'=?'); values.append(value)
        condition = ' WHERE '+' AND '.join(terms) if terms else ''
        with self.queue.connection() as conn:
            count = conn.execute('SELECT count(*) FROM jobs'+condition, values).fetchone()[0]
            rows = conn.execute('SELECT id,agent FROM jobs'+condition+' ORDER BY sequence DESC LIMIT ? OFFSET ?', [*values,limit,offset]).fetchall()
        return {'total':count, 'jobs':[self.queue.read(row['agent'],row['id']) for row in rows]}

    def overview(self):
        with self.queue.connection() as conn:
            counts = {row[0]:row[1] for row in conn.execute('SELECT status,count(*) FROM jobs GROUP BY status')}
            stats = [dict(row) for row in conn.execute('''SELECT agent,count(*) total,
                sum(status='succeeded') succeeded,sum(status='failed') failed,
                sum(status='queued') queued,sum(status='running') running,
                avg(CASE WHEN finished IS NOT NULL AND started IS NOT NULL THEN finished-started END) avg_seconds
                FROM jobs GROUP BY agent ORDER BY total DESC''')]
            errors = [dict(row) for row in conn.execute("SELECT id,agent,error,finished FROM jobs WHERE error IS NOT NULL ORDER BY finished DESC LIMIT 8")]
            pending = conn.execute('SELECT count(*) FROM jobs WHERE cleanup_pending=1').fetchone()[0]
        for row in stats:
            denominator = row['succeeded']+row['failed']
            row['success_rate'] = row['succeeded']/denominator if denominator else None
        return {'health':self.queue.health(), 'counts':counts, 'stats':stats, 'errors':errors,
                'cleanup_pending':pending, 'settings':self.queue.settings()}
