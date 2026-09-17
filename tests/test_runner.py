from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from herald.ai import (
    ExtractedAction,
    ExtractedActivity,
    ExtractedVenue,
    ExtractionResult,
    MockAIProvider,
)
from herald.config import load_settings
from herald.media import CachedMediaAsset, MediaCacheResult
from herald.models import (
    ActionKind,
    ActivityKind,
    NotificationKind,
    QueueJob,
    SourceKind,
    SourceObservation,
    SourceRef,
)
from herald.registry import IpRegistry, RegisteredIp, RegisteredSource
from herald.runner import DailyRunner, RunPhase
from herald.sources.base import FetchBatch, FetchedObservation
from herald.sources.miyoushe import MiyousheCursor
from herald.sources.skland import SklandCursor
from herald.sources.weibo import WeiboCursor
from herald.storage import StateStore


UTC = timezone.utc
NOW = datetime(2026, 8, 30, 12, tzinfo=UTC)


class FakeWeiboClient:
    def __init__(self, batches: list[FetchBatch[WeiboCursor]]) -> None:
        self.batches = list(batches)
        self.calls = []

    async def fetch_account(self, **kwargs):
        self.calls.append(kwargs)
        return self.batches.pop(0)


class FakeAccountClient:
    def __init__(self, batches) -> None:
        self.batches = list(batches)
        self.calls = []

    async def fetch_account(self, **kwargs):
        self.calls.append(kwargs)
        return self.batches.pop(0)


class MemorySender:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    def send(self, *, recipient: str, subject: str, text: str) -> None:
        self.messages.append(
            {"recipient": recipient, "subject": subject, "text": text}
        )


class RecordingMediaCache:
    def __init__(self) -> None:
        self.urls: list[str] = []

    async def cache(self, urls, output_dir):
        self.urls = list(urls)
        assets = {
            url: CachedMediaAsset(
                source_url=url,
                asset_path="assets/media/cached.jpg",
                sha256="b" * 64,
                content_type="image/jpeg",
                size_bytes=12,
            )
            for url in urls
        }
        return MediaCacheResult(assets=assets, failed_count=0)


def registry() -> IpRegistry:
    return IpRegistry(
        [
            RegisteredIp(
                slug="genshin-impact",
                name="原神",
                aliases=["Genshin"],
                sources=[
                    RegisteredSource(
                        kind=SourceKind.WEIBO,
                        account_name="原神",
                        account_id="1001",
                        url="https://weibo.com/u/1001",
                    )
                ],
            )
        ]
    )


def observation() -> SourceObservation:
    return SourceObservation(
        id="weibo-post-1",
        source=SourceRef(
            id="weibo-post-1",
            kind=SourceKind.WEIBO,
            url="https://weibo.com/1001/post-1",
            account_name="原神",
            account_id="1001",
            published_at=NOW,
            first_seen_at=NOW,
            content_hash="a" * 64,
        ),
        text="原神品牌联动，9月8日10:00开售",
    )


def extraction() -> ExtractionResult:
    return ExtractionResult(
        relevant=True,
        campaign_title="原神 × 示例品牌联动",
        partner="示例品牌",
        activities=[
            ExtractedActivity(
                kind=ActivityKind.PRODUCT,
                title="全国产品联动",
                actions=[
                    ExtractedAction(
                        kind=ActionKind.SALE_OPEN,
                        title="联动商品开售",
                        at="2026-09-08T10:00:00+08:00",
                    )
                ],
            )
        ],
    )


def settings(
    *,
    with_ai: bool = True,
    with_email: bool = True,
    always_daily: bool = False,
):
    env = {
        "WATCH_IPS": "原神",
        "ORIGIN_CITY": "上海",
        "REACHABLE_CITIES": "上海,杭州",
    }
    if with_ai:
        env.update({"AI_MODEL": "fixture", "AI_API_KEY": "test-ai-key"})
    if with_email:
        env.update(
            {
                "NOTIFY_EMAIL": "player@example.com",
                "SMTP_HOST": "smtp.example.com",
                "SMTP_USERNAME": "player@example.com",
                "SMTP_PASSWORD": "test-smtp-password",
            }
        )
    if always_daily:
        env["ALWAYS_SEND_DAILY_DIGEST"] = "true"
    return load_settings(env)


class DailyRunnerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.store = StateStore(root / "state")
        self.page = root / "page"
        self.runner = DailyRunner(registry=registry(), clock=lambda: NOW)

    async def test_source_edit_keeps_original_first_seen_time(self) -> None:
        self.store.initialize()
        original = observation()
        original.source.first_seen_at = NOW - timedelta(days=2)
        self.store.save_observation(original)
        edited = observation()
        edited.source.content_hash = "b" * 64

        normalized = self.runner._preserve_observation_history(
            self.store, edited, NOW
        )

        self.assertEqual(normalized.source.first_seen_at, NOW - timedelta(days=2))
        self.assertEqual(normalized.source.updated_at, NOW)

    async def test_dispatches_cookie_free_sources_and_saves_independent_cursors(self) -> None:
        cookie_free_registry = IpRegistry(
            [
                RegisteredIp(
                    slug="genshin-impact",
                    name="原神",
                    sources=[
                        RegisteredSource(
                            kind=SourceKind.MIYOUSHE,
                            account_name="原神",
                            account_id="75276539",
                            url=(
                                "https://www.miyoushe.com/ys/accountCenter/"
                                "postList?id=75276539"
                            ),
                        ),
                        RegisteredSource(
                            kind=SourceKind.SKLAND,
                            account_name="示例森空岛官号",
                            account_id="3737967211133",
                            url="https://www.skland.com/profile?id=3737967211133",
                        ),
                    ],
                )
            ]
        )
        miyoushe_item = observation().model_copy(deep=True)
        miyoushe_item.id = "miyoushe-post-1"
        miyoushe_item.source.id = miyoushe_item.id
        miyoushe_item.source.kind = SourceKind.MIYOUSHE
        miyoushe_item.source.url = "https://www.miyoushe.com/ys/article/1"
        skland_item = observation().model_copy(deep=True)
        skland_item.id = "skland-post-2"
        skland_item.source.id = skland_item.id
        skland_item.source.kind = SourceKind.SKLAND
        skland_item.source.url = "https://www.skland.com/article?id=2"
        miyoushe = FakeAccountClient(
            [
                FetchBatch(
                    items=(FetchedObservation(miyoushe_item),),
                    cursor=MiyousheCursor(latest_post_id="1"),
                )
            ]
        )
        skland = FakeAccountClient(
            [
                FetchBatch(
                    items=(FetchedObservation(skland_item),),
                    cursor=SklandCursor(latest_post_id="2"),
                )
            ]
        )

        result = await DailyRunner(
            registry=cookie_free_registry, clock=lambda: NOW
        ).run(
            settings=settings(with_ai=False, with_email=False),
            store=self.store,
            page_dir=self.page,
            now=NOW,
            weibo_client=None,
            miyoushe_client=miyoushe,
            skland_client=skland,
            provider=None,
            email_sender=None,
        )

        self.assertEqual(result.report.observations, 2)
        self.assertEqual(
            miyoushe.calls[0]["account_url"],
            "https://www.miyoushe.com/ys/accountCenter/postList?id=75276539",
        )
        self.assertNotIn("account_url", skland.calls[0])
        self.assertEqual(
            self.store.load_source_cursor("miyoushe-75276539"),
            {"latest_post_id": "1"},
        )
        self.assertEqual(
            self.store.load_source_cursor("skland-3737967211133"),
            {"latest_post_id": "2"},
        )

    async def test_first_source_run_uses_natural_day_history_and_linear_page_limit(self) -> None:
        client = FakeWeiboClient(
            [
                FetchBatch(
                    items=(),
                    cursor=WeiboCursor(
                        container_id="1076031001", latest_post_id="newest"
                    ),
                )
            ]
        )

        await self.runner.run(
            settings=settings(with_ai=False, with_email=False),
            store=self.store,
            page_dir=self.page,
            now=NOW,
            weibo_client=client,
            provider=None,
            email_sender=None,
        )

        china = timezone(timedelta(hours=8))
        self.assertEqual(
            client.calls[0]["published_since"],
            datetime(2026, 8, 10, 0, tzinfo=china),
        )
        self.assertEqual(client.calls[0]["max_pages"], 42)

    async def test_incremental_source_run_reads_two_natural_days(self) -> None:
        self.store.initialize()
        self.store.save_source_cursor(
            "weibo-1001",
            {"container_id": "1076031001", "latest_post_id": "previous"},
        )
        client = FakeWeiboClient(
            [
                FetchBatch(
                    items=(),
                    cursor=WeiboCursor(
                        container_id="1076031001", latest_post_id="newest"
                    ),
                )
            ]
        )

        await self.runner.run(
            settings=settings(with_ai=False, with_email=False),
            store=self.store,
            page_dir=self.page,
            now=NOW,
            weibo_client=client,
            provider=None,
            email_sender=None,
        )

        china = timezone(timedelta(hours=8))
        self.assertEqual(
            client.calls[0]["published_since"],
            datetime(2026, 8, 29, 0, tzinfo=china),
        )
        self.assertEqual(client.calls[0]["max_pages"], 4)

    async def test_local_fetch_and_extract_phases_do_not_cross_io_boundaries(self) -> None:
        item = observation()
        fetch_client = FakeWeiboClient(
            [
                FetchBatch(
                    items=(FetchedObservation(item),),
                    cursor=WeiboCursor(
                        container_id="1076031001", latest_post_id="next"
                    ),
                )
            ]
        )
        provider = MockAIProvider({item.id: extraction()})
        sender = MemorySender()

        fetched = await self.runner.run(
            settings=settings(),
            store=self.store,
            page_dir=self.page,
            now=NOW,
            weibo_client=fetch_client,
            provider=provider,
            email_sender=sender,
            phase=RunPhase.FETCH,
        )

        self.assertEqual(fetched.report.observations, 1)
        self.assertEqual(self.store.list_campaigns(), [])
        self.assertEqual(len(self.store.list_pending_extractions()), 1)
        self.assertFalse(self.page.exists())
        self.assertEqual(sender.messages, [])

        extracted = await self.runner.run(
            settings=settings(),
            store=self.store,
            page_dir=self.page,
            now=NOW,
            weibo_client=None,
            provider=provider,
            email_sender=sender,
            phase=RunPhase.EXTRACT,
        )

        self.assertEqual(extracted.report.campaigns_created, 1)
        self.assertEqual(self.store.list_pending_extractions(), [])
        self.assertFalse(self.page.exists())
        self.assertEqual(sender.messages, [])

    async def test_bootstrap_history_keeps_future_jobs_without_immediate_mail(self) -> None:
        item = observation()
        item.source.published_at = NOW - timedelta(days=10)
        item.source.first_seen_at = NOW

        await self.runner.run(
            settings=settings(with_email=False),
            store=self.store,
            page_dir=self.page,
            now=NOW,
            weibo_client=FakeWeiboClient(
                [
                    FetchBatch(
                        items=(FetchedObservation(item),),
                        cursor=WeiboCursor(
                            container_id="1076031001", latest_post_id="next"
                        ),
                    )
                ]
            ),
            provider=MockAIProvider({item.id: extraction()}),
            email_sender=None,
            phase=RunPhase.FETCH,
        )
        pending = self.store.list_pending_extractions()
        self.assertEqual(len(pending), 1)
        self.assertFalse(pending[0].notify_immediately)

        await self.runner.run(
            settings=settings(with_email=False),
            store=self.store,
            page_dir=self.page,
            now=NOW,
            weibo_client=None,
            provider=MockAIProvider({item.id: extraction()}),
            email_sender=None,
            phase=RunPhase.EXTRACT,
        )

        self.assertEqual(self.store.load_queue_jobs(date(2026, 8, 30)), [])
        self.assertEqual(len(self.store.load_queue_jobs(date(2026, 9, 7))), 1)

    async def test_complete_run_fetches_extracts_notifies_and_publishes(self) -> None:
        item = observation()
        item.media_urls = ["https://wx1.sinaimg.cn/mw2000/poster.jpg"]
        item.media_hashes = ["poster-token"]
        client = FakeWeiboClient(
            [
                FetchBatch(
                    items=(FetchedObservation(item),),
                    cursor=WeiboCursor(container_id="1076031001", latest_post_id="next"),
                )
            ]
        )
        provider = MockAIProvider({item.id: extraction()})
        sender = MemorySender()
        media_cache = RecordingMediaCache()

        result = await self.runner.run(
            settings=settings(),
            store=self.store,
            page_dir=self.page,
            now=NOW,
            weibo_client=client,
            provider=provider,
            email_sender=sender,
            media_cache=media_cache,
        )

        self.assertEqual(result.report.campaigns_created, 1)
        self.assertEqual(result.report.notifications_sent, 1)
        self.assertEqual(result.report.emails_sent, 1)
        self.assertEqual(result.published_campaigns, 1)
        self.assertEqual(len(sender.messages), 1)
        self.assertEqual(len(self.store.load_queue_jobs(date(2026, 9, 7))), 1)
        self.assertEqual(
            self.store.load_source_cursor("weibo-1001")["latest_post_id"], "next"
        )
        page = json.loads((self.page / "data/active.json").read_text("utf-8"))
        self.assertEqual(page["campaigns"][0]["ip_name"], "原神")
        self.assertEqual(
            page["campaigns"][0]["activities"][0]["reachability"], "unknown"
        )
        self.assertEqual(
            media_cache.urls,
            ["https://wx1.sinaimg.cn/mw2000/poster.jpg"],
        )
        detail = json.loads(
            next((self.page / "events").glob("*.json")).read_text("utf-8")
        )
        self.assertEqual(detail["media"][0]["asset_path"], "assets/media/cached.jpg")

    async def test_opt_in_daily_digest_sends_when_no_notifications_exist(self) -> None:
        sender = MemorySender()

        result = await self.runner.run(
            settings=settings(
                with_ai=False,
                with_email=True,
                always_daily=True,
            ),
            store=self.store,
            page_dir=self.page,
            now=NOW,
            weibo_client=FakeWeiboClient(
                [
                    FetchBatch(
                        items=(),
                        cursor=WeiboCursor(
                            container_id="1076031001", latest_post_id="next"
                        ),
                    )
                ]
            ),
            provider=None,
            email_sender=sender,
        )

        self.assertEqual(result.report.notifications_sent, 0)
        self.assertEqual(result.report.emails_sent, 1)
        self.assertEqual(len(sender.messages), 1)
        self.assertIn("今日暂无需要关注的更新", sender.messages[0]["text"])

    async def test_public_reachability_requires_explicit_opt_in(self) -> None:
        item = observation()
        extracted = extraction()
        extracted.activities[0].venues = [
            ExtractedVenue(
                name="示例门店",
                city="上海",
                country="CN",
                nationwide=False,
            )
        ]
        configured = settings(with_email=False)
        configured.public.publish_reachability = True

        await self.runner.run(
            settings=configured,
            store=self.store,
            page_dir=self.page,
            now=NOW,
            weibo_client=FakeWeiboClient(
                [
                    FetchBatch(
                        items=(FetchedObservation(item),),
                        cursor=WeiboCursor(
                            container_id="1076031001", latest_post_id="next"
                        ),
                    )
                ]
            ),
            provider=MockAIProvider({item.id: extracted}),
            email_sender=None,
        )

        page = json.loads((self.page / "data/active.json").read_text("utf-8"))
        self.assertEqual(
            page["campaigns"][0]["activities"][0]["reachability"], "local"
        )

    async def test_pending_material_is_replayed_when_key_is_added_later(self) -> None:
        item = observation()
        first_client = FakeWeiboClient(
            [
                FetchBatch(
                    items=(FetchedObservation(item),),
                    cursor=WeiboCursor(container_id="1076031001", latest_post_id="next"),
                )
            ]
        )
        await self.runner.run(
            settings=settings(with_ai=False, with_email=False),
            store=self.store,
            page_dir=self.page,
            now=NOW,
            weibo_client=first_client,
            provider=None,
            email_sender=None,
        )
        self.assertEqual(len(self.store.list_pending_extractions()), 1)

        second_client = FakeWeiboClient(
            [
                FetchBatch(
                    items=(),
                    cursor=WeiboCursor(
                        container_id="1076031001", latest_post_id="new-next"
                    ),
                )
            ]
        )
        result = await self.runner.run(
            settings=settings(with_ai=True, with_email=False),
            store=self.store,
            page_dir=self.page,
            now=NOW,
            weibo_client=second_client,
            provider=MockAIProvider({item.id: extraction()}),
            email_sender=None,
        )

        self.assertEqual(result.report.campaigns_created, 1)
        self.assertEqual(self.store.list_pending_extractions(), [])

    async def test_historical_due_job_is_seen_without_new_source_posts(self) -> None:
        item = observation()
        await self.runner.run(
            settings=settings(with_email=False),
            store=self.store,
            page_dir=self.page,
            now=NOW,
            weibo_client=FakeWeiboClient(
                [
                    FetchBatch(
                        items=(FetchedObservation(item),),
                        cursor=WeiboCursor(
                            container_id="1076031001", latest_post_id="next"
                        ),
                    )
                ]
            ),
            provider=MockAIProvider({item.id: extraction()}),
            email_sender=None,
        )
        # Replace today's immediate job with a historical future job due today.
        campaign = self.store.list_campaigns()[0]
        self.store.save_queue_job(
            QueueJob(
                id="history-job",
                campaign_id=campaign.id,
                kind=NotificationKind.DAY_BEFORE,
                due_date=date(2026, 8, 31),
                semantic_key="history-only",
                summary="明天开售：历史资料中的联动商品",
            )
        )
        sender = MemorySender()

        result = await self.runner.run(
            settings=settings(with_ai=False, with_email=True),
            store=self.store,
            page_dir=self.page,
            now=datetime(2026, 8, 31, 12, tzinfo=UTC),
            weibo_client=FakeWeiboClient(
                [
                    FetchBatch(
                        items=(),
                        cursor=WeiboCursor(
                            container_id="1076031001", latest_post_id="later"
                        ),
                    )
                ]
            ),
            provider=None,
            email_sender=sender,
        )

        self.assertEqual(result.report.observations, 0)
        self.assertEqual(result.report.notifications_sent, 1)
        self.assertIn("历史资料", sender.messages[0]["text"])


if __name__ == "__main__":
    unittest.main()
