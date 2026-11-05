from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from cad_service.cleanup import clean_job, clean_pending, remove_child
from cad_service.queue import Queue

ROOT = Path(__file__).resolve().parents[1]


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tests')
        self.root = Path(self.temp.name)
        self.queue = Queue(self.root / 'state')
        self.queue.initialize()
        self.artifacts = self.root / 'artifacts'
        self.homes = self.root / 'homes'

    def tearDown(self):
        self.temp.cleanup()

    def make_job(self, status):
        job = self.queue.submit('agent', 'result = None')['job_id']
        self.assertEqual(self.queue.claim()['id'], job)
        for root in (self.artifacts, self.homes):
            folder = root / job
            folder.mkdir(parents=True)
            (folder / 'partial').write_text('test')
        if status is not None:
            self.queue.finish(job, status, {'files': []} if status == 'succeeded' else None, 'saved error' if status == 'failed' else None)
        return job

    def test_failed_cleanup_removes_all_files_keeps_record_and_error(self):
        job = self.make_job('failed')
        clean_job(self.queue, self.artifacts, self.homes, job)
        self.assertFalse((self.artifacts / job).exists())
        self.assertFalse((self.homes / job).exists())
        row = self.queue.read('agent', job)
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(row['error'], 'saved error')
        self.assertEqual(row['cleanup_pending'], 0)
        self.assertIsNotNone(row['cleaned_at'])

    def test_cancellation_race_cleans_even_if_engine_reported_success(self):
        job = self.make_job(None)
        self.queue.cancel('agent', job)
        actual = self.queue.finish(job, 'succeeded', {'files': ['model.stl']})
        self.assertEqual(actual, 'cancelled')
        clean_job(self.queue, self.artifacts, self.homes, job)
        self.assertFalse((self.artifacts / job).exists())
        self.assertIsNone(self.queue.read('agent', job)['result'])

    def test_interrupted_waits_for_next_job_without_rerun(self):
        job = self.make_job(None)
        self.queue.recover()
        clean_pending(self.queue, self.artifacts, self.homes, include_interrupted=self.queue.has_queued())
        self.assertTrue((self.artifacts / job).exists())
        next_job = self.queue.submit('agent', 'result = None', request_id='new')['job_id']
        clean_pending(self.queue, self.artifacts, self.homes, include_interrupted=self.queue.has_queued())
        self.assertFalse((self.artifacts / job).exists())
        self.assertEqual(self.queue.claim()['id'], next_job)
        self.assertEqual(self.queue.read('agent', job)['status'], 'interrupted')

    def test_successful_files_are_protected_even_if_cleanup_called(self):
        job = self.make_job('succeeded')
        clean_job(self.queue, self.artifacts, self.homes, job)
        clean_pending(self.queue, self.artifacts, self.homes, include_interrupted=True)
        self.assertTrue((self.artifacts / job / 'partial').is_file())
        self.assertEqual(self.queue.read('agent', job)['cleanup_pending'], 0)

    def test_partial_delete_failure_remains_pending_and_retry_is_idempotent(self):
        job = self.make_job('failed')
        import cad_service.cleanup as cleanup
        original = cleanup.remove_child
        def fail_artifacts(root, name, **kwargs):
            if root == self.artifacts:
                raise PermissionError('simulated refusal')
            return original(root, name, **kwargs)
        with patch.object(cleanup, 'remove_child', fail_artifacts), self.assertRaises(PermissionError):
            clean_job(self.queue, self.artifacts, self.homes, job)
        self.assertEqual(self.queue.read('agent', job)['cleanup_pending'], 1)
        self.assertFalse((self.homes / job).exists())
        self.assertTrue((self.artifacts / job).exists())
        clean_job(self.queue, self.artifacts, self.homes, job)
        clean_job(self.queue, self.artifacts, self.homes, job)
        self.assertFalse((self.artifacts / job).exists())
        self.assertEqual(self.queue.read('agent', job)['cleanup_pending'], 0)

    def test_rejects_root_traversal_absolute_and_non_uuid_names(self):
        protected = self.root / 'protected'
        protected.mkdir()
        (protected / 'keep').write_text('safe')
        for name in ('', '.', '..', '../protected', str(protected), 'abc/def', 'abc\\def'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                remove_child(self.artifacts, name)
        with self.assertRaises(ValueError):
            remove_child(self.root, 'protected', require_uuid=True)
        self.assertEqual((protected / 'keep').read_text(), 'safe')

    def test_rejects_link_before_recursive_delete(self):
        root = self.artifacts
        root.mkdir()
        candidate = root / 'job'
        with patch.object(Path, 'is_symlink', return_value=True), patch('cad_service.cleanup.shutil.rmtree') as delete:
            with self.assertRaises(ValueError):
                remove_child(root, 'job')
        delete.assert_not_called()

    def test_cleaning_files_then_crash_before_db_update_can_recover(self):
        job = self.make_job('failed')
        with patch.object(self.queue, 'complete_cleanup', side_effect=sqlite3.OperationalError('unavailable')):
            with self.assertRaises(sqlite3.Error):
                clean_job(self.queue, self.artifacts, self.homes, job)
        self.assertFalse((self.artifacts / job).exists())
        self.assertEqual(self.queue.read('agent', job)['cleanup_pending'], 1)
        clean_pending(self.queue, self.artifacts, self.homes)
        self.assertEqual(self.queue.read('agent', job)['cleanup_pending'], 0)

    def test_old_database_migrates_without_losing_jobs_or_request_keys(self):
        job = self.queue.submit('agent', 'result = None', 'old job', 'keep-key')['job_id']
        self.queue.claim()
        self.queue.finish(job, 'failed', error='old error')
        with self.queue.connection() as conn:
            conn.execute('ALTER TABLE jobs DROP COLUMN cleanup_pending')
            conn.execute('ALTER TABLE jobs DROP COLUMN cleaned_at')
        self.queue.initialize()
        self.assertEqual(self.queue.read('agent', job)['cleanup_pending'], 1)
        self.assertEqual(self.queue.read('agent', job)['error'], 'old error')
        self.assertEqual(self.queue.submit('agent', 'result = None', 'old job', 'keep-key')['job_id'], job)
