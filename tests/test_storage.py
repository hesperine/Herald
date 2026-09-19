from __future__ import annotations

import tempfile
import unittest
import json
from datetime import date, datetime, timezone
from pathlib import Path

from herald.models import (
    Campaign,
    NotificationKind,
    NotificationReceipt,
    QueueJob,
)
from herald.storage import StateStore, UnsafeStateId


UTC = timezone.utc


class StateStoreTests(unittest.TestCase):
    def test_candidate_records_and_receipts_survive_history_pruning(self):
        from herald.models import CandidateNotice
        record = CandidateNotice(id='candidate-a', ip_slug='genshin-impact', ip_name='原神',
            detected_at=datetime(2026, 1, 1, tzinfo=UTC), public_text='公开联动预告')
        self.store.save_candidate(record)
        receipt = NotificationReceipt(job_id='candidate-a', semantic_key='candidate-a',
            sent_at=datetime(2026, 1, 1, tzinfo=UTC), delivery_day=date(2026, 1, 1))
        self.store.save_receipt(receipt)
        self.store.prune_reminders(date(2026, 9, 19))
        self.assertEqual(self.store.list_candidates(), [record])
        self.assertEqual(self.store.find_receipt('candidate-a'), receipt)

    def test_reminder_retention_preserves_boundary_future_jobs_and_facts(self):
        today=date(2026, 9, 17)
        for day in (date(2026,9,10),date(2026,9,11),today):
            self.store.save_reminder_snapshot(day, {'date':str(day),'cards':[]})
            for bucket in ('daily','queue','notified'):
                path=self.store.root/bucket/f'{day:%Y/%m/%d}'/'record.json'
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text('{}',encoding='utf8')
        future=self.store.root/'queue/2026/10/01/future.json'
        future.parent.mkdir(parents=True);future.write_text('{}',encoding='utf8')
        history=self.store.root/'observations/2026/01/01/history.json'
        history.parent.mkdir(parents=True);history.write_text('{}',encoding='utf8')
        self.store.prune_reminders(today)
        self.assertIsNone(self.store.load_reminder_snapshot(date(2026,9,10)))
        self.assertEqual([x['date'] for x in self.store.list_reminder_snapshots(today)],['2026-09-17','2026-09-11'])
        for bucket in ('daily','queue','notified'):
            self.assertFalse((self.store.root/bucket/'2026/09/10/record.json').exists())
            self.assertTrue((self.store.root/bucket/'2026/09/11/record.json').exists())
        self.assertTrue(future.exists());self.assertTrue(history.exists())

    def test_retention_keeps_unsent_updates_until_delivered(self):
        day = date(2026, 1, 1)
        job = QueueJob(id='unsent', campaign_id='campaign', kind=NotificationKind.UPDATE,
                       due_date=day, semantic_key='unsent', summary='公开更新')
        self.store.save_queue_job(job)
        self.store.prune_reminders(date(2026, 1, 10))
        self.assertEqual(self.store.load_queue_jobs(day), [job])
        self.store.save_receipt(NotificationReceipt(job_id=job.id, semantic_key=job.semantic_key,
            sent_at=datetime(2026, 1, 10, tzinfo=UTC), delivery_day=date(2026, 1, 10)))
        self.store.prune_reminders(date(2026, 1, 10))
        self.assertEqual(self.store.load_queue_jobs(day), [])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = StateStore(Path(self.temporary.name))
        self.store.initialize()

    def test_campaign_round_trip_uses_one_canonical_file(self) -> None:
        now = datetime(2026, 8, 30, 12, tzinfo=UTC)
        campaign = Campaign(
            id="campaign-a",
            ip_slug="genshin-impact",
            ip_name="原神",
            title="原神联动",
            first_seen_at=now,
            updated_at=now,
        )

        path = self.store.save_campaign(campaign)
        loaded = self.store.load_campaign("campaign-a")

        self.assertEqual(path, Path(self.temporary.name) / "events/campaign-a.json")
        self.assertEqual(loaded, campaign)
        self.assertEqual(self.store.list_campaigns(), [campaign])

    def test_queue_reads_only_the_requested_date_bucket(self) -> None:
        first = QueueJob(
            id="job-a",
            campaign_id="campaign-a",
            kind=NotificationKind.DAY_BEFORE,
            due_date=date(2026, 9, 7),
            semantic_key="campaign-a:event-start:day-before",
            summary="明天开始",
        )
        second = first.model_copy(
            update={"id": "job-b", "due_date": date(2026, 9, 8)}
        )
        self.store.save_queue_job(first)
        self.store.save_queue_job(second)

        self.assertEqual(self.store.load_queue_jobs(date(2026, 9, 7)), [first])
        self.assertEqual(self.store.load_queue_jobs(date(2026, 9, 6)), [])

    def test_receipt_is_stored_without_recipient_address(self) -> None:
        sent_at = datetime(2026, 9, 7, 21, tzinfo=UTC)
        receipt = NotificationReceipt(
            job_id="job-a",
            semantic_key="campaign-a:event-start:day-before",
            sent_at=sent_at,
            delivery_day=date(2026, 9, 8),
        )

        path = self.store.save_receipt(receipt)

        self.assertTrue(self.store.has_receipt("job-a", date(2026, 9, 8)))
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("recipient", payload)
        self.assertNotIn("to", payload)
        self.assertNotIn("@", path.read_text(encoding="utf-8"))

    def test_unsafe_ids_cannot_escape_state_root(self) -> None:
        now = datetime(2026, 8, 30, 12, tzinfo=UTC)
        campaign = Campaign(
            id="../outside",
            ip_slug="genshin-impact",
            ip_name="原神",
            title="非法路径",
            first_seen_at=now,
            updated_at=now,
        )

        with self.assertRaises(UnsafeStateId):
            self.store.save_campaign(campaign)


if __name__ == "__main__":
    unittest.main()
