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
from herald.runner import DailyRunner
from herald.sources.base import FetchBatch, FetchedObservation
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


class MemorySender:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    def send(self, *, recipient: str, subject: str, text: str) -> None:
        self.messages.append(
            {"recipient": recipient, "subject": subject, "text": text}
        )


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


def settings(*, with_ai: bool = True, with_email: bool = True):
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

    async def test_complete_run_fetches_extracts_notifies_and_publishes(self) -> None:
        item = observation()
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

        result = await self.runner.run(
            settings=settings(),
            store=self.store,
            page_dir=self.page,
            now=NOW,
            weibo_client=client,
            provider=provider,
            email_sender=sender,
        )

        self.assertEqual(result.report.campaigns_created, 1)
        self.assertEqual(result.report.notifications_sent, 1)
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
