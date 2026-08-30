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
