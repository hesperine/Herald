from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from herald.models import (
    ActionKind,
    Activity,
    ActivityKind,
    Campaign,
    EventAction,
    EventStatus,
    Venue,
)
from herald.site import StaticSiteBuilder
from herald.storage import StateStore


UTC = timezone.utc
NOW = datetime(2026, 8, 30, 12, tzinfo=UTC)


def campaign(
    campaign_id: str,
    *,
    ip_slug: str = "genshin-impact",
    status: EventStatus = EventStatus.UPCOMING,
    end_at: datetime | None = None,
) -> Campaign:
    action_at = NOW + timedelta(days=9)
    return Campaign(
        id=campaign_id,
        ip_slug=ip_slug,
        ip_name="原神",
        title="原神 × 示例品牌",
        status=status,
        first_seen_at=NOW,
        updated_at=NOW,
        activities=[
            Activity(
                id=f"{campaign_id}-activity",
                kind=ActivityKind.POPUP,
                title="上海快闪",
                status=status,
                start_at=action_at,
                end_at=end_at or action_at + timedelta(days=3),
                venues=[Venue(id="venue-a", city="上海")],
                actions=[
                    EventAction(
                        id="sale-open",
                        kind=ActionKind.SALE_OPEN,
                        title="联动商品开售",
                        at=action_at,
                    )
                ],
            )
        ],
    )


class StaticSiteBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.store = StateStore(root / "state")
        self.store.initialize()
        self.output = root / "page"
        self.builder = StaticSiteBuilder("Asia/Shanghai")

    def test_build_filters_expired_cancelled_and_unwatched_campaigns(self) -> None:
        (self.output / "events").mkdir(parents=True)
        (self.output / "events/stale.json").write_text("{}", encoding="utf-8")
        self.store.save_campaign(campaign("active"))
        self.store.save_campaign(
            campaign("expired", status=EventStatus.ENDED, end_at=NOW - timedelta(days=1))
        )
        self.store.save_campaign(campaign("cancelled", status=EventStatus.CANCELLED))
        self.store.save_campaign(campaign("other-ip", ip_slug="arknights"))

        built = self.builder.build(
            store=self.store,
            output_dir=self.output,
            now=NOW,
            watched_ip_slugs={"genshin-impact"},
            origin_city="上海",
            reachable_cities=["上海", "杭州"],
        )

        self.assertEqual([item.id for item in built], ["active"])
        active = json.loads((self.output / "data/active.json").read_text(encoding="utf-8"))
        self.assertEqual([item["id"] for item in active["campaigns"]], ["active"])
        self.assertFalse((self.output / "events/expired.json").exists())
        self.assertFalse((self.output / "events/stale.json").exists())
        self.assertTrue((self.output / ".nojekyll").is_file())
        self.assertIn(
            "event.html?id=",
            (self.output / "app.js").read_text(encoding="utf-8"),
        )
        detail_script = (self.output / "event.js").read_text(encoding="utf-8")
        self.assertIn("requires_reservation", detail_script)
        self.assertIn("requires_rush", detail_script)
        self.assertIn("online_platform", detail_script)

    def test_output_contains_reachability_but_not_user_origin_field(self) -> None:
        self.store.save_campaign(campaign("active"))

        self.builder.build(
            store=self.store,
            output_dir=self.output,
            now=NOW,
            watched_ip_slugs={"genshin-impact"},
            origin_city="上海",
            reachable_cities=["上海"],
        )

        content = (self.output / "data/active.json").read_text(encoding="utf-8")
        self.assertIn('"reachability": "local"', content)
        self.assertNotIn("origin_city", content)
        self.assertNotIn("reachable_cities", content)

    def test_calendar_is_compiled_into_one_month_file(self) -> None:
        self.store.save_campaign(campaign("active"))

        self.builder.build(
            store=self.store,
            output_dir=self.output,
            now=NOW,
            watched_ip_slugs={"genshin-impact"},
        )

        month_path = self.output / "data/calendar/2026-09.json"
        payload = json.loads(month_path.read_text(encoding="utf-8"))
        self.assertIn("2026-09-08", payload["days"])
        self.assertEqual(payload["days"]["2026-09-08"][0]["kind"], "sale_open")

    def test_summary_filters_an_expired_activity_using_build_time(self) -> None:
        item = campaign("partly-active")
        expired = item.activities[0].model_copy(deep=True)
        expired.id = "expired-activity"
        expired.title = "已经结束的快闪"
        expired.start_at = NOW - timedelta(days=3)
        expired.end_at = NOW - timedelta(days=1)
        item.activities.append(expired)
        self.store.save_campaign(item)

        self.builder.build(
            store=self.store,
            output_dir=self.output,
            now=NOW,
            watched_ip_slugs={"genshin-impact"},
        )

        payload = json.loads(
            (self.output / "data/active.json").read_text(encoding="utf-8")
        )
        activity_ids = {
            activity["id"] for activity in payload["campaigns"][0]["activities"]
        }
        self.assertEqual(activity_ids, {"partly-active-activity"})

    def test_high_risk_secret_scan_blocks_generated_output(self) -> None:
        self.store.save_campaign(campaign("active"))
        secret = "sk-super-secret-value"
        # A malicious or buggy title must not be allowed to publish a known key.
        item = self.store.load_campaign("active")
        item.title = secret
        self.store.save_campaign(item)

        with self.assertRaisesRegex(ValueError, "sensitive value"):
            self.builder.build(
                store=self.store,
                output_dir=self.output,
                now=NOW,
                watched_ip_slugs={"genshin-impact"},
                forbidden_values=[secret],
            )


if __name__ == "__main__":
    unittest.main()
