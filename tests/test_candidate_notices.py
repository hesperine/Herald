import tempfile
import unittest
from datetime import timedelta

from herald.candidate_notices import register_candidates, resolve_candidates
from herald.models import NotificationReceipt
from herald.storage import StateStore
from tests.test_pipeline import NOW, observation, extraction
from herald.pipeline import build_extraction_input
from herald.assembly import CampaignAssembler
from herald.registry import RegisteredIp


class CandidateNoticeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = StateStore(self.tmp.name)
        self.store.initialize()
        self.ip = RegisteredIp(slug='genshin-impact', name='原神')
        self.item = observation('post', '原神', digest='a' * 64)

    def register(self, items=None):
        register_candidates(self.store, self.ip, items or [self.item], NOW)
        return self.store.list_candidates()[0]

    def test_fallback_is_durable_idempotent_and_deduplicates_source_copies(self):
        record = self.register()
        self.register()
        copy = self.item.model_copy(deep=True)
        copy.id = copy.source.id = 'copy'
        self.register([copy])
        self.assertEqual(len(self.store.list_candidates()), 1)
        self.assertEqual(len(self.store.list_queue_jobs()), 1)
        self.assertEqual(self.store.list_campaigns(), [])
        self.assertEqual(record.public_text, self.item.text)

    def test_retry_enriches_same_job_and_only_unseen_facts_trigger_update(self):
        record = self.register()
        self.store.save_receipt(NotificationReceipt(job_id=record.id, semantic_key=record.id,
            sent_at=NOW, delivery_day=NOW.date(), public_text=record.public_text))
        c = CampaignAssembler().assemble(packet=build_extraction_input(self.item, self.ip),
            observation=self.item, extraction=extraction(), now=NOW)
        self.store.save_campaign(c)
        resolve_candidates(self.store, NOW)
        # The date/time was already in the fallback text: no duplicate news.
        self.assertEqual(len(self.store.list_queue_jobs()), 1)
        c.activities[0].actions[0].url = 'https://example.com/reserve'
        self.store.save_campaign(c)
        resolve_candidates(self.store, NOW + timedelta(days=1))
        resolve_candidates(self.store, NOW + timedelta(days=1))
        jobs = self.store.list_queue_jobs()
        self.assertEqual(len(jobs), 2)
        self.assertIn('https://example.com/reserve', jobs[-1].summary)

    def test_unmailed_fallback_is_enriched_without_second_announcement(self):
        record = self.register()
        c = CampaignAssembler().assemble(packet=build_extraction_input(self.item, self.ip),
            observation=self.item, extraction=extraction(), now=NOW)
        self.store.save_campaign(c)
        resolve_candidates(self.store, NOW)
        jobs = self.store.list_queue_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].id, record.id)
        self.assertNotIn('待解析', jobs[0].summary)
        self.assertEqual(self.store.list_candidates()[0].campaign_id, c.id)

    def test_edited_post_is_new_candidate_until_that_version_is_extracted(self):
        record = self.register()
        c = CampaignAssembler().assemble(packet=build_extraction_input(self.item, self.ip),
            observation=self.item, extraction=extraction(), now=NOW)
        self.store.save_campaign(c)
        resolve_candidates(self.store, NOW)
        from herald.notifications import NotificationService
        from tests.test_notifications import MemorySender
        NotificationService().deliver_due(store=self.store, day=NOW.date(), generated_at=NOW,
            sender=MemorySender(), recipient='player@example.com')
        edited = self.item.model_copy(deep=True)
        edited.source.content_hash = 'c' * 64
        edited.text += '，本次活动已取消'
        self.register([edited])
        resolve_candidates(self.store, NOW)
        records = self.store.list_candidates()
        self.assertEqual(len(records), 2)
        new = next(r for r in records if r.id != record.id)
        self.assertEqual(new.status, 'pending')
        self.assertIsNone(self.store.find_receipt(new.id))

    def test_sent_update_remains_known_after_its_queue_is_pruned(self):
        self.register()
        c = CampaignAssembler().assemble(packet=build_extraction_input(self.item, self.ip),
            observation=self.item, extraction=extraction(), now=NOW)
        self.store.save_campaign(c)
        resolve_candidates(self.store, NOW)
        from herald.notifications import NotificationService
        from tests.test_notifications import MemorySender
        service, sender = NotificationService(), MemorySender()
        service.deliver_due(store=self.store, day=NOW.date(), generated_at=NOW,
            sender=sender, recipient='player@example.com')
        c.activities[0].actions[0].url = 'https://example.com/updated'
        self.store.save_campaign(c)
        resolve_candidates(self.store, NOW)
        service.deliver_due(store=self.store, day=(NOW + timedelta(days=1)).date(), generated_at=NOW + timedelta(days=1),
            sender=sender, recipient='player@example.com')
        later = NOW + timedelta(days=9)
        self.store.prune_reminders(later.date())
        self.assertEqual(self.store.list_queue_jobs(), [])
        resolve_candidates(self.store, later)
        self.assertEqual(self.store.list_queue_jobs(), [])

    def test_reverted_facts_remove_stale_unsent_update_but_notify_after_sent_update(self):
        self.register()
        c = CampaignAssembler().assemble(packet=build_extraction_input(self.item, self.ip),
            observation=self.item, extraction=extraction(), now=NOW)
        self.store.save_campaign(c)
        resolve_candidates(self.store, NOW)
        from herald.notifications import NotificationService
        from tests.test_notifications import MemorySender
        service, sender = NotificationService(), MemorySender()
        service.deliver_due(store=self.store, day=NOW.date(), generated_at=NOW,
            sender=sender, recipient='player@example.com')
        original = c.activities[0].actions[0].at
        c.activities[0].actions[0].at += timedelta(days=2)
        self.store.save_campaign(c)
        resolve_candidates(self.store, NOW)
        self.assertEqual(len(self.store.list_queue_jobs()), 2)
        c.activities[0].actions[0].at = original
        self.store.save_campaign(c)
        resolve_candidates(self.store, NOW)
        self.assertEqual(len(self.store.list_queue_jobs()), 1)
        c.activities[0].actions[0].at += timedelta(days=2)
        self.store.save_campaign(c)
        resolve_candidates(self.store, NOW)
        later = NOW + timedelta(days=1)
        service.deliver_due(store=self.store, day=later.date(), generated_at=later,
            sender=sender, recipient='player@example.com')
        c.activities[0].actions[0].at = original
        self.store.save_campaign(c)
        resolve_candidates(self.store, NOW)
        items, _ = service.collect_due(self.store, NOW.date(), now=NOW)
        self.assertEqual(len(items), 1)
        self.assertIn(original.isoformat(), items[0].job.summary)
        service.deliver_due(store=self.store, day=(later + timedelta(days=1)).date(), generated_at=later + timedelta(days=1),
            sender=sender, recipient='player@example.com')
        c.activities[0].actions[0].at += timedelta(days=2)
        self.store.save_campaign(c)
        resolve_candidates(self.store, NOW)
        items, _ = service.collect_due(self.store, NOW.date(), now=NOW)
        self.assertEqual(len(items), 1)

    def test_new_post_carries_update_once_instead_of_updating_all_old_notices(self):
        record = self.register()
        c = CampaignAssembler().assemble(packet=build_extraction_input(self.item, self.ip),
            observation=self.item, extraction=extraction(), now=NOW)
        self.store.save_campaign(c)
        resolve_candidates(self.store, NOW)
        self.store.save_receipt(NotificationReceipt(job_id=record.id, semantic_key=record.id,
            sent_at=NOW, delivery_day=NOW.date(), public_text=record.public_text,
            facts=self.store.list_candidates()[0].facts))
        post = observation('second', '原神', digest='b' * 64)
        post.text = '原神品牌联动新增预约入口'
        self.register([post])
        c.sources.append(post.source)
        c.activities[0].actions[0].url = 'https://example.com/new'
        self.store.save_campaign(c)
        resolve_candidates(self.store, NOW)
        self.assertEqual(len(self.store.list_queue_jobs()), 2)
        from herald.notifications import NotificationService
        from tests.test_notifications import MemorySender
        NotificationService().deliver_due(store=self.store, day=NOW.date(), generated_at=NOW,
            sender=MemorySender(), recipient='player@example.com')
        resolve_candidates(self.store, NOW + timedelta(days=1))
        self.assertEqual(len(self.store.list_queue_jobs()), 2)
