from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cad_service.cleanup import clean_pending
from cad_service.queue import Queue

ROOT = Path(__file__).resolve().parents[1]


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tests')
        self.root = Path(self.temp.name)
        self.queue = Queue(self.root / 'state')
        self.queue.initialize()
        self.artifacts = self.root / 'artifacts'
        self.homes = self.root / 'homes'

    def tearDown(self):
        self.temp.cleanup()

    def make_success(self, number, agent='agent'):
        job = self.queue.submit(agent, 'result = None', str(number), str(number))['job_id']
        self.queue.claim()
        work = self.artifacts / job
        work.mkdir(parents=True)
        (work / 'model.stl').write_bytes(b'stl')
        self.queue.finish(job, 'succeeded', {'files': ['model.stl'], 'volume': 1000})
        return job

    def test_global_latest_ten_and_metadata_preserved(self):
        ids = [(self.make_success(i, 'one' if i % 2 else 'two'), 'one' if i % 2 else 'two') for i in range(12)]
        clean_pending(self.queue, self.artifacts, self.homes)
        self.assertEqual(len(list(self.artifacts.iterdir())), 10)
        for index, (job, agent) in enumerate(ids):
            row = self.queue.read(agent, job)
            self.assertEqual(row['status'], 'succeeded')
            self.assertEqual(row['result']['volume'], 1000)
            self.assertEqual(row['artifacts_expired'], index < 2)
            self.assertEqual(row['artifacts_available'], index >= 2)
            self.assertEqual((self.artifacts / job).exists(), index >= 2)

    def test_completion_time_and_sequence_tiebreak_determine_retention(self):
        with patch.object(self.queue, '_expire_old'):
            ids = [self.make_success(i) for i in range(11)]
        with self.queue.connection() as conn:
            conn.execute('UPDATE jobs SET finished=100')
            conn.execute('UPDATE jobs SET finished=200 WHERE id=?', (ids[0],))
        self.queue.enforce_retention()
        self.assertFalse(self.queue.read('agent', ids[0])['artifacts_expired'])
        self.assertTrue(self.queue.read('agent', ids[1])['artifacts_expired'])
        self.assertFalse(self.queue.read('agent', ids[-1])['artifacts_expired'])

    def test_failed_jobs_do_not_evict_successful_models(self):
        ids = [self.make_success(i) for i in range(10)]
        for _ in range(3):
            job = self.queue.submit('agent', 'result = None')['job_id']
            self.queue.claim()
            self.queue.finish(job, 'failed', error='test failure')
        self.queue.enforce_retention()
        clean_pending(self.queue, self.artifacts, self.homes)
        self.assertTrue(all((self.artifacts / job).is_dir() for job in ids))

    def test_expiration_is_durable_before_deletion_and_failed_delete_retries(self):
        ids = [self.make_success(i) for i in range(11)]
        first = self.queue.read('agent', ids[0])
        self.assertTrue(first['artifacts_expired'])
        self.assertTrue((self.artifacts / ids[0]).exists())
        with patch('cad_service.cleanup.shutil.rmtree', side_effect=PermissionError('busy')):
            with self.assertRaises(PermissionError):
                clean_pending(self.queue, self.artifacts, self.homes)
        self.assertEqual(self.queue.read('agent', ids[0])['cleanup_pending'], 1)
        clean_pending(self.queue, self.artifacts, self.homes)
        self.assertEqual(self.queue.read('agent', ids[0])['cleanup_pending'], 0)
        self.assertTrue(self.queue.read('agent', ids[0])['artifacts_expired'])

    def test_upgrade_applies_retention_to_historical_successes(self):
        with patch.object(self.queue, '_expire_old'):
            ids = [self.make_success(i) for i in range(12)]
        with self.queue.connection() as conn:
            conn.execute('ALTER TABLE jobs DROP COLUMN artifacts_expired_at')
        self.queue.initialize()
        self.queue.enforce_retention()
        clean_pending(self.queue, self.artifacts, self.homes)
        self.assertFalse((self.artifacts / ids[0]).exists())
        self.assertTrue((self.artifacts / ids[-1]).exists())

    def test_old_request_id_never_regenerates_expired_job(self):
        ids = [self.make_success(i) for i in range(11)]
        duplicate = self.queue.submit('agent', 'result = None', '0', '0')
        self.assertEqual(duplicate['job_id'], ids[0])
        self.assertTrue(duplicate['deduplicated'])
        self.assertTrue(self.queue.read('agent', ids[0])['artifacts_expired'])
        regenerated = self.queue.submit('agent', 'result = None', '0', 'new-request')
        self.assertNotEqual(regenerated['job_id'], ids[0])
