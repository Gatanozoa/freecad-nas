import bootstrap
import asyncio
import copy
import json
import unittest
from unittest.mock import patch,AsyncMock
import httpx
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

    def test_wrist_rest_advisories_after_model_correction_allow_one_confirmation(self):
        plan = self.measurement_plan(worker.DEMO)
        plan['measurement']['parameters'] = [
            {'name': name, 'value': value, 'source': source, 'required': required}
            for name, value, source, required in [
                ('手托总长', '320 mm', 'estimate', True),
                ('手托深度', '80 mm', 'estimate', True),
                ('靠键盘侧顶面高', '18 mm', 'user', True),
                ('靠人体侧顶面高', '14 mm', 'user', True),
                ('防滑垫槽偏移', '25 / 20 mm', 'estimate', False),
                ('可选装饰', '', 'unknown', False),
            ]
        ]
        advice = ['装配前建议用实物键盘复核外形尺寸', '顶面采用直线平面斜面', '防滑垫槽位置 25/20 mm 为建模假设']
        plan['measurement']['issues'] = [{'kind': 'advisory', 'message': note} for note in advice]
        plan['assumptions'] = [advice[0]]
        plan['questions'] = [advice[1]]
        for field in ('questions', 'issues'):
            with self.subTest(field=field):
                invalid = copy.deepcopy(plan)
                invalid['script'] = ''
                invalid['assumptions'] = []
                invalid['questions'] = advice if field == 'questions' else []
                invalid['measurement']['issues'] = advice if field == 'issues' else []
                responses = iter([invalid, plan])
                requests = []

                def respond(request):
                    requests.append(json.loads(request.content))
                    return httpx.Response(200, json={
                        'choices': [{'message': {'content': json.dumps(next(responses))}}],
                        'usage': {'total_tokens': 10},
                    })

                real_client = httpx.AsyncClient

                def client(**kwargs):
                    return real_client(transport=httpx.MockTransport(respond), **kwargs)

                job = self.job(prompt='我接受估算，手托长320 mm，深80 mm，斜面两侧高18 mm和14 mm，其余细节沿用建议值。',
                               provider=self.provider(), demo=False)
                with patch('mobile.provider.httpx.AsyncClient', client), patch('mobile.worker.execute', AsyncMock()) as execute:
                    asyncio.run(worker.process(self.store, job['id']))
                    execute.assert_not_awaited()
                saved = self.store.job(job['id'])
                self.assertEqual(len(requests), 2)
                self.assertIn('校验未通过', requests[1]['messages'][-1]['content'])
                self.assertEqual(saved['status'], 'needs_confirmation')
                self.assertEqual(saved['plan']['measurement']['issues'], [])
                self.assertEqual(saved['plan']['questions'], [])
                self.assertEqual(saved['plan']['assumptions'], advice)
                self.assertEqual(saved['plan']['measurement']['parameters'][0]['source'], 'estimate')
                self.assertEqual(self.client.post(f"/api/jobs/{job['id']}/confirm-dimensions", json={}).status_code, 200)
                with patch('mobile.worker.generate', AsyncMock()) as generate, \
                     patch('mobile.worker.execute', AsyncMock(return_value={'ok': True})) as execute, \
                     patch('mobile.worker.archive', AsyncMock()):
                    asyncio.run(worker.process(self.store, job['id']))
                    generate.assert_not_awaited()
                    execute.assert_awaited_once()

    def test_advice_does_not_override_a_structured_blocker(self):
        for kind, reason in [('conflict', '图纸标注 5 mm，与文字要求 8 mm 冲突'),
                             ('ambiguity', '安装槽的深度方向无法确定'),
                             ('unsupported', '所需输入的有机曲面超出服务能力')]:
            with self.subTest(kind=kind):
                plan = self.measurement_plan(worker.DEMO)
                plan['measurement']['issues'] = [
                    {'kind': 'advisory', 'message': '建议实物复核尺寸'},
                    {'kind': kind, 'message': reason},
                ]
                row = self.run_plan(plan)
                self.assertEqual(row['status'], 'needs_input')
                self.assertEqual(row['plan']['measurement']['issues'], [reason])
                self.assertEqual(row['plan']['assumptions'], ['建议实物复核尺寸'])
                self.assertEqual(row['plan']['questions'], [reason])
                self.assertEqual(self.client.post(f"/api/jobs/{row['id']}/confirm-dimensions", json={}).status_code, 400)

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
