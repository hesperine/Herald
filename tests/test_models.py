from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from herald.models import (
    Activity,
    ActivityKind,
    Campaign,
    EventAction,
    EventStatus,
    ActionKind,
    PendingExtraction,
)


UTC = timezone.utc


class PendingCompatibilityTests(unittest.TestCase):
    def test_old_pending_records_load_without_new_diagnostic_fields(self):
        pending = PendingExtraction.model_validate({'id': 'legacy', 'observation_ids': ['source'],
            'ip_slug': 'genshin-impact', 'queued_at': '2026-10-27T00:00:00Z', 'reason': 'pending'})
        self.assertEqual(pending.diagnostics, [])
        self.assertFalse(pending.retry_after_explicit)


class VisibilityTests(unittest.TestCase):
    def test_date_only_end_uses_china_calendar_day(self):
        activity = Activity(id='date-only', kind=ActivityKind.POPUP, title='快闪',
                            start_date='2026-07-04', end_date='2026-07-17', rules='全预约制')
        self.assertIsNone(activity.end_at)
        self.assertTrue(activity.is_visible(datetime(2026, 7, 17, 15, tzinfo=UTC)))
        self.assertFalse(activity.is_visible(datetime(2026, 7, 17, 16, tzinfo=UTC)))

    def test_activity_is_hidden_only_when_whole_activity_is_expired(self) -> None:
        now = datetime(2026, 9, 8, 12, tzinfo=UTC)
        activity = Activity(
            id="activity-a",
            kind=ActivityKind.POPUP,
            title="主题快闪",
            start_at=now - timedelta(days=1),
            end_at=now + timedelta(days=1),
            actions=[
                EventAction(
                    id="reservation",
                    kind=ActionKind.RESERVATION_CLOSE,
                    title="预约截止",
                    at=now - timedelta(hours=2),
                )
            ],
        )

        self.assertTrue(activity.is_visible(now))

        expired = activity.model_copy(update={"end_at": now - timedelta(seconds=1)})
        self.assertFalse(expired.is_visible(now))

    def test_cancelled_campaign_is_hidden(self) -> None:
        now = datetime(2026, 9, 8, 12, tzinfo=UTC)
        campaign = Campaign(
            id="campaign-a",
            ip_slug="genshin-impact",
            ip_name="原神",
            title="联动",
            status=EventStatus.CANCELLED,
            first_seen_at=now,
            updated_at=now,
        )

        self.assertFalse(campaign.is_visible(now))

    def test_campaign_with_pending_activity_remains_visible(self) -> None:
        now = datetime(2026, 9, 8, 12, tzinfo=UTC)
        campaign = Campaign(
            id="campaign-a",
            ip_slug="arknights",
            ip_name="明日方舟",
            title="联动预告",
            first_seen_at=now,
            updated_at=now,
            activities=[
                Activity(
                    id="pending",
                    kind=ActivityKind.OTHER,
                    title="详情待公布",
                    status=EventStatus.DETAILS_PENDING,
                )
            ],
        )

        self.assertTrue(campaign.is_visible(now))


if __name__ == "__main__":
    unittest.main()
