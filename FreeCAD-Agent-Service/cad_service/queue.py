"""SQLite is the durable queue. A single Linux worker owns the execution lock."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
from cad_service.controls import DEFAULTS, read_settings

TERMINAL = {'succeeded', 'failed', 'cancelled', 'interrupted'}
RETAIN_SUCCEEDED = 10


class Queue:
    def __init__(self, state):
        self.state = Path(state)

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.state / 'queue.sqlite3', timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def initialize(self):
        self.state.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE, agent TEXT NOT NULL,
                    description TEXT NOT NULL, script TEXT NOT NULL, digest TEXT NOT NULL,
                    request_id TEXT, status TEXT NOT NULL DEFAULT 'queued',
                    created REAL NOT NULL, started REAL, finished REAL,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    error TEXT, result TEXT);
                CREATE UNIQUE INDEX IF NOT EXISTS request_keys
                    ON jobs(agent, request_id) WHERE request_id IS NOT NULL;
                CREATE UNIQUE INDEX IF NOT EXISTS one_running_job
                    ON jobs(status) WHERE status='running';
                CREATE TABLE IF NOT EXISTS worker (
                    id INTEGER PRIMARY KEY CHECK(id=1), heartbeat REAL NOT NULL,
                    ready INTEGER NOT NULL, message TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS service_settings (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS agents (
                    agent_id TEXT PRIMARY KEY, display_name TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE, token_tail TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1, pending_limit INTEGER,
                    created REAL NOT NULL, last_seen REAL);
            ''')
            conn.execute('BEGIN IMMEDIATE')
            columns = {row[1] for row in conn.execute('PRAGMA table_info(jobs)')}
            if 'cleanup_pending' not in columns:
                conn.execute('ALTER TABLE jobs ADD COLUMN cleanup_pending INTEGER NOT NULL DEFAULT 0')
                conn.execute("UPDATE jobs SET cleanup_pending=1 WHERE status IN ('failed','cancelled','interrupted')")
            if 'cleaned_at' not in columns:
                conn.execute('ALTER TABLE jobs ADD COLUMN cleaned_at REAL')
            if 'artifacts_expired_at' not in columns:
                conn.execute('ALTER TABLE jobs ADD COLUMN artifacts_expired_at REAL')
            for column, sqltype in (('artifacts_expiry_reason', 'TEXT'), ('retention_limit_at_expiry', 'INTEGER'), ('timeout_applied', 'INTEGER')):
                if column not in columns:
                    conn.execute(f'ALTER TABLE jobs ADD COLUMN {column} {sqltype}')
            defaults = {**DEFAULTS, 'job_timeout': int(os.getenv('CAD_TIMEOUT', '180'))}
            conn.execute('INSERT OR IGNORE INTO service_settings VALUES(1,?)', (json.dumps(defaults),))

    def submit(self, agent, script, description='', request_id='', max_pending=None):
        if not isinstance(description, str) or len(description) > 2000:
            raise ValueError('description must contain at most 2000 characters')
        if not isinstance(request_id, str) or len(request_id) > 128:
            raise ValueError('request_id must contain at most 128 characters')
        digest = hashlib.sha256(json.dumps([script, description], ensure_ascii=False).encode()).hexdigest()
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if request_id:
                old = conn.execute('SELECT id,digest FROM jobs WHERE agent=? AND request_id=?', (agent, request_id)).fetchone()
                if old:
                    if old['digest'] != digest:
                        raise ValueError('request_id was already used for different content')
                    return {'job_id': old['id'], 'deduplicated': True}
            settings = read_settings(conn)
            if not settings['accepting_jobs']:
                raise ValueError('SUBMISSIONS_PAUSED: 服务已暂停接收新任务')
            count = conn.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
            if count >= (settings['queue_limit'] if max_pending is None else max_pending):
                raise ValueError('Queue full; retry later with the same request_id')
            config = conn.execute('SELECT pending_limit FROM agents WHERE agent_id=?', (agent,)).fetchone()
            limit = config['pending_limit'] if config and config['pending_limit'] is not None else settings['per_agent_limit']
            agent_count = conn.execute("SELECT count(*) FROM jobs WHERE agent=? AND status IN ('queued','running')", (agent,)).fetchone()[0]
            if agent_count >= limit:
                raise ValueError('AGENT_QUEUE_FULL: 该 Agent 已达到待处理任务上限')
            job_id = str(uuid.uuid4())
            conn.execute('INSERT INTO jobs(id,agent,description,script,digest,request_id,created) VALUES(?,?,?,?,?,?,?)',
                         (job_id, agent, description, script, digest, request_id or None, time.time()))
        return {'job_id': job_id, 'status': 'queued', 'deduplicated': False}

    def read(self, agent, job_id):
        with self.connection() as conn:
            row = conn.execute('SELECT * FROM jobs WHERE id=? AND agent=?', (job_id, agent)).fetchone()
            if row is None:
                raise LookupError('Job not found')
            result = dict(row)
            if row['status'] == 'queued':
                result['queue_position'] = conn.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running') AND sequence<=?", (row['sequence'],)).fetchone()[0]
            else:
                result['queue_position'] = None
        for key in ('script', 'digest', 'sequence', 'request_id'):
            result.pop(key)
        result['result'] = json.loads(result['result']) if result['result'] else None
        result['artifacts_expired'] = result['artifacts_expired_at'] is not None
        result['artifacts_available'] = result['status'] == 'succeeded' and not result['artifacts_expired']
        return result

    def list(self, agent, limit=20):
        if not 1 <= limit <= 50:
            raise ValueError('limit must be between 1 and 50')
        with self.connection() as conn:
            ids = [row[0] for row in conn.execute('SELECT id FROM jobs WHERE agent=? ORDER BY sequence DESC LIMIT ?', (agent, limit))]
        return [self.read(agent, job_id) for job_id in ids]

    def cancel(self, agent, job_id):
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT status FROM jobs WHERE id=? AND agent=?', (job_id, agent)).fetchone()
            if row is None:
                raise LookupError('Job not found')
            if row['status'] == 'queued':
                conn.execute("UPDATE jobs SET status='cancelled',cancel_requested=1,finished=? WHERE id=?", (time.time(), job_id))
            elif row['status'] == 'running':
                conn.execute('UPDATE jobs SET cancel_requested=1 WHERE id=?', (job_id,))
        return self.read(agent, job_id)

    def claim(self):
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            settings = read_settings(conn)
            if settings['execution_paused']:
                return None
            if conn.execute("SELECT 1 FROM jobs WHERE status='running'").fetchone():
                return None
            row = conn.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY sequence LIMIT 1").fetchone()
            if row:
                conn.execute("UPDATE jobs SET status='running',started=?,timeout_applied=? WHERE id=?", (time.time(), settings['job_timeout'], row['id']))
                return {**dict(row), 'timeout_applied': settings['job_timeout']}
        return None

    def cancelled(self, job_id):
        with self.connection() as conn:
            return bool(conn.execute('SELECT cancel_requested FROM jobs WHERE id=?', (job_id,)).fetchone()[0])

    def finish(self, job_id, status, result=None, error=None):
        if status not in TERMINAL:
            raise ValueError('Invalid terminal state')
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT cancel_requested,status FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row is None:
                raise LookupError('Job not found')
            if row['status'] != 'running':
                return row['status']
            if row['cancel_requested']:
                status, result, error = 'cancelled', None, 'Cancelled by client'
            conn.execute("UPDATE jobs SET status=?,result=?,error=?,finished=?,cleanup_pending=? WHERE id=? AND status='running'",
                         (status, json.dumps(result) if result is not None else None, error, time.time(), int(status != 'succeeded'), job_id))
            if status == 'succeeded':
                self._expire_old(conn)
        return status

    def _expire_old(self, conn):
        # Tombstone in the same transaction as success, before any file is deleted.
        limit = read_settings(conn)['retention_count']
        conn.execute("""UPDATE jobs SET artifacts_expired_at=?,cleanup_pending=1,artifacts_expiry_reason='retention',retention_limit_at_expiry=?
            WHERE status='succeeded' AND artifacts_expired_at IS NULL AND id NOT IN
            (SELECT id FROM jobs WHERE status='succeeded'
             ORDER BY finished DESC,sequence DESC LIMIT ?)""", (time.time(), limit, limit))

    def enforce_retention(self):
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._expire_old(conn)

    def recover(self):
        # Only after obtaining the exclusive worker file lock.
        with self.connection() as conn:
            conn.execute("UPDATE jobs SET status='interrupted',finished=?,cleanup_pending=1,error='Worker restarted; submit a new request_id to retry' WHERE status='running'", (time.time(),))

    def has_queued(self):
        with self.connection() as conn:
            return conn.execute("SELECT 1 FROM jobs WHERE status='queued' LIMIT 1").fetchone() is not None

    def settings(self):
        with self.connection() as conn:
            return read_settings(conn)

    def pending_cleanup(self, include_interrupted=False):
        statuses = ('failed', 'cancelled', 'interrupted') if include_interrupted else ('failed', 'cancelled')
        with self.connection() as conn:
            return [row[0] for row in conn.execute(
                "SELECT id FROM jobs WHERE cleanup_pending=1 AND (status IN (" + ','.join('?' for _ in statuses) + ") OR (status='succeeded' AND artifacts_expired_at IS NOT NULL)) ORDER BY sequence", statuses)]

    def complete_cleanup(self, job_id):
        with self.connection() as conn:
            conn.execute("UPDATE jobs SET cleanup_pending=0,cleaned_at=? WHERE id=? AND cleanup_pending=1 AND (status IN ('failed','cancelled','interrupted') OR (status='succeeded' AND artifacts_expired_at IS NOT NULL))", (time.time(), job_id))

    def cleanup_allowed(self, job_id):
        with self.connection() as conn:
            return conn.execute("SELECT 1 FROM jobs WHERE id=? AND cleanup_pending=1 AND (status IN ('failed','cancelled','interrupted') OR (status='succeeded' AND artifacts_expired_at IS NOT NULL))", (job_id,)).fetchone() is not None

    def heartbeat(self, ready, message):
        with self.connection() as conn:
            conn.execute('INSERT INTO worker VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET heartbeat=excluded.heartbeat,ready=excluded.ready,message=excluded.message', (time.time(), int(ready), message))

    def health(self):
        with self.connection() as conn:
            row = conn.execute('SELECT * FROM worker WHERE id=1').fetchone()
        return {'ready': bool(row and row['ready'] and time.time() - row['heartbeat'] < 20),
                'worker': row['message'] if row else 'waiting for worker'}
