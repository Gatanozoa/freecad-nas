import asyncio
import json
from pathlib import Path
import site
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
site.addsitedir(str(ROOT/'.qa/deps'));sys.path.insert(0,str(ROOT/'.qa/deps'))
from starlette.testclient import TestClient
from cad_service.api import create_app
from cad_service.management import AdminError, Management
from cad_service.queue import Queue
from cad_service.cleanup import clean_pending

SEED={'existing':'z'*64}


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'tests')
        self.root=Path(self.temp.name)
        self.app=create_app(self.root/'state',self.root/'artifacts',SEED,base_url='http://testserver')
        self.client=TestClient(self.app)
        self.client.__enter__()
        self.csrf=''

    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.temp.cleanup()

    def login(self,password='admin',username='admin'):
        response=self.client.post('/api/admin/login',json={'username':username,'password':password})
        self.assertEqual(response.status_code,200,response.text)
        self.csrf=response.json()['csrf']
        return response

    def send(self,method,path,data=None,**kwargs):
        return self.client.request(method,'/api/admin/'+path,json=data,headers={'X-CSRF-Token':self.csrf,**kwargs})

    def success(self,description='part'):
        job=self.app.queue.submit('existing','result=None',description)['job_id']
        self.app.queue.claim()
        work=self.root/'artifacts'/job;work.mkdir(parents=True)
        (work/'model.stl').write_bytes(b'stl-model')
        self.app.queue.finish(job,'succeeded',{'files':['model.stl'],'volume':1000})
        return job

    def test_public_page_requires_login_for_management(self):
        page=self.client.get('/admin')
        self.assertEqual(page.status_code,200)
        self.assertIn('登录管理后台',page.text)
        self.assertEqual(self.client.get('/admin/assets/app.js').status_code,200)
        self.assertEqual(self.client.get('/api/admin/agents').status_code,401)
        self.assertEqual(self.client.get('/api/admin/agents',headers={'Authorization':'Bearer '+SEED['existing']}).status_code,401)
        self.login()
        me=self.client.get('/api/admin/session').json()
        self.assertEqual(me['username'],'admin');self.assertTrue(me['default_password'])
        self.assertNotIn('password_hash',me)

    def test_csrf_origin_and_logout(self):
        self.login()
        self.assertEqual(self.client.post('/api/admin/agents',json={'agent_id':'no-csrf'}).status_code,403)
        denied=self.send('POST','agents',{'agent_id':'bad'},Origin='http://evil.test')
        self.assertEqual(denied.status_code,403)
        self.assertEqual(self.send('POST','logout',{}).status_code,200)
        self.assertEqual(self.client.get('/api/admin/overview').status_code,401)

    def test_password_change_invalidates_other_sessions_and_survives_restart(self):
        self.login(); old_cookie=self.client.cookies.get('cad_admin')
        wrong=self.send('POST','password',{'current_password':'wrong','new_password':'correct-password'})
        self.assertEqual(wrong.status_code,403)
        response=self.send('POST','password',{'current_password':'admin','new_password':'correct-password','username':'owner'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertIsNone(self.app.management.session(old_cookie))
        self.assertFalse(self.client.get('/api/admin/session').json()['default_password'])
        with self.app.queue.connection() as conn:
            stored=conn.execute('SELECT * FROM admin_account').fetchone()
            self.assertNotIn('correct-password',str(dict(stored)))
        self.app.management.initialize(SEED)
        self.assertEqual(self.client.post('/api/admin/login',json={'username':'admin','password':'admin'}).status_code,401)
        self.login('correct-password','owner')

    def test_login_rate_limit(self):
        for _ in range(5):
            self.assertEqual(self.client.post('/api/admin/login',json={'username':'admin','password':'bad'}).status_code,401)
        self.assertEqual(self.client.post('/api/admin/login',json={'username':'admin','password':'admin'}).status_code,429)

    def test_agents_dynamic_auth_rotation_disable_and_migration(self):
        self.login()
        self.assertEqual(self.app.management.authenticate(SEED['existing']),'existing')
        response=self.send('POST','agents',{'agent_id':'new-agent','display_name':'新助手','pending_limit':2})
        self.assertEqual(response.status_code,200,response.text)
        token=response.json()['token']
        self.assertEqual(self.app.management.authenticate(token),'new-agent')
        self.assertEqual(self.send('POST','agents',{'agent_id':'new-agent'}).status_code,409)
        listing=self.client.get('/api/admin/agents').text
        self.assertNotIn(token,listing);self.assertNotIn('token_hash',listing)
        self.assertEqual(self.send('PATCH','agents/new-agent',{'enabled':False}).status_code,200)
        self.assertIsNone(self.app.management.authenticate(token))
        self.app.management.initialize(SEED)  # No reimport/resurrection on restart.
        self.assertIsNone(self.app.management.authenticate(token))
        self.send('PATCH','agents/new-agent',{'enabled':True})
        changed=self.send('POST','agents/new-agent/rotate',{}).json()['token']
        self.assertNotEqual(token,changed);self.assertIsNone(self.app.management.authenticate(token))
        self.assertEqual(self.app.management.authenticate(changed),'new-agent')
        self.app.management.initialize({'existing':'different-ignored-token'*3})
        self.assertEqual(self.app.management.authenticate(SEED['existing']),'existing')

    def test_pauses_and_runtime_limits_snapshot(self):
        self.login()
        first=self.app.queue.submit('existing','result=None',request_id='first')['job_id']
        response=self.send('PUT','settings',{'settings':{'execution_paused':True,'job_timeout':45,'per_agent_limit':1}})
        self.assertEqual(response.status_code,200,response.text)
        self.assertIsNone(self.app.queue.claim())
        with self.assertRaisesRegex(ValueError,'AGENT_QUEUE_FULL'):
            self.app.queue.submit('existing','result=None')
        self.send('PUT','settings',{'settings':{'execution_paused':False}})
        row=self.app.queue.claim();self.assertEqual(row['timeout_applied'],45)
        self.send('PUT','settings',{'settings':{'job_timeout':90,'accepting_jobs':False}})
        self.assertEqual(self.app.queue.read('existing',first)['timeout_applied'],45)
        with self.assertRaisesRegex(ValueError,'SUBMISSIONS_PAUSED'):
            self.app.queue.submit('existing','new=None')
        self.assertTrue(self.app.queue.submit('existing','result=None',request_id='first')['deduplicated'])

    def test_per_agent_override(self):
        self.login();self.send('PATCH','agents/existing',{'pending_limit':1})
        self.app.queue.submit('existing','result=None')
        with self.assertRaisesRegex(ValueError,'AGENT_QUEUE_FULL'):
            self.app.queue.submit('existing','result=None')

    def test_retention_preview_confirmation_and_stale_rejection(self):
        self.login();jobs=[self.success(str(i)) for i in range(3)]
        self.assertEqual(self.send('PUT','settings',{'settings':{'retention_count':1}}).status_code,409)
        preview=self.send('POST','retention/preview',{'retention_count':1}).json()
        self.assertEqual(preview['count'],2)
        self.success('new after preview')
        self.assertEqual(self.send('PUT','settings',{'settings':{'retention_count':1},'confirmation':preview['confirmation']}).status_code,409)
        preview=self.send('POST','retention/preview',{'retention_count':1}).json()
        applied=self.send('PUT','settings',{'settings':{'retention_count':1},'confirmation':preview['confirmation']})
        self.assertEqual(applied.status_code,200,applied.text)
        self.assertTrue(self.app.queue.read('existing',jobs[0])['artifacts_expired'])
        clean_pending(self.app.queue,self.root/'artifacts',self.root/'homes')
        self.assertEqual(len(list((self.root/'artifacts').iterdir())),1)

    def test_manual_delete_confirmation_and_admin_download(self):
        self.login();job=self.success()
        self.assertEqual(self.client.get(f'/api/admin/files/{job}/model.stl').content,b'stl-model')
        self.assertEqual(self.send('POST',f'jobs/{job}/artifacts/delete',{}).status_code,409)
        preview=self.client.get(f'/api/admin/jobs/{job}/artifacts/preview').json()
        response=self.send('POST',f'jobs/{job}/artifacts/delete',{'confirmation':preview['confirmation']})
        self.assertEqual(response.status_code,200,response.text)
        expired=self.client.get(f'/api/admin/files/{job}/model.stl')
        self.assertEqual(expired.status_code,410)
        self.assertIn('管理员',expired.json()['error']['message'])
        clean_pending(self.app.queue,self.root/'artifacts',self.root/'homes')
        self.assertFalse((self.root/'artifacts'/job).exists())

    def test_jobs_overview_stats_and_cancel(self):
        self.login();self.success()
        waiting=self.app.queue.submit('existing','result=None')['job_id']
        detail=self.client.get('/api/admin/jobs/'+waiting).json()
        self.assertEqual(detail['script'],'result=None')
        self.assertEqual(self.send('POST','jobs/'+waiting+'/cancel',{}).json()['status'],'cancelled')
        overview=self.client.get('/api/admin/overview').json()
        self.assertEqual(overview['counts']['succeeded'],1)
        self.assertEqual(overview['counts']['cancelled'],1)
        self.assertGreater(overview['storage']['bytes'],0)
        self.assertEqual(self.client.get('/api/admin/jobs?agent=missing').json()['total'],0)

    def test_settings_validation(self):
        self.login()
        for settings in [{'queue_limit':0},{'job_timeout':999999},{'accepting_jobs':'false'},{'unknown':1}]:
            self.assertEqual(self.send('PUT','settings',{'settings':settings}).status_code,400)

    def test_disabled_agent_keeps_existing_task_and_cannot_access_mcp(self):
        self.login();job=self.app.queue.submit('existing','result=None')['job_id']
        self.send('PATCH','agents/existing',{'enabled':False})
        self.assertEqual(self.client.post('/mcp',headers={'Authorization':'Bearer '+SEED['existing']},json={}).status_code,401)
        self.assertEqual(self.app.queue.read('existing',job)['status'],'queued')

    def test_new_web_agent_connects_through_real_mcp(self):
        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        self.login()
        token=self.send('POST','agents',{'agent_id':'new-mcp'}).json()['token']
        async def connect():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),headers={'Authorization':'Bearer '+token}) as http:
                async with streamable_http_client('http://testserver/mcp',http_client=http) as (read,write,_):
                    async with ClientSession(read,write) as session:
                        await session.initialize()
                        value=await session.call_tool('submit_model',{'script':'result=None','request_id':'web-agent'})
                        self.assertFalse(value.isError)
                        payload=value.structuredContent or json.loads(value.content[0].text)
                        return payload['job_id']
        # Add the ASGI test hostname to the transport allowlist through a new origin.
        self.app.mcp.settings.transport_security.allowed_hosts.extend(['testserver','testserver:*'])
        job=asyncio.run(connect())
        self.assertEqual(self.app.queue.read('new-mcp',job)['agent'],'new-mcp')

    def test_web_settings_survive_reinitialize_and_old_env(self):
        from unittest.mock import patch
        self.login()
        self.send('PUT','settings',{'settings':{'job_timeout':55,'queue_limit':31,'per_agent_limit':2}})
        with patch.dict('os.environ',{'CAD_TIMEOUT':'999'}):
            self.app.queue.initialize()
        self.assertEqual(self.app.queue.settings()['job_timeout'],55)
        self.assertEqual(self.app.queue.settings()['queue_limit'],31)
