import importlib.util
from pathlib import Path
import tempfile
from concurrent.futures import ThreadPoolExecutor
import unittest

from cad_service.queue import Queue
from cad_service.policy import validate

ROOT = Path(__file__).resolve().parents[1]


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir=ROOT / 'tests')
        self.queue = Queue(self.folder.name)
        self.queue.initialize()

    def tearDown(self):
        self.folder.cleanup()

    def submit(self, agent='a', text='result = None', key=''):
        return self.queue.submit(agent, text, 'part', key)['job_id']

    def test_fifo_across_agents_and_single_execution(self):
        first, second = self.submit('a'), self.submit('b')
        self.assertEqual(self.queue.read('b', second)['queue_position'], 2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(lambda _: self.queue.claim(), range(2)))
        claimed = [row for row in claims if row]
        self.assertEqual([row['id'] for row in claimed], [first])
        self.queue.finish(first, 'succeeded', {'files': []})
        self.assertEqual(self.queue.read('b', second)['queue_position'], 1)
        self.assertEqual(self.queue.claim()['id'], second)

    def test_request_id_scope_and_conflict(self):
        first = self.submit('a', key='key')
        self.assertEqual(self.submit('a', key='key'), first)
        self.assertNotEqual(self.submit('b', key='key'), first)
        with self.assertRaisesRegex(ValueError, 'different content'):
            self.submit('a', 'changed = None', 'key')

    def test_access_isolation_and_queue_limit(self):
        job = self.submit()
        for operation in [lambda: self.queue.read('b', job), lambda: self.queue.cancel('b', job)]:
            with self.assertRaises(LookupError):
                operation()
        self.assertEqual(self.queue.list('b'), [])
        with self.assertRaisesRegex(ValueError, 'Queue full'):
            self.queue.submit('a', 'x=1', max_pending=1)
        self.assertNotIn('script', self.queue.read('a', job))

    def test_cancel_queued_and_cancel_completion_race(self):
        queued = self.submit()
        self.assertEqual(self.queue.cancel('a', queued)['status'], 'cancelled')
        self.assertIsNone(self.queue.claim())
        running = self.submit()
        self.queue.claim()
        self.assertEqual(self.queue.cancel('a', running)['status'], 'running')
        self.assertTrue(self.queue.cancelled(running))
        self.queue.finish(running, 'succeeded', {'files': ['model.stl']})
        row = self.queue.read('a', running)
        self.assertEqual(row['status'], 'cancelled')
        self.assertIsNone(row['result'])

    def test_restart_recovery_does_not_rerun_running_jobs(self):
        running, queued = self.submit(), self.submit()
        self.queue.claim()
        self.queue.recover()
        self.assertEqual(self.queue.read('a', running)['status'], 'interrupted')
        self.assertEqual(self.queue.claim()['id'], queued)

    def test_heartbeat_expiry(self):
        self.assertFalse(self.queue.health()['ready'])
        self.queue.heartbeat(True, 'ready')
        self.assertTrue(self.queue.health()['ready'])
        with self.queue.connection() as conn:
            conn.execute('UPDATE worker SET heartbeat=0')
        self.assertFalse(self.queue.health()['ready'])


class PolicyTests(unittest.TestCase):
    def test_geometry_and_disallowed_operations(self):
        validate('import FreeCAD as App\nimport Part\nfrom math import pi\nresult = Part.makeBox(1,2,3)')
        for source in ('import os', 'from . import Part', 'from FreeCAD import *', 'open("x")', 'f = eval',
                       'x.__class__', 'result.saveAs("x")', 'from Part import export as harmless',
                       'from FreeCAD import openDocument as helper', 'if :'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                validate(source)


class SetupTests(unittest.TestCase):
    def test_private_config_generation_no_env_and_no_overwrite(self):
        spec = importlib.util.spec_from_file_location('cad_setup', ROOT / 'setup.py')
        setup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(setup)
        import json
        with tempfile.TemporaryDirectory(dir=ROOT / 'tests') as directory:
            folder = Path(directory)
            (folder / 'compose.template.yml').write_bytes((ROOT / 'compose.template.yml').read_bytes())
            output = setup.generate(folder)
            self.assertNotIn('${', output.read_text())
            self.assertNotIn('__BIND_IP__', output.read_text())
            identities = json.loads((folder / 'AGENT-CONNECTIONS.private.json').read_text())
            self.assertEqual(set(identities), {'codex', 'hermes', 'mobile'})
            self.assertEqual(len({entry['authorization_header'] for entry in identities.values()}), 3)
            before = output.read_bytes()
            with self.assertRaises(FileExistsError):
                setup.generate(folder)
            self.assertEqual(before, output.read_bytes())
