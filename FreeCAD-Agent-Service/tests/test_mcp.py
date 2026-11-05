import asyncio
import json
import logging
import os
from pathlib import Path
import site
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
site.addsitedir(str(ROOT / '.qa/deps'))
sys.path.insert(0, str(ROOT / '.qa/deps'))
logging.disable(logging.CRITICAL)

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.testclient import TestClient
from cad_service.api import create_app

TOKENS = {'codex': 'c' * 64, 'hermes': 'h' * 64}


def auth(name):
    return {'Authorization': 'Bearer ' + TOKENS[name]}


class MCPTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir=ROOT / 'tests')
        self.state = Path(self.folder.name) / 'state'
        self.artifacts = Path(self.folder.name) / 'artifacts'
        self.app = create_app(self.state, self.artifacts, TOKENS)

    def tearDown(self):
        self.folder.cleanup()

    def test_protocol_to_queue_to_authenticated_download(self):
        with TestClient(self.app) as client:
            self.assertEqual(client.get('/healthz').status_code, 503)
            self.app.queue.heartbeat(True, 'ready')
            self.assertEqual(client.get('/healthz').status_code, 200)
            self.assertEqual(client.post('/mcp', json={}).status_code, 401)
            self.assertEqual(client.get('/mcp', headers=auth('codex')).status_code, 405)
            self.assertEqual(client.post('/mcp', headers={**auth('codex'), 'Origin': 'http://bad.test'}, json={}).status_code, 403)
            self.assertEqual(client.post('/mcp', headers=auth('codex'), content=b'x' * (512*1024+1)).status_code, 413)

            async def protocol():
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), headers=auth('codex')) as http:
                    async with streamable_http_client('http://localhost:8000/mcp', http_client=http) as (read, write, _):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            self.assertEqual({tool.name for tool in (await session.list_tools()).tools}, {'submit_model', 'get_job', 'list_jobs', 'cancel_job', 'get_artifacts'})
                            async def call(name, arguments):
                                value = await session.call_tool(name, arguments)
                                if value.isError:
                                    return value
                                return value.structuredContent or json.loads(value.content[0].text)
                            arguments = {'script': 'result = None', 'description': 'test', 'request_id': 'one'}
                            first = await call('submit_model', arguments)
                            self.assertTrue((await call('submit_model', arguments))['deduplicated'])
                            self.assertTrue((await call('submit_model', {**arguments, 'script': 'import os'})).isError)
                            job_id = first['job_id']
                            self.assertEqual((await call('get_job', {'job_id': job_id}))['status'], 'queued')
                            self.assertEqual((await call('get_artifacts', {'job_id': job_id}))['files'], {})
                            self.assertEqual(len((await call('list_jobs', {}))['jobs']), 1)
                            row = self.app.queue.claim()
                            self.assertEqual(row['id'], job_id)
                            work = self.artifacts / job_id
                            work.mkdir(parents=True)
                            (work / 'model.stl').write_bytes(b'test-stl')
                            self.app.queue.finish(job_id, 'succeeded', {'files': ['model.stl']})
                            files = await call('get_artifacts', {'job_id': job_id})
                            self.assertTrue(files['files']['model.stl'].endswith('/model.stl'))
                            cancelled = await call('submit_model', {'script': 'result = None'})
                            self.assertEqual((await call('cancel_job', {'job_id': cancelled['job_id']}))['status'], 'cancelled')
                            return job_id
            job = asyncio.run(protocol())
            path = f'/files/{job}/model.stl'
            self.assertEqual(client.get(path).status_code, 401)
            self.assertEqual(client.get(path, headers=auth('hermes')).status_code, 404)
            self.assertEqual(client.get(path, headers=auth('codex')).content, b'test-stl')

    def test_parallel_clients_do_not_share_identity(self):
        async def submit(name):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), headers=auth(name)) as http:
                async with streamable_http_client('http://localhost:8000/mcp', http_client=http) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        value = await session.call_tool('submit_model', {'script': 'result = None', 'request_id': 'same'})
                        self.assertFalse(value.isError)
                        payload = value.structuredContent or json.loads(value.content[0].text)
                        listing = await session.call_tool('list_jobs', {})
                        listing = listing.structuredContent or json.loads(listing.content[0].text)
                        self.assertTrue(all(row['agent'] == name for row in listing['jobs']))
                        return payload['job_id']
        async def parallel():
            return await asyncio.gather(submit('codex'), submit('hermes'))
        with TestClient(self.app):
            one, two = asyncio.run(parallel())
            self.assertNotEqual(one, two)
            with self.assertRaises(LookupError):
                self.app.queue.read('hermes', one)

    def test_invalid_credentials_and_url_rejected(self):
        for agents in ([], {'a': 'short'}, {'a': 'a'*64, 'b': 'a'*64}):
            with self.assertRaises(ValueError):
                create_app(self.state, self.artifacts, agents)
        with self.assertRaises(ValueError):
            create_app(self.state, self.artifacts, TOKENS, 'http://user:password@host')

    def test_expired_outputs_return_410_and_structured_mcp_error(self):
        from cad_service.cleanup import clean_pending
        with TestClient(self.app) as client:
            ids = []
            for number in range(11):
                job = self.app.queue.submit('codex', 'result = None', request_id=str(number))['job_id']
                self.app.queue.claim()
                folder = self.artifacts / job
                folder.mkdir(parents=True)
                (folder / 'model.stl').write_bytes(b'test-stl')
                self.app.queue.finish(job, 'succeeded', {'files': ['model.stl']})
                ids.append(job)
            path = f'/files/{ids[0]}/model.stl'
            # Tombstone makes the error deterministic even before physical deletion.
            response = client.get(path, headers=auth('codex'))
            self.assertEqual(response.status_code, 410)
            self.assertEqual(response.json()['error']['code'], 'ARTIFACTS_EXPIRED')
            self.assertIn('request_id', response.json()['error']['action'])
            self.assertTrue((self.artifacts / ids[0]).exists())
            clean_pending(self.app.queue, self.artifacts, Path(self.folder.name) / 'homes')
            self.assertFalse((self.artifacts / ids[0]).exists())
            self.assertEqual(client.get(path, headers=auth('codex')).status_code, 410)
            self.assertEqual(client.get(path, headers=auth('hermes')).status_code, 404)
            self.assertEqual(client.get(path).status_code, 401)
            self.assertEqual(client.get(f'/files/{ids[-1]}/model.stl', headers=auth('codex')).content, b'test-stl')

            async def protocol():
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), headers=auth('codex')) as http:
                    async with streamable_http_client('http://localhost:8000/mcp', http_client=http) as (read, write, _):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            value = await session.call_tool('get_artifacts', {'job_id': ids[0]})
                            self.assertTrue(value.isError)
                            self.assertEqual(value.structuredContent['error']['code'], 'ARTIFACTS_EXPIRED')
                            self.assertEqual(value.structuredContent['files'], {})
            asyncio.run(protocol())

    def test_unexpected_missing_file_is_404_not_expiration(self):
        with TestClient(self.app) as client:
            job = self.app.queue.submit('codex', 'result=None')['job_id']
            self.app.queue.claim()
            self.app.queue.finish(job, 'succeeded', {'files': ['model.stl']})
            response = client.get(f'/files/{job}/model.stl', headers=auth('codex'))
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json()['error']['code'], 'ARTIFACT_NOT_FOUND')

    def test_retention_between_metadata_read_and_file_lookup_returns_410(self):
        from unittest.mock import patch
        from cad_service.cleanup import clean_pending
        with TestClient(self.app) as client:
            ids = []
            def success():
                job = self.app.queue.submit('codex', 'result=None')['job_id']
                self.app.queue.claim()
                folder = self.artifacts / job
                folder.mkdir(parents=True)
                (folder / 'model.stl').write_bytes(b'stl')
                self.app.queue.finish(job, 'succeeded', {'files': ['model.stl']})
                return job
            ids = [success() for _ in range(10)]
            original = self.app.queue.read
            triggered = False
            def race(agent, job):
                nonlocal triggered
                row = original(agent, job)
                if not triggered:
                    triggered = True
                    success()
                    clean_pending(self.app.queue, self.artifacts, Path(self.folder.name) / 'homes')
                return row
            with patch.object(self.app.queue, 'read', race):
                response = client.get(f'/files/{ids[0]}/model.stl', headers=auth('codex'))
            self.assertEqual(response.status_code, 410)
            self.assertEqual(response.json()['error']['code'], 'ARTIFACTS_EXPIRED')

    def test_accepted_download_finishes_if_expiration_happens_during_stream(self):
        from unittest.mock import patch
        from cad_service.api import OpenArtifactResponse
        with TestClient(self.app) as client:
            def success():
                job = self.app.queue.submit('codex', 'result=None')['job_id']
                self.app.queue.claim()
                folder = self.artifacts / job
                folder.mkdir(parents=True)
                (folder / 'model.stl').write_bytes(b'stl')
                self.app.queue.finish(job, 'succeeded', {'files': ['model.stl']})
                return job
            ids = [success() for _ in range(10)]
            handles = []
            def stream(response):
                handles.append(response.handle)
                success()  # Marks the served job expired after its file was opened.
                yield response.handle.read()
            path = f'/files/{ids[0]}/model.stl'
            with patch.object(OpenArtifactResponse, 'chunks', stream):
                response = client.get(path, headers=auth('codex'))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, b'stl')
            self.assertTrue(handles[0].closed)
            self.assertEqual(client.get(path, headers=auth('codex')).status_code, 410)
