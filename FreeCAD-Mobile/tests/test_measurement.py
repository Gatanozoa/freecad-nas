import bootstrap
import asyncio
import copy
import json
import unittest
from unittest.mock import patch,AsyncMock
import test_mobile
from mobile import worker
from mobile.measurement import review,approval_digest

class MeasurementTests(unittest.TestCase):
    setUp=test_mobile.MobileTests.setUp
    tearDown=test_mobile.MobileTests.tearDown
    login=test_mobile.MobileTests.login
    job=test_mobile.MobileTests.job
    provider=test_mobile.MobileTests.provider
    measurement_plan=test_mobile.MobileTests.measurement_plan

    def run_plan(self,plan,parent=None):
        job=self.job(provider=self.provider(),demo=False,parent=parent)
        with patch('mobile.worker.generate',AsyncMock(return_value=(copy.deepcopy(plan),{'total_tokens':10}))),patch('mobile.worker.cad_call',AsyncMock()) as cad:
            asyncio.run(worker.process(self.store,job['id']))
            cad.assert_not_awaited()
        return self.store.job(job['id'])

    def test_missing_measurements_never_submit_despite_script(self):
        plan=self.measurement_plan(worker.DEMO)
        plan['measurement']['parameters'].append({'name':'板厚','value':'','source':'unknown','required':True,'question':'板厚是多少？','measurement_hint':'用卡尺夹住上下平面'})
        row=self.run_plan(plan)
        self.assertEqual(row['status'],'needs_input');self.assertEqual(row['script'],'')
        self.assertIn('板厚是多少？',row['plan']['questions'])
        self.assertIn('卡尺',row['plan']['measurement']['parameters'][1]['measurement_hint'])
        self.assertEqual(self.client.post('/api/jobs/'+row['id']+'/confirm-dimensions',json={}).status_code,400)

    def test_missing_assessment_and_conflicting_dimensions_block(self):
        for plan in [{'script':worker.DEMO,'questions':[]},self.measurement_plan(worker.DEMO)]:
            if 'measurement' in plan:plan['measurement']['issues']=['图纸板厚5mm与文字8mm冲突，请确认']
            self.assertEqual(self.run_plan(plan)['status'],'needs_input')

    def test_estimates_require_explicit_confirmation_and_resume_without_llm(self):
        plan=self.measurement_plan(worker.DEMO);plan['measurement']['parameters'][0]['source']='estimate'
        row=self.run_plan(plan);self.assertEqual(row['status'],'needs_confirmation')
        self.assertEqual(row['plan']['measurement']['parameters'][0]['source'],'estimate')
        path='/api/jobs/'+row['id']+'/confirm-dimensions'
        self.assertEqual(self.client.post(path,json={'measurement_approval':'fake'}).status_code,200)
        self.assertEqual(self.client.post(path,json={}).status_code,200)
        self.assertEqual(self.store.job(row['id'])['stage'],'dimensions_confirmed')
        with patch('mobile.worker.generate',AsyncMock()) as gen,patch('mobile.worker.execute',AsyncMock(return_value={'ok':True})) as execute,patch('mobile.worker.archive',AsyncMock()):
            asyncio.run(worker.process(self.store,row['id']))
            gen.assert_not_awaited();execute.assert_awaited_once()

    def test_confirmation_survives_store_reopen(self):
        from mobile.store import Store
        row=self.run_plan(self.measurement_plan(worker.DEMO))
        self.client.post('/api/jobs/'+row['id']+'/confirm-dimensions',json={})
        reopened=Store(self.temp.name)
        with patch('mobile.worker.generate',AsyncMock()) as gen,patch('mobile.worker.execute',AsyncMock(return_value={'ok':True})) as execute,patch('mobile.worker.archive',AsyncMock()):
            asyncio.run(worker.process(reopened,row['id']))
            gen.assert_not_awaited();execute.assert_awaited_once()

    def test_repair_cannot_change_confirmed_dimensions_silently(self):
        row=self.run_plan(self.measurement_plan(worker.DEMO))
        self.client.post('/api/jobs/'+row['id']+'/confirm-dimensions',json={})
        changed=self.measurement_plan(worker.DEMO)
        changed['measurement']['parameters'][0]['value']='80 × 30 × 5 mm'
        with patch('mobile.worker.execute',AsyncMock(side_effect=ValueError('invalid solid'))) as execute,patch('mobile.worker.generate',AsyncMock(return_value=(changed,None))):
            asyncio.run(worker.process(self.store,row['id']))
            execute.assert_awaited_once()
        self.assertEqual(self.store.job(row['id'])['status'],'needs_confirmation')
        self.assertEqual(self.store.job(row['id'])['plan']['measurement']['parameters'][0]['value'],'80 × 30 × 5 mm')

    def test_model_cannot_self_approve(self):
        plan=self.measurement_plan(worker.DEMO);plan['measurement_approval']=approval_digest(plan,plan['script'])
        self.assertEqual(self.run_plan(plan)['status'],'needs_confirmation')

    def test_stale_card_and_cancel(self):
        first=self.run_plan(self.measurement_plan(worker.DEMO))
        second=self.run_plan(self.measurement_plan(worker.DEMO),parent=first['id'])
        self.assertEqual(self.client.post('/api/jobs/'+first['id']+'/confirm-dimensions',json={}).status_code,400)
        self.assertEqual(self.client.post('/api/jobs/'+second['id']+'/cancel',json={}).json()['status'],'cancelled')

    def test_history_contains_previous_questions_and_measurements(self):
        plan=self.measurement_plan('');plan['questions']=['请测量孔径'];plan['measurement']['parameters'].append({'name':'孔径','value':'','source':'unknown'})
        parent=self.run_plan(plan)
        child=self.job(provider=self.provider(),demo=False,parent=parent['id'])
        gen=AsyncMock(return_value=(plan,None))
        with patch('mobile.worker.generate',gen):asyncio.run(worker.process(self.store,child['id']))
        context=gen.call_args.args[1]
        self.assertIn('请测量孔径',context);self.assertIn('60 × 30 × 5 mm',context)

    def test_edited_approved_script_cannot_reuse_approval(self):
        row=self.run_plan(self.measurement_plan(worker.DEMO))
        self.client.post('/api/jobs/'+row['id']+'/confirm-dimensions',json={})
        self.store.update(row['id'],script='result=None')
        with patch('mobile.worker.generate',AsyncMock(return_value=(self.measurement_plan(worker.DEMO),None))),patch('mobile.worker.execute',AsyncMock()) as execute:
            asyncio.run(worker.process(self.store,row['id']));execute.assert_not_awaited()
        self.assertEqual(self.store.job(row['id'])['status'],'needs_confirmation')

if __name__=='__main__':unittest.main()
