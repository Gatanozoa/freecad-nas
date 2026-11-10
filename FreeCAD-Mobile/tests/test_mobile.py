import bootstrap
import asyncio
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,AsyncMock
from cryptography.fernet import Fernet
from starlette.testclient import TestClient
from mobile.store import Store
from mobile.api import create_app
from mobile import worker

class MobileTests(unittest.TestCase):
    def setUp(self):
        qa=bootstrap.ROOT/'.qa';qa.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=qa)
        self.env=patch.dict(os.environ,{'MASTER_KEY':Fernet.generate_key().decode(),'CAD_URL':'http://cad:8088/mcp','CAD_TOKEN':'private-cad-token'})
        self.env.start();self.store=Store(self.temp.name);self.client=TestClient(create_app(self.store))
        self.login()
    def tearDown(self): self.client.close();self.env.stop();self.temp.cleanup()
    def login(self):
        self.assertEqual(self.client.post('/api/login',json={'username':'admin','password':'admin'}).status_code,200)
    def test_chat_history_is_project_scoped(self):
        first=self.job()
        second=self.job(parent=first['id'])
        other=self.job()
        rows=self.client.get('/api/jobs',params={'project':first['project']}).json()
        self.assertEqual([r['id'] for r in rows],[first['id'],second['id']])
        self.assertNotIn(other['id'],[r['id'] for r in rows])
        self.assertTrue(all('script' not in r and 'config' not in r for r in rows))
        self.assertEqual(self.client.get('/api/jobs',params={'project':"' OR 1=1 --"}).json(),[])
    def provider(self):
        r=self.client.post('/api/providers',json={'name':'测试模型','url':'http://model/v1','model':'test','key':'sensitive-model-key','vision':True})
        self.assertEqual(r.status_code,200,r.text);return r.json()['id']
    def job(self,**kw):
        r=self.client.post('/api/jobs',json={'prompt':'固定零件','demo':True,**kw});self.assertEqual(r.status_code,202,r.text);return r.json()
    def test_unauthenticated_and_origin(self):
        with TestClient(create_app(self.store)) as other:self.assertEqual(other.get('/api/jobs').status_code,401)
        self.assertEqual(self.client.post('/api/jobs',json={},headers={'Origin':'http://evil'}).status_code,403)
    def test_provider_secret_encrypted_and_not_returned(self):
        pid=self.provider();self.assertNotIn('sensitive-model-key',self.client.get('/api/providers').text)
        with self.store.db() as db:
            self.assertNotIn('sensitive-model-key',db.execute('SELECT secret FROM providers').fetchone()[0])
        self.assertEqual(self.store.provider(pid,True)['key'],'sensitive-model-key')
    def test_snapshot_survives_provider_delete(self):
        pid=self.provider();job=self.job(demo=False,provider=pid)
        self.client.delete('/api/providers/'+pid)
        snapshot=json.loads(self.store.cipher.decrypt(worker._config(self.store,job['id']).encode()))
        self.assertEqual(snapshot['key'],'sensitive-model-key')
    def test_versions_and_idempotency(self):
        first=self.job(request_id='stable');again=self.job(request_id='stable');self.assertEqual(first['id'],again['id'])
        new=self.job(parent=first['id']);self.assertEqual(new['project'],first['project']);self.assertEqual(new['version'],2)
    def test_cancel_before_claim(self):
        j=self.job();r=self.client.post('/api/jobs/'+j['id']+'/cancel',json={});self.assertEqual(r.json()['status'],'cancelled')
    def test_password_invalidates_sessions(self):
        self.assertEqual(self.client.post('/api/password',json={'old':'admin','new':'new-password'}).status_code,200)
        self.assertEqual(self.client.get('/api/session').status_code,401)
        self.assertEqual(self.client.post('/api/login',json={'username':'admin','password':'new-password'}).status_code,200)
    def test_bad_images_and_paths(self):
        self.assertEqual(self.client.post('/api/uploads',content=b'not-image',headers={'Content-Type':'image/png'}).status_code,400)
        self.assertEqual(self.client.post('/api/jobs',json={'prompt':'test','demo':True,'images':['../bad.jpg']}).status_code,400)
    def test_missing_archive_explicit(self):
        j=self.job();self.store.update(j['id'],status='confirmed',archived=1)
        r=self.client.get('/api/jobs/'+j['id']+'/files/model.stl');self.assertEqual(r.status_code,410)
        self.assertEqual(r.json()['code'],'ARCHIVE_MISSING')
    def test_needs_input_no_cad_submission(self):
        pid=self.provider();j=self.job(demo=False,provider=pid)
        plan={'script':'','questions':['孔径是多少？'],'summary':'需要尺寸','assumptions':[]}
        with patch('mobile.worker.generate',AsyncMock(return_value=(plan,{'total_tokens':10}))),patch('mobile.worker.cad_call',AsyncMock()) as cad:
            asyncio.run(worker.process(self.store,j['id']));cad.assert_not_awaited()
        self.assertEqual(self.store.job(j['id'])['status'],'needs_input')
    def test_modify_passes_base_script(self):
        parent=self.job();self.store.update(parent['id'],script=worker.DEMO,status='confirmed')
        pid=self.provider();j=self.job(demo=False,provider=pid,parent=parent['id'])
        gen=AsyncMock(return_value=({'script':'','questions':['确认孔径'],'summary':'','assumptions':[]},None))
        with patch('mobile.worker.generate',gen):asyncio.run(worker.process(self.store,j['id']))
        self.assertEqual(gen.call_args.args[3],worker.DEMO)
    def test_confirmation_gated_and_same_job(self):
        j=self.job();self.assertEqual(self.client.post('/api/jobs/'+j['id']+'/confirm',json={}).status_code,400)
        self.store.update(j['id'],status='review',archived=1)
        r=self.client.post('/api/jobs/'+j['id']+'/confirm',json={});self.assertEqual(r.json()['status'],'confirmed');self.assertEqual(r.json()['id'],j['id'])
    def test_delete_blocks_active_then_hides_project(self):
        j=self.job();self.assertEqual(self.client.delete('/api/projects/'+j['project']).status_code,400)
        self.client.post('/api/jobs/'+j['id']+'/cancel',json={})
        self.assertEqual(self.client.delete('/api/projects/'+j['project']).status_code,200)
        self.assertEqual(self.client.get('/api/jobs/'+j['id']).status_code,404)
    def test_no_unsafe_cleanup(self):
        with self.assertRaises(ValueError):worker.remove_directory(self.store.root,'../other')
    def test_archive_download_does_not_need_cad(self):
        j=self.job();folder=self.store.root/'files'/j['id'];folder.mkdir();(folder/'model.stl').write_bytes(b'archived')
        self.store.update(j['id'],status='confirmed',archived=1)
        with patch.dict(os.environ,{'CAD_URL':'http://unreachable.invalid/mcp'}):
            response=self.client.get('/api/jobs/'+j['id']+'/files/model.stl')
        self.assertEqual(response.content,b'archived')
    def test_cancel_during_generation(self):
        pid=self.provider();j=self.job(demo=False,provider=pid)
        async def slow(*args):
            self.store.update(j['id'],cancel=1)
            await asyncio.sleep(5)
        with patch('mobile.worker.generate',slow):asyncio.run(worker.process(self.store,j['id']))
        self.assertEqual(self.store.job(j['id'])['status'],'cancelled')
    def test_repairs_only_once(self):
        pid=self.provider();j=self.job(demo=False,provider=pid)
        gen=AsyncMock(return_value=({'script':worker.DEMO,'questions':[],'summary':'','assumptions':[]}, {'total_tokens':2}))
        with patch('mobile.worker.generate',gen),patch('mobile.worker.execute',AsyncMock(side_effect=ValueError('invalid solid'))):
            asyncio.run(worker.process(self.store,j['id']))
        self.assertEqual(gen.await_count,2);self.assertEqual(self.store.job(j['id'])['status'],'failed')
        self.assertEqual(len(self.store.job(j['id'])['usage']),2)
    def test_missing_result_repairs_before_cad_submission(self):
        pid=self.provider();j=self.job(demo=False,provider=pid)
        gen=AsyncMock(side_effect=[({'script':'shape = None','questions':[]},None),({'script':worker.DEMO,'questions':[]},None)])
        execute=AsyncMock(return_value={'ok':True})
        with patch('mobile.worker.generate',gen),patch('mobile.worker.execute',execute),patch('mobile.worker.archive',AsyncMock()):
            asyncio.run(worker.process(self.store,j['id']))
        self.assertEqual(gen.await_count,2);self.assertEqual(execute.await_count,1)
        self.assertIn('result',gen.call_args.args[4])
    def test_recovers_atomic_archive_without_remote_access(self):
        j=self.job();folder=self.store.root/'files'/j['id'];folder.mkdir()
        for name in ('model.FCStd','model.step','model.stl','report.json','source.py'):(folder/name).write_bytes(b'complete')
        with patch('mobile.worker.cad_call',AsyncMock()) as cad:
            asyncio.run(worker.process(self.store,j['id']));cad.assert_not_awaited()
        self.assertEqual(self.store.job(j['id'])['status'],'review')
    def test_secret_redaction_including_snapshot(self):
        pid=self.provider();self.job(demo=False,provider=pid);self.client.delete('/api/providers/'+pid)
        self.assertNotIn('sensitive-model-key',self.store.safe_error('sensitive-model-key private-cad-token'))

if __name__=='__main__':unittest.main(verbosity=2)
