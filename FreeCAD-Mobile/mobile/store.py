"""Independent mobile archive. No dependency on CAD database or artifact volume."""
import contextlib
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
import uuid
from cryptography.fernet import Fernet

TERMINAL = {'review', 'confirmed', 'failed', 'cancelled', 'interrupted', 'needs_input'}

def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    return salt + ':' + hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 600000).hex()

def password_ok(password, hashed):
    return hmac.compare_digest(password_hash(password, hashed.split(':')[0]), hashed)

class Store:
    def __init__(self, root=None):
        self.root = Path(root or os.environ.get('DATA_DIR', '/data')).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / 'files').mkdir(exist_ok=True)
        (self.root / 'uploads').mkdir(exist_ok=True)
        self.cipher = Fernet(os.environ['MASTER_KEY'].encode())
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS account(id INTEGER PRIMARY KEY CHECK(id=1), username TEXT, password TEXT);
                CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, expires REAL);
                CREATE TABLE IF NOT EXISTS providers(id TEXT PRIMARY KEY, data TEXT, secret TEXT);
                CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, name TEXT, created REAL, deleted INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, project TEXT, version INTEGER, parent TEXT,
                    provider TEXT, prompt TEXT, images TEXT, status TEXT, stage TEXT, created REAL, updated REAL,
                    script TEXT, plan TEXT, report TEXT, usage TEXT, error TEXT, remote TEXT,
                    cancel INTEGER DEFAULT 0, position INTEGER, attempt INTEGER DEFAULT 0,
                    request_id TEXT UNIQUE, config TEXT, archived INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS runtime(id INTEGER PRIMARY KEY CHECK(id=1), heartbeat REAL);
                CREATE TABLE IF NOT EXISTS attempts(ip TEXT PRIMARY KEY, count INTEGER, since REAL);
                CREATE TABLE IF NOT EXISTS preferences(name TEXT PRIMARY KEY, value TEXT);
            ''')
            db.execute('BEGIN IMMEDIATE')
            columns={row[1] for row in db.execute('PRAGMA table_info(jobs)')}
            if 'cad_health' not in columns:
                db.execute('ALTER TABLE jobs ADD COLUMN cad_health TEXT')
            if not db.execute('SELECT 1 FROM account').fetchone():
                db.execute('INSERT OR IGNORE INTO account VALUES(1,?,?)', ('admin', password_hash(os.environ.get('INITIAL_PASSWORD', 'admin'))))

    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(self.root / 'mobile.sqlite', timeout=20)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def update(self, job_id, **fields):
        fields['updated'] = time.time()
        with self.db() as db:
            db.execute('UPDATE jobs SET ' + ','.join(k+'=?' for k in fields) + ' WHERE id=?', [*fields.values(), job_id])

    def job(self, job_id):
        with self.db() as db:
            row = db.execute('SELECT j.*,p.name FROM jobs j JOIN projects p ON j.project=p.id WHERE j.id=? AND p.deleted=0', (job_id,)).fetchone()
        if not row:
            raise LookupError('任务不存在')
        result = dict(row)
        for key in ('images', 'plan', 'report', 'usage', 'cad_health'):
            result[key] = json.loads(result[key]) if result[key] else ([] if key in ('images','usage') else None)
        result.pop('config', None)
        return result

    def provider(self, provider_id, secret=False):
        with self.db() as db:
            row = db.execute('SELECT * FROM providers WHERE id=?', (provider_id,)).fetchone()
        if not row:
            raise ValueError('请选择有效的模型配置')
        data = json.loads(row['data'])
        data['id'] = row['id']
        if secret:
            data['key'] = self.cipher.decrypt(row['secret'].encode()).decode()
        else:
            data['has_key'] = True
        return data

    def default_provider(self):
        with self.db() as db:
            row=db.execute("SELECT value FROM preferences WHERE name='default_provider'").fetchone()
            if row and db.execute('SELECT 1 FROM providers WHERE id=?',(row[0],)).fetchone():
                return row[0]
            first=db.execute('SELECT id FROM providers ORDER BY rowid LIMIT 1').fetchone()
            return first[0] if first else None

    def submit(self, prompt, provider, images, parent=None, demo=False, request_id=None):
        job_id = str(uuid.uuid4())
        config = self.provider(provider, True) if not demo else {}
        digest = hashlib.sha256(json.dumps([prompt,provider,images,parent,demo],sort_keys=True).encode()).hexdigest()
        idem = (request_id or job_id) + ':' + digest
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT id FROM jobs WHERE request_id=?', (idem,)).fetchone()
            if old:
                return old['id']
            if db.execute("SELECT count(*) FROM jobs WHERE status IN ('pending','working','cancelling')").fetchone()[0] >= 20:
                raise ValueError('待处理任务已满，请等待或取消部分任务')
            if parent:
                base = db.execute('SELECT j.* FROM jobs j JOIN projects p ON p.id=j.project WHERE j.id=? AND p.deleted=0', (parent,)).fetchone()
                if not base:
                    raise ValueError('原版本不存在')
                project = base['project']
                version = db.execute('SELECT max(version)+1 FROM jobs WHERE project=?', (project,)).fetchone()[0]
            else:
                project, version = str(uuid.uuid4()), 1
                db.execute('INSERT INTO projects(id,name,created) VALUES(?,?,?)', (project, '固定零件测试' if demo else prompt[:32], time.time()))
            # Snapshot encrypted credentials so changing a provider cannot change a running job.
            snapshot = self.cipher.encrypt(json.dumps(config).encode()).decode()
            db.execute('''INSERT INTO jobs(id,project,version,parent,provider,prompt,images,status,stage,created,updated,request_id,config)
                VALUES(?,?,?,?,?,?,?,'pending',?,?,?,?,?)''',
                (job_id,project,version,parent,provider,prompt,json.dumps(images),'demo' if demo else 'pending',time.time(),time.time(),idem,snapshot))
        return job_id

    def safe_error(self, message):
        text = str(message)
        with self.db() as db:
            secrets_to_hide = [self.cipher.decrypt(r[0].encode()).decode() for r in db.execute('SELECT secret FROM providers')]
            for row in db.execute('SELECT config FROM jobs WHERE config IS NOT NULL'):
                config = json.loads(self.cipher.decrypt(row[0].encode()))
                if config.get('key'):
                    secrets_to_hide.append(config['key'])
        secrets_to_hide.append(os.environ.get('CAD_TOKEN', ''))
        for key in secrets_to_hide:
            if key:
                text = text.replace(key, '[已隐藏]')
        return text[:3000]
