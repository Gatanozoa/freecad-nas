import bootstrap
import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from cryptography.fernet import Fernet
from starlette.testclient import TestClient
from mobile.store import Store
from mobile.api import create_app
from mobile.provider import generate
import httpx

class SettingsTests(unittest.TestCase):
    def setUp(self):
        qa=bootstrap.ROOT/'.qa';qa.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=qa)
        self.env=patch.dict(os.environ,{'MASTER_KEY':Fernet.generate_key().decode()});self.env.start()
        self.store=Store(self.temp.name);self.client=TestClient(create_app(self.store))
        self.client.post('/api/login',json={'username':'admin','password':'admin'})
    def tearDown(self):self.client.close();self.env.stop();self.temp.cleanup()
    def save(self,**values):
        return self.client.post('/api/providers',json={'name':'建模','url':' https://provider.test/v1/chat/completions/ ','model':' model-id ','key':' Bearer secret-key ','vision':True,**values})
    def test_full_endpoint_and_key_normalized(self):
        r=self.save();self.assertEqual(r.status_code,200,r.text)
        config=self.store.provider(r.json()['id'],True)
        self.assertEqual(config['url'],'https://provider.test/v1');self.assertEqual(config['key'],'secret-key')
        self.assertNotIn('secret-key',r.text);self.assertEqual(config['model'],'model-id')
    def test_missing_key_explicit_validation(self):
        r=self.save(key='');self.assertEqual(r.status_code,400);self.assertIn('Key',r.json()['error'])
    def test_default_saved_across_store_instances(self):
        first=self.save().json()['id'];second=self.save(name='second').json()['id']
        self.assertEqual(self.client.get('/api/preferences').json()['default_provider'],first)
        self.assertEqual(self.client.post('/api/preferences',json={'default_provider':second}).status_code,200)
        self.assertEqual(Store(self.temp.name).default_provider(),second)
    def test_edit_blank_key_preserves_secret_and_survives_restart(self):
        pid=self.save().json()['id'];self.assertEqual(self.save(id=pid,key='',model='new').status_code,200)
        self.assertEqual(Store(self.temp.name).provider(pid,True)['key'],'secret-key')
    def test_saved_configuration_is_used_by_backend(self):
        pid=self.save().json()['id'];config=Store(self.temp.name).provider(pid,True);requests=[]
        def handler(request):
            requests.append(request)
            return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'script':'result=None','questions':[]})}}]})
        real=httpx.AsyncClient
        with patch('mobile.provider.httpx.AsyncClient',lambda **kwargs:real(transport=httpx.MockTransport(handler),**kwargs)):
            asyncio.run(generate(config,'测试'))
        self.assertEqual(requests[0].headers['authorization'],'Bearer secret-key')
        self.assertEqual(json.loads(requests[0].content)['model'],'model-id')
        self.assertEqual(str(requests[0].url),'https://provider.test/v1/chat/completions')
    def test_unsupported_protocol_error(self):
        response=self.save(url='https://provider.test/v1/responses');self.assertEqual(response.status_code,400)

if __name__=='__main__':unittest.main(verbosity=2)
