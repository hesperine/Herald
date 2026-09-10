import json
import unittest

import httpx

from herald.ai import OpenAICompatibleProvider, BatchResult, MergeResult
from test_ai import packet, result
from test_merge import campaign


class AIWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def test_date_only_rules_and_source_summary_contract(self):
        from herald.ai import ExtractionResult
        value = ExtractionResult.model_validate({'relevant': True,
            'source_summaries': {'post-a': '快闪预约安排'},
            'activities': [{'kind': 'popup', 'title': '快闪', 'start_date': '2026-07-04',
                            'rules': '全预约制', 'related_offers': '店铺立减15%'}]})
        self.assertIsNone(value.activities[0].start_at)
        self.assertEqual(str(value.activities[0].start_date), '2026-07-04')
        provider = OpenAICompatibleProvider(client=None, base_url='https://example.com',
            model='test', api_key='secret', thinking='disabled', max_tokens=4096)
        payload = provider._request_payload(packet())
        self.assertEqual(payload['thinking'], {'type': 'disabled'})
        self.assertEqual(payload['max_tokens'], 4096)
        self.assertIn('合作方不等于场地', payload['messages'][0]['content'])
        example = json.loads(payload['messages'][2]['content'])
        self.assertEqual(len(example['activities']), 2)
        self.assertIsNone(example['activities'][0]['start_at'])
        self.assertTrue(example['activities'][1]['actions'][0]['rules'])

    def test_fewshots_are_messages_and_current_input_is_last(self):
        provider = OpenAICompatibleProvider(client=None, base_url='https://example.com', model='test', api_key='secret')
        messages = provider._request_payload(packet())['messages']
        self.assertEqual([m['role'] for m in messages], ['system', 'user', 'assistant', 'user'])
        self.assertEqual(json.loads(messages[-1]['content'])['source']['observation_id'], packet().observation_id)
        self.assertNotIn('source_media', json.dumps(messages))
        self.assertNotIn('sinaimg', json.dumps(messages))
        self.assert_field_descriptions(json.loads(messages[-1]['content'])['output_schema'])

    def assert_field_descriptions(self, schema):
        definitions = schema['$defs']
        action = definitions['ExtractedAction']['properties']
        self.assertIn('未说明填 null', action['requires_reservation']['description'])
        self.assertIn('版本号', definitions['ExtractedActivity']['properties']['end_at']['description'])
        if schema['title'] != 'MergeResult':
            self.assertIn('activities[0]', definitions['ExtractedClaim']['properties']['field_path']['description'])
        self.assertIn('lottery_open', action['kind']['description'])
        self.assertEqual(action['requires_reservation']['default'], None)

    async def test_batch_and_merge_use_their_own_schemas(self):
        bodies = []
        responses = [
            {'groups': [{'observation_ids': [packet().observation_id], 'extraction': result().model_dump(mode='json')}], 'ignored_observation_ids': []},
            {'decision': 'update', 'updates': [], 'new_activities': []},
        ]
        def handler(request):
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(responses.pop(0))}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAICompatibleProvider(client=client, base_url='https://example.com', model='test', api_key='secret')
            batch = await provider.extract_batch([packet()], [])
            merged = await provider.merge_campaign(campaign('c'), [packet()], result())
        self.assertIsInstance(batch, BatchResult)
        self.assertIsInstance(merged, MergeResult)
        merge_example = json.loads(bodies[1]['messages'][2]['content'])
        self.assertTrue(merge_example['updates'])
        self.assertEqual(len(merge_example['new_activities']), 1)
        self.assertEqual(json.loads(bodies[0]['messages'][-1]['content'])['output_schema']['title'], 'BatchResult')
        self.assertEqual(json.loads(bodies[1]['messages'][-1]['content'])['output_schema']['title'], 'MergeResult')
        self.assertNotIn('poster.jpg', json.dumps(bodies))
        self.assertNotIn('secret', json.dumps(bodies))
        for body in bodies:
            self.assert_field_descriptions(json.loads(body['messages'][-1]['content'])['output_schema'])

    async def test_batch_rejects_missing_or_invented_post_ids(self):
        def handler(request):
            return httpx.Response(200, json={'choices': [{'message': {'content': '{"groups": [], "ignored_observation_ids": []}'}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAICompatibleProvider(client=client, base_url='https://example.com', model='test', api_key='secret')
            from herald.ai import AIProviderError
            with self.assertRaises(AIProviderError):
                await provider.extract_batch([packet()], [])
