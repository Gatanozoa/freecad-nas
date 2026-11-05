"""No network or third-party Python dependencies; one worker per data volume."""
import json
import logging
import math
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import threading
import time

from cad_service.queue import Queue
from cad_service import __version__
from cad_service.cleanup import clean_job, clean_pending, remove_child

log = logging.getLogger('cad-worker')


class Executor:
    def __init__(self, timeout=180, python=None, home_root='/tmp/cad'):
        self.timeout = timeout
        self.python = python or sys.executable
        self.home_root = Path(home_root)

    def run(self, folder, script, cancelled=lambda: False, stopping=lambda: False):
        folder = Path(folder).resolve()
        outcome = None
        try:
            outcome = self._run(folder, script, cancelled, stopping)
            return outcome
        finally:
            try:
                remove_child(self.home_root, folder.name)
            except (OSError, ValueError):
                if outcome is not None and outcome[0] == 'interrupted':
                    # Keep interruption semantics; persistent pending cleanup retries later.
                    log.exception('Temporary cleanup postponed for interrupted task')
                else:
                    raise

    def _run(self, folder, script, cancelled, stopping):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'source.py').write_text(script, encoding='utf-8')
        report_path = folder / 'report.json'
        report_path.unlink(missing_ok=True)
        home = self.home_root / folder.name
        for suffix in ('', 'config', 'data', 'cache', 'tmp'):
            (home / suffix).mkdir(parents=True, exist_ok=True)
        env = {
            'PATH': '/opt/freecad/usr/bin:/usr/bin:/bin', 'LD_LIBRARY_PATH': '/opt/freecad/usr/lib',
            'PYTHONPATH': str(Path(__file__).resolve().parents[1]),
            'HOME': str(home), 'FREECAD_USER_HOME': str(home),
            'FREECAD_USER_DATA': str(home / 'data'), 'FREECAD_USER_TEMP': str(home / 'tmp'),
            'XDG_CONFIG_HOME': str(home / 'config'), 'XDG_DATA_HOME': str(home / 'data'),
            'XDG_CACHE_HOME': str(home / 'cache'), 'TMPDIR': str(home / 'tmp'),
            'QT_QPA_PLATFORM': 'offscreen', 'PYTHONDONTWRITEBYTECODE': '1',
            'PYTHONUNBUFFERED': '1', 'CAD_TIMEOUT': str(self.timeout), 'LANG': 'C.UTF-8',
        }
        terminal_error = None
        with (folder / 'execution.log').open('wb') as output:
            process = subprocess.Popen([self.python, '-m', 'cad_service.engine', str(folder)], cwd=folder,
                                       env=env, stdout=output, stderr=output, start_new_session=True)
            deadline = time.monotonic() + self.timeout
            try:
                while process.poll() is None:
                    if stopping():
                        terminal_error = ('interrupted', 'Worker is stopping')
                        break
                    if cancelled():
                        terminal_error = ('cancelled', 'Cancelled by client')
                        break
                    if time.monotonic() >= deadline:
                        terminal_error = ('failed', f'FreeCAD timed out after {self.timeout} seconds')
                        break
                    time.sleep(.25)
            finally:
                # Also terminate any descendants after the main process exits.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        if terminal_error:
            return terminal_error[0], None, terminal_error[1]
        if process.returncode or not report_path.is_file():
            with (folder / 'execution.log').open('rb') as output:
                output.seek(0, 2)
                output.seek(max(0, output.tell() - 2000))
                message = output.read().decode('utf-8', errors='replace')
            return 'failed', None, message or f'FreeCAD exited with code {process.returncode}'
        report = json.loads(report_path.read_text(encoding='utf-8'))
        return 'succeeded', report, None


def main():
    import fcntl
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    log.info('FreeCAD Agent Service worker version %s', __version__)
    queue = Queue(os.getenv('STATE_DIR', '/state'))
    queue.initialize()
    lock = (queue.state / 'worker.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError('Another worker owns this queue; do not scale the worker')
    queue.recover()
    stop = threading.Event()
    ready = threading.Event()
    phase = {'message': 'FreeCAD startup test'}
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    def heartbeat():
        while not stop.is_set():
            try:
                queue.heartbeat(ready.is_set(), phase['message'])
            except sqlite3.Error:
                log.warning('Heartbeat database temporarily unavailable')
            stop.wait(2)
    heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
    heartbeat_thread.start()
    executor = Executor(int(os.getenv('CAD_TIMEOUT', '180')))
    smoke = """import FreeCAD as App
import Part
doc = App.newDocument('Startup')
result = doc.addObject('Part::Feature', 'Plate')
result.Shape = Part.makeBox(60, 30, 5).cut(Part.makeCylinder(3, 7, App.Vector(30, 15, -1)))
doc.recompute()
"""
    try:
        try:
            status, report, error = executor.run('/tmp/startup-work', smoke, stopping=stop.is_set)
        finally:
            remove_child(Path('/tmp'), 'startup-work')
        expected_volume = 60 * 30 * 5 - math.pi * 3 ** 2 * 5
        if status != 'succeeded' or any(abs(a-b) > 1e-4 for a,b in zip(report['dimensions'], [60,30,5])) or abs(report['volume'] - expected_volume) > 1e-3:
            raise RuntimeError('FreeCAD startup geometry/export test failed: ' + str(error))
        ready.set()
        phase['message'] = 'ready'
        queue.heartbeat(True, 'ready')
        log.info('FreeCAD geometry and FCStd/STEP/STL exports verified; accepting jobs')
        artifacts = Path(os.getenv('ARTIFACT_DIR', '/artifacts'))
        queue.enforce_retention()
        while not stop.is_set():
            try:
                # Interrupted outputs wait until another queued job is about to start.
                clean_pending(queue, artifacts, executor.home_root, include_interrupted=queue.has_queued() and not queue.settings()['execution_paused'])
                if not ready.is_set():
                    ready.set()
                    phase['message'] = 'ready'
                    queue.heartbeat(True, 'ready')
                row = queue.claim()
            except sqlite3.Error:
                log.warning('Queue database temporarily unavailable')
                stop.wait(2)
                continue
            except (OSError, ValueError):
                ready.clear()
                phase['message'] = 'cleanup blocked; see worker logs'
                log.exception('Cleanup failed; not starting another model')
                stop.wait(2)
                continue
            if row is None:
                stop.wait(.5)
                continue
            log.info('Starting job %s', row['id'])
            try:
                executor.timeout = row['timeout_applied']
                status, report, error = executor.run(artifacts / row['id'], row['script'],
                    cancelled=lambda: queue.cancelled(row['id']), stopping=stop.is_set)
            except Exception as exc:
                status, report, error = 'failed', None, f'{type(exc).__name__}: {exc}'[-2000:]
            # A DB failure must never cause automatic execution of this script again.
            while True:
                try:
                    status = queue.finish(row['id'], status, report, error)
                    break
                except sqlite3.Error:
                    log.warning('Cannot save job result; retrying persistence, not execution')
                    if stop.wait(2):
                        break
            if status in ('failed', 'cancelled'):
                try:
                    clean_job(queue, artifacts, executor.home_root, row['id'])
                except (OSError, ValueError, sqlite3.Error):
                    log.exception('Immediate cleanup failed; pending cleanup will retry')
            elif status == 'succeeded':
                try:
                    clean_pending(queue, artifacts, executor.home_root)
                except (OSError, ValueError, sqlite3.Error):
                    log.exception('Retention cleanup failed; pending cleanup will retry')
            log.info('Finished job %s: %s', row['id'], status)
    finally:
        ready.clear()
        stop.set()
        heartbeat_thread.join(timeout=3)
        try:
            queue.heartbeat(False, 'stopped')
        finally:
            lock.close()


if __name__ == '__main__':
    main()
