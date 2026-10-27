import tempfile
import unittest
import json
from datetime import timedelta

import httpx

from herald.ai import AIProviderError, OpenAICompatibleProvider
from herald.retry import BudgetedProvider, record_failure
from herald.models import PendingExtraction
from herald.storage import StateStore
from test_ai import NOW, packet
from test_pipeline import CountingProvider, extraction, observation
from herald.pipeline import ObservationPipeline
from herald.registry import RegisteredIp


class RetryTests(unittest.IsolatedAsyncioTestCase):
    def test_ordinary_failures_retry_next_run_without_exponential_delay(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(directory)
            for category in ('transient', 'validation'):
                pending = PendingExtraction(id='pending-test', observation_ids=['a'],
                    ip_slug='genshin-impact', queued_at=NOW, reason='pending', attempts=8)
                record_failure(store, pending, NOW, AIProviderError('PRIVATE-ERROR', category=category))
                saved = store.load_pending_extraction(pending.id)
                self.assertEqual(saved.attempts, 9)
                self.assertIsNone(saved.next_retry_at)
                self.assertFalse(saved.retry_after_explicit)
                self.assertNotIn('PRIVATE-ERROR', saved.model_dump_json())

    async def test_legacy_ordinary_future_timers_do_not_skip_any_pipeline(self):
        from test_contextual_pipeline import Provider
        from herald.ai import ExtractionResult
        class Semantic(CountingProvider):
            async def extract_batch(self, *args):
                raise AssertionError('incremental source should not use a batch')
        factories = (lambda: CountingProvider(ExtractionResult(relevant=False)),
                     lambda: Semantic(ExtractionResult(relevant=False)), Provider)
        for factory, category in ((factory, category) for factory in factories
                                  for category in ('transient', 'validation')):
            raw = factory()
            with self.subTest(provider=type(raw).__name__, category=category), tempfile.TemporaryDirectory() as directory:
                store = StateStore(directory)
                store.initialize()
                item = observation('legacy-delay', 'official', digest='legacy-delay')
                store.save_observation(item)
                pending = PendingExtraction(id=ObservationPipeline._id('pending-ai', item.id),
                    observation_ids=[item.id], ip_slug='genshin-impact', queued_at=NOW,
                    reason='old retry', attempts=3, error_category=category,
                    next_retry_at=NOW + timedelta(hours=12))
                store.save_pending_extraction(pending)
                await ObservationPipeline().process(store=store,
                    ip=RegisteredIp(slug='genshin-impact', name='原神'), observations=[item],
                    now=NOW + timedelta(hours=2), provider=BudgetedProvider(raw, store, NOW + timedelta(hours=2)))
                self.assertGreater(raw.calls, 0)
                self.assertEqual(store.list_pending_extractions(), [])

    async def test_service_unavailable_retry_after_is_respected_without_immediate_retries(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(503, headers={'Retry-After': '7200'}, text='PRIVATE-BODY')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            raw = OpenAICompatibleProvider(client=client, base_url='https://example.test',
                model='fixture', api_key='fixture')
            with tempfile.TemporaryDirectory() as directory:
                store = StateStore(directory)
                for hours in (0, 1, 2):
                    with self.assertRaises(AIProviderError) as caught:
                        await BudgetedProvider(raw, store, NOW + timedelta(hours=hours)).extract(packet())
                self.assertEqual(caught.exception.retry_after_seconds, 7200)
                self.assertEqual(len(calls), 2)

    async def test_no_header_rate_limit_only_cools_for_five_minutes(self):
        class Limited:
            calls = 0
            async def extract(self, value):
                self.calls += 1
                raise AIProviderError('redacted', category='rate_limit')
        with tempfile.TemporaryDirectory() as directory:
            store, raw = StateStore(directory), Limited()
            for elapsed in (0, 60, 300):
                with self.assertRaises(AIProviderError):
                    await BudgetedProvider(raw, store, NOW + timedelta(seconds=elapsed)).extract(packet())
            self.assertEqual(raw.calls, 2)
            pending = PendingExtraction(id='pending-test', observation_ids=['a'],
                ip_slug='genshin-impact', queued_at=NOW, reason='pending')
            record_failure(store, pending, NOW, AIProviderError('redacted', category='rate_limit'))
            self.assertEqual(store.load_pending_extraction(pending.id).next_retry_at,
                             NOW + timedelta(minutes=5))

    async def test_explicit_retry_after_blocks_ordinary_pending_until_due(self):
        raw = CountingProvider(extraction())
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(directory)
            store.initialize()
            item = observation('explicit', 'official', digest='explicit')
            store.save_observation(item)
            pending = PendingExtraction(id=ObservationPipeline._id('pending-ai', item.id),
                observation_ids=[item.id], ip_slug='genshin-impact', queued_at=NOW, reason='pending')
            record_failure(store, pending, NOW, AIProviderError('redacted', retry_after_seconds=14400))
            for hours in (2, 4):
                await ObservationPipeline().process(store=store,
                    ip=RegisteredIp(slug='genshin-impact', name='原神'), observations=[item],
                    now=NOW + timedelta(hours=hours), provider=raw)
                self.assertEqual(raw.calls, int(hours == 4))

    async def test_safe_diagnostics_survive_process_and_pending_storage(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200,
                json={'choices': [{'finish_reason': 'length', 'message': {'content': 'PRIVATE-BODY'}}],
                      'usage': {'prompt_tokens': 10, 'PRIVATE-META': 'PRIVATE-BODY'}}))) as client:
            with tempfile.TemporaryDirectory() as directory:
                store = StateStore(directory)
                raw = OpenAICompatibleProvider(client=client, base_url='https://private.test',
                    model='fixture', api_key='PRIVATE-KEY', max_attempts=1)
                provider = BudgetedProvider(raw, store, NOW)
                with self.assertRaises(AIProviderError) as caught:
                    await provider.extract(packet())
                pending = PendingExtraction(id='pending-test', observation_ids=['a'],
                    ip_slug='genshin-impact', queued_at=NOW, reason='pending')
                record_failure(store, pending, NOW, caught.exception)
                saved = StateStore(directory).load_pending_extraction(pending.id)
                self.assertEqual(saved.diagnostics[-1]['code'], 'incomplete_response')
                report = json.loads((store.root / 'ai-diagnostics.json').read_text(encoding='utf8'))
                self.assertEqual(report['entries'][-1]['finish_reason'], 'length')
                self.assertEqual(report['entries'][-1]['operation'], 'extract')
                for path in store.root.rglob('*.json'):
                    text = path.read_text(encoding='utf8')
                    for private in ('PRIVATE-KEY', 'PRIVATE-BODY', 'PRIVATE-META', 'private.test'):
                        self.assertNotIn(private, text)

    async def test_budget_deferral_is_diagnosed_without_counting_as_an_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(directory)
            raw = CountingProvider(extraction())
            with self.assertRaises(AIProviderError) as caught:
                await BudgetedProvider(raw, store, NOW, max_calls=0).extract(packet())
            self.assertEqual(caught.exception.diagnostics[-1]['code'], 'call_budget_exhausted')
            pending = PendingExtraction(id='pending-test', observation_ids=['a'],
                ip_slug='genshin-impact', queued_at=NOW, reason='pending')
            store.save_pending_extraction(pending)
            record_failure(store, pending, NOW, caught.exception)
            self.assertEqual(store.load_pending_extraction(pending.id).attempts, 0)

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
