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
    SourceKind,
    SourceRef,
    Venue,
)
from herald.media import CachedMediaAsset
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
    def test_unstructured_candidate_is_in_history_but_not_activity_catalog(self):
        from herald.candidate_notices import register_candidates
        from herald.registry import RegisteredIp
        from tests.test_pipeline import observation
        register_candidates(self.store, RegisteredIp(slug='genshin-impact', name='原神'),
            [observation('public', '原神', digest='c' * 64)], NOW)
        self.builder.build(store=self.store, output_dir=self.output, now=NOW,
                           watched_ip_slugs={'genshin-impact'}, forbidden_values=['private-secret-fixture'])
        active = json.loads((self.output / 'data/active.json').read_text('utf8'))
        history = json.loads((self.output / 'data/reminders/2026-08-30.json').read_text('utf8'))
        self.assertEqual(active['cards'], [])
        self.assertEqual(len(history['cards']), 1)
        self.assertIsNone(history['cards'][0]['detail_url'])
        self.assertEqual(history['cards'][0]['extraction_status'], 'pending')
        self.assertFalse(list((self.output / 'events').glob('*.json')))

    def test_legacy_campaign_link_keeps_activity_anchor_after_consolidation(self):
        c = campaign('current')
        self.store.save_campaign(c)
        alias = campaign('old')
        alias.redirected_to = c.id
        self.store.save_campaign(alias)
        self.builder.build(store=self.store, output_dir=self.output, now=NOW,
                           watched_ip_slugs={'genshin-impact'})
        old = json.loads((self.output / 'events/old.json').read_text('utf8'))
        self.assertEqual(old['activities'][0]['id'], 'current-activity')

    def test_history_alias_and_activity_collision_redirect_survive_rebuild(self):
        c = campaign('current')
        self.store.save_campaign(c)
        alias = campaign('old')
        alias.redirected_to = c.id
        alias.activity_redirects = {'old-activity': 'current-activity'}
        self.store.save_campaign(alias)
        self.store.save_reminder_snapshot(NOW.date(), {'date': str(NOW.date()), 'cards': [{
            'campaign_id': 'old', 'activity_id': 'old-activity',
            'campaign_title': alias.title, 'ip_name': alias.ip_name,
            'activity': {'title': '旧活动'}, 'sources': [], 'reasons': []}]})
        self.builder.build(store=self.store, output_dir=self.output, now=NOW,
                           watched_ip_slugs={'genshin-impact'})
        history = json.loads((self.output / 'data/reminders/2026-08-30.json').read_text('utf8'))
        self.assertEqual(history['cards'][0]['detail_url'],
                         'event.html?id=current#activity-current-activity')

    def test_sent_pending_history_links_to_later_structured_details(self):
        from herald.candidate_notices import register_candidates, resolve_candidates
        from herald.notifications import NotificationService
        from herald.registry import RegisteredIp
        from tests.test_pipeline import observation
        from tests.test_notifications import MemorySender
        item = observation('public', '原神', digest='c' * 64)
        register_candidates(self.store, RegisteredIp(slug='genshin-impact', name='原神'), [item], NOW)
        NotificationService().deliver_due(store=self.store, day=NOW.date(), generated_at=NOW,
            sender=MemorySender(), recipient='player@example.com')
        self.builder.build(store=self.store, output_dir=self.output, now=NOW,
                           watched_ip_slugs={'genshin-impact'})
        c = campaign('resolved')
        c.sources = [item.source]
        self.store.save_campaign(c)
        later = NOW + timedelta(days=1)
        resolve_candidates(self.store, later)
        self.builder.build(store=self.store, output_dir=self.output, now=later,
                           watched_ip_slugs={'genshin-impact'})
        history = json.loads((self.output / 'data/reminders/2026-08-30.json').read_text('utf8'))
        self.assertEqual(history['cards'][0]['detail_url'], 'event.html?id=resolved')
        self.assertEqual(history['cards'][0]['extraction_status'], 'structured')

    def test_activity_cards_and_expired_child_filter(self):
        c = campaign('multi')
        expired = c.activities[0].model_copy(deep=True)
        expired.id = 'expired-child'
        expired.end_at = NOW - timedelta(days=1)
        c.activities.append(expired)
        self.store.save_campaign(c)
        self.builder.build(store=self.store, output_dir=self.output, now=NOW,
                           watched_ip_slugs={'genshin-impact'})
        active = json.loads((self.output / 'data/active.json').read_text('utf-8'))
        self.assertEqual(len(active['cards']), 1)
        self.assertIn('#activity-multi-activity', active['cards'][0]['url'])
        detail = json.loads((self.output / 'events/multi.json').read_text('utf-8'))
        self.assertEqual([a['id'] for a in detail['activities']], ['multi-activity'])
        self.assertTrue((self.output / 'today.html').exists())
        calendar = ''.join(p.read_text('utf-8') for p in (self.output / 'data/calendar').glob('*.json'))
        self.assertNotIn('expired-child', calendar)

    def test_seven_day_history_survives_rebuild_and_expired_detail_removal(self):
        from herald.models import QueueJob, NotificationKind
        self.store.save_campaign(campaign('archive'))
        self.store.save_queue_job(QueueJob(id='notice', campaign_id='archive',
            activity_id='archive-activity', kind=NotificationKind.UPDATE,
            due_date=NOW.date(), semantic_key='notice', summary='公开提醒'))
        def build(now):
            self.builder.build(store=self.store, output_dir=self.output, now=now,
                               watched_ip_slugs={'genshin-impact'})
        build(NOW)
        self.store.delete_queue_job(self.store.load_queue_jobs(NOW.date())[0])
        build(NOW)
        self.assertEqual(len(self.store.load_reminder_snapshot(NOW.date())['cards']), 1)
        item = self.store.load_campaign('archive')
        item.status = EventStatus.ENDED
        self.store.save_campaign(item)
        build(NOW + timedelta(days=6))
        history = self.output / 'data/reminders' / (str(NOW.date()) + '.json')
        payload = json.loads(history.read_text('utf-8'))
        self.assertEqual(payload['cards'][0]['reasons'][0]['summary'], '公开提醒')
        self.assertIsNone(payload['cards'][0]['detail_url'])
        self.assertNotIn('venues', payload['cards'][0]['activity'])
        self.assertFalse((self.output / 'events/archive.json').exists())
        build(NOW + timedelta(days=7))
        self.assertFalse(history.exists())
        self.assertIsNone(self.store.load_reminder_snapshot(NOW.date()))
        self.assertIsNotNone(self.store.load_campaign('archive'))

    def test_detail_gallery_has_accessible_dialog_and_original_fallback(self):
        from html.parser import HTMLParser
        self.builder.build(store=self.store, output_dir=self.output, now=NOW,
                           watched_ip_slugs={'genshin-impact'})
        class Elements(HTMLParser):
            def __init__(self):
                super().__init__()
                self.tags = []
            def handle_starttag(self, tag, attrs):
                self.tags.append((tag, dict(attrs)))
        parser = Elements()
        parser.feed((self.output / 'event.html').read_text('utf-8'))
        dialogs = [attrs for tag, attrs in parser.tags if tag == 'dialog']
        self.assertEqual(len(dialogs), 1)
        self.assertEqual(dialogs[0]['aria-labelledby'], 'viewer-caption')
        ids = {attrs.get('id') for tag, attrs in parser.tags}
        self.assertTrue({'viewer-close', 'viewer-prev', 'viewer-next', 'viewer-zoom', 'viewer-original'} <= ids)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.store = StateStore(root / "state")
        self.store.initialize()
        self.output = root / "page"
        self.builder = StaticSiteBuilder("Asia/Shanghai")

    def test_html_declares_utf8_and_chinese_language(self) -> None:
        self.builder.build(
            store=self.store,
            output_dir=self.output,
            now=NOW,
            watched_ip_slugs={"genshin-impact"},
        )

        for filename in ("index.html", "event.html"):
            with self.subTest(filename=filename):
                content = (self.output / filename).read_text(encoding="utf-8")
                self.assertTrue(content.startswith("<!doctype html>"))
                self.assertIn('<html lang="zh-CN">', content)
                self.assertIn('<meta charset="utf-8">', content)

    def test_generated_pages_bypass_stale_browser_caches(self) -> None:
        self.builder.build(
            store=self.store,
            output_dir=self.output,
            now=NOW,
            watched_ip_slugs={"genshin-impact"},
        )

        index = (self.output / "index.html").read_text(encoding="utf-8")
        detail = (self.output / "event.html").read_text(encoding="utf-8")
        today = (self.output / "today.html").read_text(encoding="utf-8")
        self.assertRegex(index, r'href="style\.css\?v=[0-9a-f]{12}"')
        self.assertRegex(index, r'src="app\.js\?v=[0-9a-f]{12}"')
        self.assertRegex(detail, r'src="event\.js\?v=[0-9a-f]{12}"')
        self.assertRegex(today, r'src="today\.js\?v=[0-9a-f]{12}"')

        app_script = (self.output / "app.js").read_text(encoding="utf-8")
        detail_script = (self.output / "event.js").read_text(encoding="utf-8")
        self.assertIn("fetch('data/active.json',{cache:'no-store'})", app_script)
        self.assertIn(
            "fetch('events/'+encodeURIComponent(eventId)+'.json',{cache:'no-store'})",
            detail_script,
        )

    def test_daily_notification_view_is_generated_separately_from_catalog(self) -> None:
        from herald.models import QueueJob, NotificationKind
        self.store.save_campaign(campaign("active"))
        self.store.save_queue_job(QueueJob(id="today", campaign_id="active",
            activity_id="active-activity", kind=NotificationKind.UPDATE,
            due_date=NOW.date(), semantic_key="update", summary="补充地点"))
        self.builder.build(store=self.store, output_dir=self.output, now=NOW,
            watched_ip_slugs={"genshin-impact"})
        payload = json.loads((self.output / "data/today.json").read_text("utf-8"))
        self.assertEqual(len(payload["cards"]), 1)
        self.assertEqual(payload["cards"][0]["activity"]["venues"][0]["city"], "上海")

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

    def test_detail_uses_downloaded_media_and_keeps_source_metadata(self) -> None:
        item = campaign("active")
        item.sources = [
            SourceRef(
                id="weibo-post",
                kind=SourceKind.WEIBO,
                url="https://weibo.com/1/post",
                account_name="原神",
                published_at=NOW,
                first_seen_at=NOW,
                content_hash="a" * 64,
                media_urls=["https://wx1.sinaimg.cn/mw2000/poster.jpg"],
                media_hashes=["public-token-hash"],
            )
        ]
        self.store.save_campaign(item)
        asset = CachedMediaAsset(
            source_url="https://wx1.sinaimg.cn/mw2000/poster.jpg",
            asset_path="assets/media/content-digest.jpg",
            sha256="b" * 64,
            content_type="image/jpeg",
            size_bytes=1234,
        )

        self.builder.build(
            store=self.store,
            output_dir=self.output,
            now=NOW,
            watched_ip_slugs={"genshin-impact"},
            media_assets={str(asset.source_url): asset},
        )

        detail = json.loads(
            (self.output / "events/active.json").read_text(encoding="utf-8")
        )
        self.assertEqual(detail["sources"][0]["media_hashes"], ["public-token-hash"])
        self.assertEqual(detail["media"][0]["asset_path"], asset.asset_path)
        self.assertEqual(detail["media"][0]["sha256"], "b" * 64)
        detail_script = (self.output / "event.js").read_text(encoding="utf-8")
        self.assertIn("createElement('img')", detail_script)
        self.assertIn("asset_path", detail_script)

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

    def test_binary_page_media_does_not_break_secret_scan(self) -> None:
        self.store.save_campaign(campaign("active"))
        media_dir = self.output / "assets" / "media"
        media_dir.mkdir(parents=True)
        (media_dir / "poster.jpg").write_bytes(b"\xff\xd8\xff\x00public-image")

        built = self.builder.build(
            store=self.store,
            output_dir=self.output,
            now=NOW,
            watched_ip_slugs={"genshin-impact"},
            forbidden_values=["private-value"],
        )

        self.assertEqual([item.id for item in built], ["active"])


if __name__ == "__main__":
    unittest.main()
