import os
from cad_service.queue import Queue

try:
    value = Queue(os.getenv('STATE_DIR', '/state')).health()
    raise SystemExit(0 if value['ready'] else 1)
except Exception:
    raise SystemExit(1)
