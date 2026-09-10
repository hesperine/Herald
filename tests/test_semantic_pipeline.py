import json
import tempfile
import unittest
from datetime import timedelta

import httpx

from herald.ai import OpenAICompatibleProvider
from herald.pipeline import ObservationPipeline
from herald.registry import IpRegistry
from herald.storage import StateStore
from test_dedupe import make_observation
from test_merge import BASE


class SemanticPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_three_posts_split_two_then_one(self):
        from herald.config import PublicSettings
        sizes = []
        def handler(request):
            messages = json.loads(request.content)['messages']
            self.assertEqual(len(messages), 4)
            sources = json.loads(messages[-1]['content'])['sources']
            sizes.append(len(sources))
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({
                'groups': [], 'ignored_observation_ids': [p['observation_id'] for p in sources]})}}]})
        ip = IpRegistry.load_builtin().resolve(PublicSettings(watched_ips=['原神'])).supported[0]
        items = [make_observation(str(i), text='原神品牌联动' + str(i), account='official') for i in range(3)]
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(directory)
            store.initialize()
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                provider = OpenAICompatibleProvider(client=client, base_url='https://example.com', model='test', api_key='test')
                result = await ObservationPipeline().process(store=store, ip=ip, observations=items,
                    now=BASE, provider=provider, suppress_immediate_for={o.id for o in items})
            self.assertEqual(sizes, [2, 1])
            self.assertEqual(result.pending_extractions, 0)

    async def test_irrelevant_post_never_calls_ai(self):
        from herald.config import PublicSettings
        ip = IpRegistry.load_builtin().resolve(PublicSettings(watched_ips=['原神'])).supported[0]
        def handler(request):
            self.fail('ordinary update must be filtered before AI')
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(directory)
            store.initialize()
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                provider = OpenAICompatibleProvider(client=client, base_url='https://example.com', model='test', api_key='test')
                result = await ObservationPipeline().process(store=store, ip=ip,
                    observations=[make_observation('ordinary', text='原神版本维护公告', account='official')],
                    now=BASE, provider=provider)
            self.assertEqual(result.ai_calls, 0)
            self.assertEqual(store.list_pending_extractions(), [])

    async def test_historical_batch_and_failed_merge_remain_retryable(self):
        calls = []
        def handler(request):
            body = json.loads(json.loads(request.content)['messages'][-1]['content'])
            calls.append(body)
            if 'existing_campaign' in body:
                return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({'decision': 'update', 'updates': [{'field_path': 'id', 'value': 'illegal', 'observation_id': 'c', 'quote': '联动'}]})}}]})
            if 'sources' in body:
                answer = {'groups': [{'observation_ids': [p['observation_id'] for p in body['sources']], 'extraction': {'relevant': True, 'campaign_title': '原神品牌联动', 'partner': '品牌'}}]}
            else:
                answer = {'relevant': True, 'campaign_title': '原神品牌联动', 'candidate_campaign_id': body['source']['campaign_candidates'][0]['id']}
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(answer)}}]})
        from herald.config import PublicSettings
        ip = IpRegistry.load_builtin().resolve(PublicSettings(watched_ips=['原神'])).supported[0]
        a = make_observation('a', text='原神品牌联动预告', account='official')
        b = make_observation('b', text='原神品牌联动补充', account='official')
        c = make_observation('c', text='原神品牌联动更新', account='official')
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(directory)
            store.initialize()
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                provider = OpenAICompatibleProvider(client=client, base_url='https://example.com', model='test', api_key='test')
                pipeline = ObservationPipeline()
                await pipeline.process(store=store, ip=ip, observations=[a, b], now=BASE, provider=provider, suppress_immediate_for={'a', 'b'})
                self.assertEqual(len(calls), 1)
                self.assertEqual(len(calls[0]['sources']), 2)
                self.assertEqual(store.load_queue_jobs(BASE.date()), [])
                before = store.list_campaigns()
                result = await pipeline.process(store=store, ip=ip, observations=[c], now=BASE, provider=provider)
                self.assertEqual(result.pending_extractions, 1)
                self.assertEqual(store.list_campaigns(), before)
                self.assertEqual(len(store.list_pending_extractions()), 1)

    async def test_two_round_update_and_repeat_noop(self):
        calls = []
        def handler(request):
            data = json.loads(request.content)
            body = json.loads(data['messages'][-1]['content'])
            calls.append(body)
            if 'existing_campaign' in body:
                answer = {'decision': 'update', 'updates': [{'field_path': 'title', 'value': '原神 × 品牌 正式联动', 'observation_id': 'b', 'quote': '正式联动'}]}
            else:
                candidates = body['source']['campaign_candidates']
                answer = {'relevant': True, 'campaign_title': '原神 × 品牌', 'partner': '品牌', 'candidate_campaign_id': candidates[0]['id'] if candidates else None}
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(answer)}}]})
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(directory)
            store.initialize()
            from herald.config import PublicSettings
            ip = IpRegistry.load_builtin().resolve(PublicSettings(watched_ips=['原神'])).supported[0]
            a = make_observation('a', text='原神品牌联动官宣', account='official')
            b = make_observation('b', text='原神品牌正式联动', account='official')
            b.source.published_at += timedelta(days=1)
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                provider = OpenAICompatibleProvider(client=client, base_url='https://example.com', model='test', api_key='test')
                pipeline = ObservationPipeline()
                await pipeline.process(store=store, ip=ip, observations=[b, a], now=BASE, provider=provider)
                self.assertEqual(len(store.list_campaigns()), 1)
                self.assertEqual(store.list_campaigns()[0].title, '原神 × 品牌 正式联动')
                self.assertEqual(len(calls), 3)
                await pipeline.process(store=store, ip=ip, observations=[b, a], now=BASE, provider=provider)
                self.assertEqual(len(calls), 3)
