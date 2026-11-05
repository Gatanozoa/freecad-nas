import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AuditTests(unittest.TestCase):
    def test_python_io_and_process_guards_in_separate_process(self):
        code = r'''
import os, subprocess, sys, types
from pathlib import Path
if sys.platform == 'win32':
    sys.modules['resource'] = types.SimpleNamespace()
from cad_service.engine import audit_guard
folder = Path(sys.argv[1])
work = folder / 'work'
home = folder / 'home'
outside = folder / 'outside'
for path in (work, home, outside):
    path.mkdir()
(outside / 'original').write_text('protected')
audit_guard(work, home)
(work / 'allowed').write_text('ok')
(home / 'allowed').write_text('ok')
def denied(operation):
    try:
        operation()
    except PermissionError:
        return
    raise AssertionError('Operation should have been blocked')
denied(lambda: os.open(outside / 'created', os.O_CREAT | os.O_WRONLY, 0o600))
denied(lambda: (folder / 'work-evil').write_text('prefix bypass'))
denied(lambda: os.remove(outside / 'original'))
denied(lambda: os.rename(work / 'allowed', outside / 'renamed'))
denied(lambda: os.symlink(work / 'allowed', work / 'link'))
denied(lambda: sys.audit('open', '/state/queue.sqlite3', 'r', 0))
denied(lambda: sys.audit('socket.connect', None, ('127.0.0.1', 80)))
denied(lambda: subprocess.run([sys.executable, '-c', 'pass']))
assert (outside / 'original').read_text() == 'protected'
'''
        with tempfile.TemporaryDirectory(dir=ROOT / 'tests') as directory:
            env = {**os.environ, 'PYTHONPATH': str(ROOT)}
            process = subprocess.run([sys.executable, '-c', code, directory], capture_output=True, text=True, env=env, timeout=15)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
