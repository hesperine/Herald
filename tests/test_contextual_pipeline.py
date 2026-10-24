import tempfile
import unittest
from datetime import timedelta
from herald.ai import ExtractionResult, MergeResult, AIProviderError
from herald.contextual_ai import RoutingResult, CampaignRoute, CampaignResolution
from herald.pipeline import ObservationPipeline
from herald.registry import RegisteredIp
from herald.storage import StateStore
from tests.test_pipeline import observation, NOW


class Provider:
    contextual = True
    def __init__(self):
        self.calls = 0
        self.fail = False
    async def route_campaigns(self, packets, catalog):
        self.calls += 1
        return RoutingResult(groups=[CampaignRoute(phrase='原神×品牌', partner='品牌',
            campaign_id=catalog[0]['id'] if catalog else None,
            observation_ids=[p.observation_id for p in packets])])
    async def resolve_campaign(self, route, packets, existing):
        self.calls += 1
        if self.fail:
            raise AIProviderError('redacted')
        return CampaignResolution(extraction=ExtractionResult(relevant=True, campaign_title='原神×品牌', partner='品牌',
            candidate_campaign_id=existing.id if existing else None), merge=MergeResult(decision='update') if existing else None)


class ContextualPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_scope_audit_failure_preserves_known_association(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(directory)
            store.initialize()
            item = observation('a', '原神', digest='a'*64)
            pipeline, provider = ObservationPipeline(), Provider()
            ip = RegisteredIp(slug='genshin-impact', name='原神')
            await pipeline.process(store=store, ip=ip, observations=[item], now=NOW, provider=provider)
            from herald.models import PendingExtraction
            store.save_pending_extraction(PendingExtraction(id=pipeline._id('pending-ai', 'a'),
                observation_ids=['a'], ip_slug=ip.slug, queued_at=NOW, reason='audit'))
            provider.fail = True
            # A new plan is required only for this test's audit of the same source.
            for p in (store.root / 'contextual-plans').glob('*.json'):
                p.unlink()
            await pipeline.process(store=store, ip=ip, observations=[item], now=NOW, provider=provider)
            self.assertEqual(store.list_candidates()[0].status, 'structured')

    async def test_new_duplicate_source_does_not_orphan_existing_pending(self):
        from herald.models import PendingExtraction
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(directory)
            store.initialize()
            a = observation('a', '原神', digest='a'*64)
            b = observation('b', '原神', digest='a'*64)
            store.save_observation(a)
            store.save_pending_extraction(PendingExtraction(id=ObservationPipeline._id('pending-ai', 'a'),
                observation_ids=['a'], ip_slug='genshin-impact', queued_at=NOW, reason='pending'))
            await ObservationPipeline().process(store=store, ip=RegisteredIp(slug='genshin-impact', name='原神'),
                observations=[a, b], now=NOW, provider=Provider())
            self.assertEqual(store.list_pending_extractions(), [])

    async def test_model_repeating_identical_activity_does_not_create_new_ids(self):
        from herald.ai import ExtractedActivity
        class Repeating(Provider):
            async def resolve_campaign(self, route, packets, existing):
                activity = ExtractedActivity(title='联动快闪', kind='popup')
                return CampaignResolution(extraction=ExtractionResult(relevant=True, partner='品牌',
                    campaign_title='原神×品牌', activities=[activity], candidate_campaign_id=existing.id if existing else None),
                    merge=MergeResult(decision='update', new_activities=[activity]) if existing else None)
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(directory)
            store.initialize()
            for oid in ('a', 'b'):
                item = observation(oid, '原神', digest=oid*64)
                item.text += oid
                await ObservationPipeline().process(store=store, ip=RegisteredIp(slug='genshin-impact', name='原神'),
                    observations=[item], now=NOW, provider=Repeating())
            self.assertEqual(len(store.list_campaigns()[0].activities), 1)

    async def test_unrelated_legacy_activity_archived_without_inventing_cancellation(self):
        from herald.contextual_ai import ActivityExclusion
        from herald.models import Campaign, Activity
        class Audit(Provider):
            async def resolve_campaign(self, route, packets, existing):
                result = await super().resolve_campaign(route, packets, existing)
                result.exclusions = [ActivityExclusion(activity_id='unrelated', observation_id='audit',
                    quote='剿灭作战追加', reason='普通游戏关卡，不属于品牌联动')]
                return result
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(directory)
            store.initialize()
            old = Campaign(id='old', ip_slug='genshin-impact', ip_name='原神', title='原神×品牌', partner='品牌',
                first_seen_at=NOW, updated_at=NOW, activities=[Activity(id='unrelated', title='剿灭作战', kind='in_game')])
            store.save_campaign(old)
            item = observation('audit', '原神', digest='f'*64)
            item.text += '；剿灭作战追加'
            await ObservationPipeline().process(store=store, ip=RegisteredIp(slug='genshin-impact', name='原神'),
                observations=[item], now=NOW, provider=Audit())
            updated = store.load_campaign('old')
            self.assertEqual(updated.activities, [])
            self.assertEqual(updated.excluded_activities[0].id, 'unrelated')
            self.assertEqual(updated.excluded_activities[0].status.value, 'announced')

    async def test_partial_plan_does_not_resolve_candidate_until_all_groups_finish(self):
        from herald.contextual_ai import UnconfirmedSource
        class Multi(Provider):
            async def route_campaigns(self, packets, catalog):
                return RoutingResult(groups=[CampaignRoute(phrase=p, observation_ids=[packets[0].observation_id]) for p in ['甲', '乙']])
            async def resolve_campaign(self, route, packets, existing):
                if route.phrase == '乙':
                    raise AIProviderError('failed')
                return CampaignResolution(extraction=ExtractionResult(relevant=True, campaign_title=route.phrase))
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(directory)
            store.initialize()
            await ObservationPipeline().process(store=store, ip=RegisteredIp(slug='genshin-impact', name='原神'),
                observations=[observation('a', '原神', digest='a'*64)], now=NOW, provider=Multi())
            self.assertEqual(len(store.list_pending_extractions()), 1)
            self.assertEqual(store.list_candidates()[0].status, 'pending')

    async def test_source_replay_and_followup_preserve_one_campaign(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(directory)
            store.initialize()
            provider, pipeline = Provider(), ObservationPipeline()
            ip = RegisteredIp(slug='genshin-impact', name='原神')
            a = observation('a', '原神', digest='a'*64)
            b = observation('b', '原神', digest='b'*64)
            b.text += '，合作宣传PV'
            await pipeline.process(store=store, ip=ip, observations=[a], now=NOW, provider=provider)
            await pipeline.process(store=store, ip=ip, observations=[a], now=NOW, provider=provider)
            self.assertEqual(provider.calls, 2)
            await pipeline.process(store=store, ip=ip, observations=[b], now=NOW+timedelta(days=1), provider=provider)
            self.assertEqual(len(store.list_campaigns()), 1)
            self.assertEqual(len(store.list_pending_extractions()), 0)
            self.assertTrue(all(c.resolution_version == 2 for c in store.list_candidates()))

    async def test_failure_keeps_pending_and_retry_finishes_saved_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(directory)
            store.initialize()
            provider, pipeline = Provider(), ObservationPipeline()
            provider.fail = True
            ip = RegisteredIp(slug='genshin-impact', name='原神')
            a = observation('a', '原神', digest='a'*64)
            await pipeline.process(store=store, ip=ip, observations=[a], now=NOW, provider=provider)
            self.assertEqual(len(store.list_pending_extractions()), 1)
            self.assertEqual(store.list_campaigns(), [])
            provider.fail = False
            await pipeline.process(store=store, ip=ip, observations=[a], now=NOW+timedelta(days=1), provider=provider)
            self.assertEqual(provider.calls, 3)
            self.assertEqual(len(store.list_campaigns()), 1)
