import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('time_schema_experiment',Path(__file__).resolve().parents[1]/'scripts/time-schema-experiment.py')
lab=importlib.util.module_from_spec(spec);spec.loader.exec_module(lab)


class TimeSchemaTests(unittest.TestCase):
    def test_date_only_stays_date_only(self):
        value={'activities':[{'kind':'merchandise','title':'预售','start':{'date':'2026-09-08','time':None,'timezone':'Asia/Shanghai'},'end':None,'actions':[]}]}
        actual=lab.from_nested(value)
        self.assertEqual(actual['activities'][0]['start_date'],'2026-09-08')
        self.assertIsNone(actual['activities'][0]['start_at'])

    def test_time_and_evidence_survive_conversion(self):
        value={'activities':[{'kind':'merchandise','title':'预售','actions':[{'kind':'gift','title':'满赠','end':{'date':'2026-09-21','time':'23:59','timezone':'Asia/Shanghai'}}]}],
               'claims':[{'field_path':'activities[0].actions[0].end.time','quote':'9月21日23:59','confidence':1}]}
        actual=lab.from_nested(value)
        self.assertEqual(actual['activities'][0]['actions'][0]['end_at'],'2026-09-21T23:59:00+08:00')
        self.assertEqual(actual['claims'][0]['field_path'],'activities[0].actions[0].end_at')

    def test_invalid_time_and_legacy_fields_are_rejected(self):
        for end in [{'date':'2026-09-21','time':'25:00','timezone':'Asia/Shanghai'}, {'date':None,'time':'12:00','timezone':'Asia/Shanghai'}]:
            with self.assertRaises(ValueError):lab.from_nested({'kind':'gift','title':'x','end':end})
        with self.assertRaises(ValueError):lab.from_nested({'kind':'gift','title':'x','at':'2026-09-21'})

    def test_schema_and_example_are_consistent(self):
        from herald.ai import ExtractionResult
        schema=lab.convert_schema(ExtractionResult.model_json_schema())
        for name in ['ExtractedAction','ExtractedActivity']:
            props=schema['$defs'][name]['properties']
            self.assertIn('start',props);self.assertNotIn('start_date',props);self.assertNotIn('at',props)
        x=lab.to_nested({'activities':[{'kind':'merchandise','title':'x','start_at':None,'start_date':'2026-09-08'}]})
        self.assertEqual(x['activities'][0]['start']['date'],'2026-09-08')

    def test_batch_claim_paths_are_resolved_per_group(self):
        def group(clock):
            return {'observation_ids':['x'], 'extraction':{'activities':[{'kind':'merchandise','title':'x',
                'start':{'date':'2026-09-08','time':clock,'timezone':'Asia/Shanghai'}}],
                'claims':[{'field_path':'activities[0].start','quote':'时间','confidence':1}]}}
        actual=lab.from_nested({'groups':[group(None),group('12:00')]})
        self.assertEqual(actual['groups'][0]['extraction']['claims'][0]['field_path'],'activities[0].start_date')
        self.assertEqual(actual['groups'][1]['extraction']['claims'][0]['field_path'],'activities[0].start_at')


class TimeSchemaProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_wire_schema_is_nested_but_result_is_canonical(self):
        import httpx,json
        from herald.ai import OpenAICompatibleProvider,BatchResult
        bodies=[]
        def handler(request):
            bodies.append(json.loads(request.content))
            answer={'groups':[{'observation_ids':['p'],'extraction':{'relevant':True,'activities':[
                {'kind':'merchandise','title':'x','actions':[{'kind':'gift','title':'赠品','start':{'date':'2026-09-08','time':'12:00','timezone':'Asia/Shanghai'},'end':None}]}]}}], 'ignored_observation_ids':[]}
            return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(answer)}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider=OpenAICompatibleProvider(client=client,base_url='https://example.com',model='test',api_key='test')
            lab.install(provider)
            actual=await provider._complete({'messages':[{'role':'system','content':'test'},{'role':'user','content':json.dumps({'output_schema':BatchResult.model_json_schema()})}]},BatchResult)
        props=json.loads(bodies[0]['messages'][-1]['content'])['output_schema']['$defs']['ExtractedAction']['properties']
        self.assertIn('start',props);self.assertNotIn('at',props)
        definitions=json.loads(bodies[0]['messages'][-1]['content'])['output_schema']['$defs']
        descriptions=[]
        import re
        for name in ('ExtractedActivity','ExtractedAction'):
            for side in ('start','end'):
                endpoint=definitions[name]['properties'][side]
                descriptions.append(endpoint['description'])
                clock=endpoint['anyOf'][0]['properties']['time']
                self.assertIn('必须保留',clock['description'])
                self.assertRegex('12:00',clock['anyOf'][0]['pattern'])
                self.assertIsNone(re.fullmatch(clock['anyOf'][0]['pattern'],'25:00'))
        self.assertEqual(len(set(descriptions)),4)
        self.assertIn('渠道',props['end']['description'])
        self.assertIn('不能',definitions['ExtractedActivity']['properties']['start']['description'])
        self.assertEqual(actual.groups[0].extraction.activities[0].actions[0].at.isoformat(),'2026-09-08T12:00:00+08:00')
