import bootstrap
import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import patch,AsyncMock
from cryptography.fernet import Fernet
from mobile.store import Store
from mobile import worker

class WorkerHealthTests(unittest.TestCase):
    def setUp(self):
        qa=bootstrap.ROOT/'.qa';qa.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=qa)
        self.env=patch.dict(os.environ,{'MASTER_KEY':Fernet.generate_key().decode()});self.env.start()
        self.store=Store(self.temp.name);self.job=self.store.submit('test',None,[],demo=True)
    def tearDown(self):self.env.stop();self.temp.cleanup()
    def test_offline_queue_recovers_same_remote_without_resubmit(self):
        reports=[{'job_id':'remote-1'},{'status':'queued','queue_position':1},{'status':'succeeded','result':{'dimensions':[60,30,5]}}]
        calls=AsyncMock(side_effect=reports)
        health=AsyncMock(side_effect=[{'ready':False,'message':'心跳过期'},{'ready':True,'message':'在线'}])
        stages=[];update=self.store.update
        def record(*args,**kwargs):stages.append(kwargs.get('stage'));return update(*args,**kwargs)
        with patch('mobile.worker.bounded_call',calls),patch('mobile.worker.cad_health',health),patch('mobile.worker.asyncio.sleep',AsyncMock()),patch.object(self.store,'update',record):
            result=asyncio.run(worker.execute(self.store,self.job,worker.DEMO,0))
        self.assertIn('cad_unavailable',stages);self.assertEqual(result['dimensions'],[60,30,5])
        self.assertEqual([c.args[0] for c in calls.call_args_list].count('submit_model'),1)
    def test_query_disconnect_retries_instead_of_generating_again(self):
        calls=AsyncMock(side_effect=[{'job_id':'remote-1'},ConnectionError('offline'),{'status':'succeeded','result':{'ok':True}}])
        with patch('mobile.worker.bounded_call',calls),patch('mobile.worker.cad_health',AsyncMock(return_value={'ready':True})),patch('mobile.worker.asyncio.sleep',AsyncMock()):
            result=asyncio.run(worker.execute(self.store,self.job,worker.DEMO,0))
        self.assertTrue(result['ok']);self.assertEqual(len(calls.call_args_list),3)
    def test_cancel_not_falsely_complete_when_gateway_down(self):
        self.store.update(self.job,remote='remote-1',cancel=1)
        with patch('mobile.worker.bounded_call',AsyncMock(side_effect=ConnectionError('offline'))):
            asyncio.run(worker.process(self.store,self.job))
        self.assertEqual(self.store.job(self.job)['status'],'cancelling')
    def test_migration_preserves_script(self):
        self.store.update(self.job,script=worker.DEMO)
        again=Store(self.temp.name)
        self.assertEqual(again.job(self.job)['script'],worker.DEMO)
        self.assertIsNone(again.job(self.job)['cad_health'])

if __name__=='__main__':unittest.main(verbosity=2)
