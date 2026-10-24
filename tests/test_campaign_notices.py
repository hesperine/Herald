import tempfile
import unittest
from datetime import timedelta
from herald.candidate_notices import register_candidates, resolve_candidates
from herald.campaign_notices import prepare_baselines
from herald.models import NotificationReceipt
from herald.notifications import NotificationService
from herald.pipeline import build_extraction_input
from herald.assembly import CampaignAssembler
from herald.registry import RegisteredIp
from herald.storage import StateStore
from tests.test_pipeline import NOW, observation, extraction
from tests.test_notifications import MemorySender


class CampaignNoticeTests(unittest.TestCase):
    def test_summary_does_not_expose_internal_status_or_repeat_entity_heading(self):
        from herald.campaign_notices import facts, describe
        text = describe(self.c, facts(self.c))
        self.assertNotIn('details_pending', text)
        self.assertNotIn('announced', text)
        self.assertNotIn('· 活动：', text)

    def test_migration_keeps_sent_history_and_queues_scope_audit_once(self):
        from herald.campaign_notices import migrate_contextual
        self.store.save_observation(self.a)
        register_candidates(self.store, self.ip, [self.a], NOW)
        resolve_candidates(self.store, NOW)
        NotificationService().deliver_due(store=self.store, day=NOW.date(), generated_at=NOW,
            sender=MemorySender(), recipient='test@example.com')
        migrate_contextual(self.store, [self.ip], NOW)
        pending = self.store.list_pending_extractions()
        self.assertEqual(len(pending), 1)
        migrate_contextual(self.store, [self.ip], NOW)
        self.assertEqual(self.store.list_pending_extractions(), pending)
        resolve_candidates(self.store, NOW+timedelta(days=1))
        due, _ = NotificationService().collect_due(self.store, (NOW+timedelta(days=1)).date())
        self.assertEqual(due, [])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = StateStore(self.tmp.name)
        self.store.initialize()
        self.ip = RegisteredIp(slug='genshin-impact', name='原神')
        self.a = observation('a', '原神', digest='a'*64)
        self.c = CampaignAssembler().assemble(packet=build_extraction_input(self.a, self.ip), observation=self.a, extraction=extraction(), now=NOW)
        self.store.save_campaign(self.c)

    def register(self, item):
        register_candidates(self.store, self.ip, [item], NOW)
        for notice in self.store.list_candidates():
            notice.resolution_version = 2
            notice.campaign_ids = [self.c.id]
            notice.campaign_id = self.c.id
            notice.status = 'structured'
            self.store.save_candidate(notice)

    def test_multiple_posts_one_job_and_future_source_no_repeat(self):
        self.register(self.a)
        b = observation('b', '原神', digest='b'*64)
        b.text += '宣传PV'
        self.register(b)
        resolve_candidates(self.store, NOW)
        self.assertEqual(len(self.store.list_queue_jobs()), 1)
        sender = MemorySender()
        service = NotificationService()
        service.deliver_due(store=self.store, day=NOW.date(), generated_at=NOW, sender=sender, recipient='test@example.com')
        c = observation('c', '原神', digest='c'*64)
        c.text += '再次宣传'
        self.register(c)
        resolve_candidates(self.store, NOW+timedelta(days=1))
        items, _ = service.collect_due(self.store, (NOW+timedelta(days=1)).date())
        self.assertEqual(items, [])

    def test_new_fact_only_is_notified_and_cancel_is_visible(self):
        self.register(self.a)
        resolve_candidates(self.store, NOW)
        service = NotificationService()
        service.deliver_due(store=self.store, day=NOW.date(), generated_at=NOW, sender=MemorySender(), recipient='test@example.com')
        self.c.activities[0].actions[0].url = 'https://example.com/reserve'
        self.store.save_campaign(self.c)
        resolve_candidates(self.store, NOW+timedelta(days=1))
        items, _ = service.collect_due(self.store, (NOW+timedelta(days=1)).date())
        self.assertEqual(len(items), 1)
        self.assertIn('https://example.com/reserve', items[0].job.summary)
        self.assertNotIn('2026-09-08', items[0].job.summary)

    def test_legacy_receipt_migration_does_not_resend(self):
        self.register(self.a)
        notice = self.store.list_candidates()[0]
        from herald.candidate_notices import public_facts
        self.store.save_receipt(NotificationReceipt(job_id=notice.id, candidate_id=notice.id,
            semantic_key=notice.id, sent_at=NOW, delivery_day=NOW.date(), facts=public_facts(self.c)))
        prepare_baselines(self.store)
        resolve_candidates(self.store, NOW+timedelta(days=1))
        items, _ = NotificationService().collect_due(self.store, (NOW+timedelta(days=1)).date())
        self.assertEqual(items, [])
