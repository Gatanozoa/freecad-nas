import bootstrap
import asyncio
import json
import unittest
from unittest.mock import patch
import httpx
from mobile.provider import generate

class ProviderTests(unittest.TestCase):
    def test_full_image_request_and_usage(self):
        captured=[]
        async def handler(request):
            captured.append(request)
            return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'script':'result=None','questions':[],'assumptions':[],'dimensions':[],'summary':'test'})}}],'usage':{'total_tokens':100}})
        real=httpx.AsyncClient
        def client(**kwargs):return real(transport=httpx.MockTransport(handler),**kwargs)
        with patch('mobile.provider.httpx.AsyncClient',client):
            plan,usage=asyncio.run(generate({'url':'https://provider.test/v1','key':'secret','model':'custom'},'孔径改8',['data:image/png;base64,test'],'original-script'))
        body=json.loads(captured[0].content)
        self.assertEqual(str(captured[0].url),'https://provider.test/v1/chat/completions')
        self.assertEqual(body['messages'][-1]['content'][1]['type'],'image_url')
        self.assertIn('original-script',body['messages'][1]['content']);self.assertEqual(usage['total_tokens'],100)
    def test_provider_error_hides_response_body(self):
        real=httpx.AsyncClient
        def client(**kwargs):return real(transport=httpx.MockTransport(lambda r:httpx.Response(401,text='secret')),**kwargs)
        with patch('mobile.provider.httpx.AsyncClient',client):
            with self.assertRaisesRegex(ValueError,'HTTP 401') as caught:
                asyncio.run(generate({'url':'https://provider.test/v1','key':'secret','model':'custom'},'test'))
        self.assertNotIn('secret',str(caught.exception))

if __name__=='__main__':unittest.main(verbosity=2)
