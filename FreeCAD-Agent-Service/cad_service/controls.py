import json

DEFAULTS = {'accepting_jobs': True, 'execution_paused': False, 'retention_count': 10,
            'job_timeout': 180, 'queue_limit': 20, 'per_agent_limit': 20}
RANGES = {'retention_count': (1, 1000), 'job_timeout': (5, 3600),
          'queue_limit': (1, 1000), 'per_agent_limit': (1, 1000)}


def read_settings(conn):
    row = conn.execute('SELECT data FROM service_settings WHERE id=1').fetchone()
    return {**DEFAULTS, **(json.loads(row[0]) if row else {})}


def validate_settings(changes):
    if not isinstance(changes, dict) or set(changes) - set(DEFAULTS):
        raise ValueError('未知设置项')
    for key, value in changes.items():
        if key in RANGES:
            low, high = RANGES[key]
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f'{key} 必须是 {low}–{high} 的整数')
        elif type(value) is not bool:
            raise ValueError(f'{key} 必须是布尔值')


def retention_rows(conn, count):
    return conn.execute('''SELECT id,agent,description,finished FROM jobs
        WHERE status='succeeded' AND artifacts_expired_at IS NULL AND id NOT IN
        (SELECT id FROM jobs WHERE status='succeeded' ORDER BY finished DESC,sequence DESC LIMIT ?)
        ORDER BY finished,sequence''', (count,)).fetchall()
