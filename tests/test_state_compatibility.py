import tempfile
import unittest
from datetime import timedelta

from herald.compatibility import repair_state
from herald.models import NotificationReceipt, PendingReview
from herald.storage import StateStore
from herald.registry import RegisteredIp
from herald.scheduler import ScheduleCompiler
from herald.merge import CampaignMerger
from tests.test_merge import campaign, source, BASE


class CompatibilityTests(unittest.TestCase):
    def test_repair_after_interruption_does_not_duplicate_collision_activity(self):
        from unittest.mock import patch
        from herald.compatibility import consolidate_campaigns
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(directory)
            store.initialize()
            one = campaign('one', action_at=BASE + timedelta(days=3))
            two = campaign('two', action_at=BASE + timedelta(days=5))
            store.save_campaign(one)
            store.save_campaign(two)
            save = store.save_campaign
            def interrupted(item):
                if item.redirected_to:
                    raise OSError('interrupted before alias save')
                return save(item)
            with patch.object(store, 'save_campaign', side_effect=interrupted):
                with self.assertRaises(OSError):
                    consolidate_campaigns(store, {'genshin-impact'})
            consolidate_campaigns(store, {'genshin-impact'})
            self.assertEqual(len(store.list_campaigns()[0].activities), 2)

    def test_legacy_activity_id_collision_does_not_discard_a_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(directory)
            store.initialize()
            one = campaign('one', action_at=BASE + timedelta(days=3))
            two = campaign('two', action_at=BASE + timedelta(days=5))
            store.save_campaign(one)
            store.save_campaign(two)
            old_job = ScheduleCompiler().reconcile_campaign(store, two, BASE)[0]
            repair_state(store, [RegisteredIp(slug='genshin-impact', name='原神')], BASE)
            result = store.list_campaigns()[0]
            self.assertEqual(len(result.activities), 2)
            self.assertEqual(len({a.id for a in result.activities}), 2)
            moved = next(j for j in store.list_queue_jobs() if j.id == old_job.id)
            self.assertEqual(moved.activity_id, result.activities[1].id)
            repair_state(store, [RegisteredIp(slug='genshin-impact', name='原神')], BASE)
            self.assertEqual(len(store.list_campaigns()[0].activities), 2)

    def test_old_suppressed_campaign_is_backfilled_once_with_future_job(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(directory)
            store.initialize()
            s = source('historic')
            s.excerpt = '原神品牌联动，9月23日开始'
            c = campaign('legacy', sources=[s], action_at=BASE + timedelta(days=5))
            c.activities[0].actions[0].kind = 'announcement'
            store.save_campaign(c)
            ip = RegisteredIp(slug='genshin-impact', name='原神')
            repair_state(store, [ip], BASE)
            first = store.list_queue_jobs()
            self.assertEqual(len(first), 2)
            for j in first:
                store.save_receipt(NotificationReceipt(job_id=j.id, semantic_key=j.semantic_key,
                    sent_at=BASE, delivery_day=BASE.date()))
            repair_state(store, [ip], BASE + timedelta(days=1))
            self.assertEqual({j.id for j in first}, {j.id for j in store.list_queue_jobs()})

    def test_partner_consolidation_and_pending_review_keep_activity_ids_and_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(directory)
            store.initialize()
            first = campaign('first', action_at=BASE + timedelta(days=5))
            second = campaign('second', action_at=BASE + timedelta(days=6))
            second.activities[0].id = 'other-activity'
            store.save_campaign(first)
            store.save_campaign(second)
            job = ScheduleCompiler().reconcile_campaign(store, second, BASE)[0]
            store.save_receipt(NotificationReceipt(job_id=job.id, semantic_key=job.semantic_key,
                sent_at=BASE, delivery_day=BASE.date()))
            pending = campaign('pending', partner='另一品牌')
            pending.activities[0].id = 'pending-activity'
            store.save_pending_review(PendingReview(id='review', candidate=pending,
                possible_campaign_ids=['first'], queued_at=BASE, reason='ambiguous'))
            repair_state(store, [RegisteredIp(slug='genshin-impact', name='原神')], BASE)
            self.assertEqual(len(store.list_campaigns()), 2)
            self.assertEqual(store.load_campaign('second').id, 'first')
            self.assertEqual({a.id for a in store.load_campaign('first').activities}, {'national-sale', 'other-activity'})
            self.assertIsNotNone(store.find_receipt(job.id))
            self.assertTrue((store.root / 'pending-review').exists())
            repair_state(store, [RegisteredIp(slug='genshin-impact', name='原神')], BASE)
            self.assertEqual(len(store.list_campaigns()), 2)

    def test_legacy_announcement_receipt_is_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(directory)
            store.initialize()
            s = source('historic')
            s.excerpt = '原神品牌联动'
            c = campaign('old', sources=[s])
            store.save_campaign(c)
            change = CampaignMerger().merge(None, c, BASE).changes[0]
            store.save_change(change)
            job = ScheduleCompiler().jobs_for_changes([change])[0]
            store.save_queue_job(job)
            store.save_receipt(NotificationReceipt(job_id=job.id, semantic_key=job.semantic_key,
                sent_at=BASE, delivery_day=BASE.date()))
            repair_state(store, [RegisteredIp(slug='genshin-impact', name='原神')], BASE)
            self.assertEqual([j.id for j in store.list_queue_jobs()], [job.id])

    def test_legacy_receipt_survives_even_if_daily_record_and_queue_were_pruned(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(directory)
            store.initialize()
            s = source('historic')
            s.excerpt = '原神品牌联动'
            c = campaign('old', sources=[s])
            store.save_campaign(c)
            change = CampaignMerger().merge(None, c, BASE).changes[0]
            job = ScheduleCompiler().jobs_for_changes([change])[0]
            store.save_receipt(NotificationReceipt(job_id=job.id, semantic_key=job.semantic_key,
                sent_at=BASE, delivery_day=BASE.date()))
            repair_state(store, [RegisteredIp(slug='genshin-impact', name='原神')], BASE)
            self.assertEqual(store.list_queue_jobs(), [])
            self.assertEqual(store.list_candidates()[0].id, job.id)
