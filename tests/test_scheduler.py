from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from herald.models import (
    ActionKind,
    Activity,
    ActivityKind,
    Campaign,
    ChangeKind,
    ChangeRecord,
    EventAction,
    EventStatus,
    NotificationKind,
)
from herald.scheduler import ScheduleCompiler
from herald.storage import StateStore


UTC = timezone.utc
NOW = datetime(2026, 8, 30, 12, tzinfo=UTC)


def make_campaign(action_at: datetime, *, revision: int = 1) -> Campaign:
    return Campaign(
        id="campaign-a",
        ip_slug="genshin-impact",
        ip_name="原神",
        title="原神联动",
        first_seen_at=NOW,
        updated_at=NOW,
        revision=revision,
        activities=[
            Activity(
                id="popup-shanghai",
                kind=ActivityKind.POPUP,
                title="上海快闪",
                actions=[
                    EventAction(
                        id="sale-open",
                        kind=ActionKind.SALE_OPEN,
                        title="联动商品开售",
                        at=action_at,
                        requires_rush=True,
                    )
                ],
            )
        ],
    )


class ScheduleCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.compiler = ScheduleCompiler("Asia/Shanghai")

    def test_new_announcement_becomes_a_job_on_detection_day(self) -> None:
        detected_at = datetime(2026, 8, 30, 13, tzinfo=UTC)
        change = ChangeRecord(
            id="change-a",
            campaign_id="campaign-a",
            kind=ChangeKind.NEW_CAMPAIGN,
            summary="新增联动",
            detected_at=detected_at,
            semantic_key="campaign-a:new",
        )

        jobs = self.compiler.jobs_for_changes([change])

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].kind, NotificationKind.ANNOUNCEMENT)
        self.assertEqual(jobs[0].due_date, date(2026, 8, 30))

    def test_source_only_change_does_not_notify_again(self) -> None:
        change = ChangeRecord(
            id="change-a",
            campaign_id="campaign-a",
            kind=ChangeKind.SOURCE_ADDED,
            summary="增加来源",
            detected_at=NOW,
            semantic_key="campaign-a:source",
        )

        self.assertEqual(self.compiler.jobs_for_changes([change]), [])

    def test_future_action_creates_day_before_job(self) -> None:
        action_at = datetime(2026, 9, 8, 2, tzinfo=UTC)  # 10:00 Shanghai
        campaign = make_campaign(action_at)

        jobs = self.compiler.future_jobs(campaign, NOW)

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].due_date, date(2026, 9, 7))
        self.assertEqual(jobs[0].expected_at, action_at)
        self.assertEqual(jobs[0].kind, NotificationKind.DAY_BEFORE)

    def test_repeated_reconciliation_preserves_distinct_actions_without_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(directory)
            store.initialize()
            item = make_campaign(datetime(2026, 9, 8, 2, tzinfo=UTC))
            item.activities[0].actions.append(EventAction(id="reservation",
                kind=ActionKind.RESERVATION_OPEN, title="预约",
                at=datetime(2026, 9, 8, 1, tzinfo=UTC)))
            self.compiler.reconcile_campaign(store, item, NOW)
            self.compiler.reconcile_campaign(store, item, NOW)
            self.assertEqual(len(store.load_queue_jobs(date(2026, 9, 7))), 2)

    def test_reconcile_moves_changed_schedule_to_new_date_bucket(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory))
            store.initialize()
            original = make_campaign(datetime(2026, 9, 8, 2, tzinfo=UTC))
            original_jobs = self.compiler.reconcile_campaign(store, original, NOW)

            changed = make_campaign(
                datetime(2026, 9, 10, 2, tzinfo=UTC), revision=2
            )
            changed_jobs = self.compiler.reconcile_campaign(store, changed, NOW)

            self.assertEqual(len(original_jobs), 1)
            self.assertEqual(store.load_queue_jobs(date(2026, 9, 7)), [])
            self.assertEqual(
                store.load_queue_jobs(date(2026, 9, 9)), changed_jobs
            )

    def test_stale_job_is_rejected_after_action_time_changes(self) -> None:
        original = make_campaign(datetime(2026, 9, 8, 2, tzinfo=UTC))
        job = self.compiler.future_jobs(original, NOW)[0]
        changed = make_campaign(datetime(2026, 9, 10, 2, tzinfo=UTC))

        self.assertFalse(self.compiler.validate_due_job(job, changed))

    def test_cancelled_campaign_has_no_future_jobs(self) -> None:
        campaign = make_campaign(datetime(2026, 9, 8, 2, tzinfo=UTC))
        campaign.status = EventStatus.CANCELLED

        self.assertEqual(self.compiler.future_jobs(campaign, NOW), [])


if __name__ == "__main__":
    unittest.main()
