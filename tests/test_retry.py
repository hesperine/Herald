import tempfile
import unittest
from datetime import timedelta

import httpx

from herald.ai import AIProviderError, OpenAICompatibleProvider
from herald.retry import BudgetedProvider
from herald.storage import StateStore
from test_ai import NOW, packet
from test_pipeline import CountingProvider, extraction, observation
from herald.pipeline import ObservationPipeline
from herald.registry import RegisteredIp


class RetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_rate_limit_is_redacted_and_honors_retry_after(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(429, headers={'Retry-After': '43200'},
                                  json={'error': {'message': 'private-response'}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAICompatibleProvider(client=client, base_url='https://example.com',
                                                model='fixture', api_key='fixture')
            with self.assertRaises(AIProviderError) as caught:
                await provider.extract(packet())
        self.assertEqual(len(calls), 1)
        self.assertEqual(caught.exception.category, 'rate_limit')
        self.assertEqual(caught.exception.retry_after_seconds, 43200)
        self.assertNotIn('private-response', str(caught.exception))

    async def test_budget_preserves_work_and_later_run_completes(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(directory)
            store.initialize()
            raw = CountingProvider(extraction())
            provider = BudgetedProvider(raw, store, NOW, max_calls=0)
            ip = RegisteredIp(slug='genshin-impact', name='原神')
            item = observation('budget', 'official', digest='budget')
            pipeline = ObservationPipeline()
            await pipeline.process(store=store, ip=ip, observations=[item], now=NOW, provider=provider)
            self.assertEqual(raw.calls, 0)
            self.assertEqual(len(store.list_pending_extractions()), 1)
            await pipeline.process(store=store, ip=ip, observations=[item], now=NOW,
                                   provider=BudgetedProvider(raw, store, NOW))
            self.assertEqual(raw.calls, 1)
            self.assertEqual(store.list_pending_extractions(), [])

    async def test_shared_cooldown_survives_new_run(self):
        class Limited:
            calls = 0
            async def extract(self, value):
                self.calls += 1
                raise AIProviderError('redacted', category='quota', retry_after_seconds=86400)
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(directory)
            raw = Limited()
            for now in (NOW, NOW + timedelta(hours=6)):
                provider = BudgetedProvider(raw, store, now)
                with self.assertRaises(AIProviderError):
                    await provider.extract(packet())
            self.assertEqual(raw.calls, 1)
            with self.assertRaises(AIProviderError):
                await BudgetedProvider(raw, store, NOW + timedelta(days=1)).extract(packet())
            self.assertEqual(raw.calls, 2)
