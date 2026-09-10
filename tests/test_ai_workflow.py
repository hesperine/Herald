import json
import unittest

import httpx

from herald.ai import OpenAICompatibleProvider, BatchResult, MergeResult
from test_ai import packet, result
from test_merge import campaign


class AIWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def test_fewshots_are_messages_and_current_input_is_last(self):
        provider = OpenAICompatibleProvider(client=None, base_url='https://example.com', model='test', api_key='secret')
        messages = provider._request_payload(packet())['messages']
        self.assertEqual([m['role'] for m in messages], ['system', 'user', 'assistant', 'user'])
        self.assertEqual(json.loads(messages[-1]['content'])['source']['observation_id'], packet().observation_id)
        self.assertNotIn('source_media', json.dumps(messages))
        self.assertNotIn('sinaimg', json.dumps(messages))

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
        self.assertEqual(json.loads(bodies[0]['messages'][-1]['content'])['output_schema']['title'], 'BatchResult')
        self.assertEqual(json.loads(bodies[1]['messages'][-1]['content'])['output_schema']['title'], 'MergeResult')
        self.assertNotIn('poster.jpg', json.dumps(bodies))
        self.assertNotIn('secret', json.dumps(bodies))

    async def test_batch_rejects_missing_or_invented_post_ids(self):
        def handler(request):
            return httpx.Response(200, json={'choices': [{'message': {'content': '{"groups": [], "ignored_observation_ids": []}'}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAICompatibleProvider(client=client, base_url='https://example.com', model='test', api_key='secret')
            from herald.ai import AIProviderError
            with self.assertRaises(AIProviderError):
                await provider.extract_batch([packet()], [])
