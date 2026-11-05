from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from cad_service.worker import Executor

ROOT = Path(__file__).resolve().parents[1]


class ExecutorTests(unittest.TestCase):
    def run_executor(self, mode):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tests') as directory:
            folder = Path(directory)
            class Process:
                pid = 1234
                returncode = 0
                def poll(self):
                    return 0 if mode == 'success' else None
                def wait(self):
                    return self.returncode
            def launch(command, **kwargs):
                self.assertNotIn('CAD_AGENTS', kwargs['env'])
                self.assertNotIn('PUBLIC_URL', kwargs['env'])
                self.assertEqual(kwargs['env']['FREECAD_USER_HOME'], str(folder / 'homes/job'))
                self.assertEqual(kwargs['env']['CAD_TIMEOUT'], '0' if mode == 'timeout' else '10')
                (Path(kwargs['cwd']) / 'report.json').write_text('{"unit":"mm"}')
                return Process()
            with patch('cad_service.worker.subprocess.Popen', launch), \
                 patch('cad_service.worker.os.killpg', create=True) as kill, \
                 patch('cad_service.worker.signal.SIGKILL', 9, create=True):
                value = Executor(timeout=0 if mode == 'timeout' else 10, home_root=folder / 'homes').run(
                    folder / 'job', 'result = None', cancelled=lambda: mode == 'cancel', stopping=lambda: mode == 'stop')
                kill.assert_called_once_with(1234, 9)
                self.assertFalse((folder / 'homes/job').exists())
                self.assertTrue((folder / 'job/source.py').exists())
                return value

    def test_success_and_environment_allowlist(self):
        self.assertEqual(self.run_executor('success'), ('succeeded', {'unit': 'mm'}, None))

    def test_cancel_kills_group(self):
        self.assertEqual(self.run_executor('cancel')[0], 'cancelled')

    def test_shutdown_kills_group(self):
        self.assertEqual(self.run_executor('stop')[0], 'interrupted')

    def test_timeout_kills_group(self):
        self.assertEqual(self.run_executor('timeout')[0], 'failed')

    def test_launch_failure_still_cleans_temporary_directory(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tests') as directory:
            root = Path(directory)
            with patch('cad_service.worker.subprocess.Popen', side_effect=OSError('launch failed')):
                with self.assertRaisesRegex(OSError, 'launch failed'):
                    Executor(home_root=root / 'homes').run(root / 'job', 'result = None')
            self.assertFalse((root / 'homes/job').exists())

    def test_cleanup_error_does_not_reclassify_interruption_as_failure(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tests') as directory:
            executor = Executor(home_root=Path(directory) / 'homes')
            with patch.object(executor, '_run', return_value=('interrupted', None, 'stopping')), \
                 patch('cad_service.worker.remove_child', side_effect=PermissionError('busy')):
                self.assertEqual(executor.run(Path(directory) / 'job', 'result=None')[0], 'interrupted')
