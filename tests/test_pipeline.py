from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from herald.ai import AIProviderError, ExtractedAction, ExtractedActivity, ExtractionResult
from herald.models import ActionKind, ActivityKind, SourceKind, SourceObservation, SourceRef
from herald.pipeline import ObservationPipeline, build_extraction_input
from herald.registry import RegisteredIp
from herald.storage import StateStore


UTC = timezone.utc
NOW = datetime(2026, 8, 30, 12, tzinfo=UTC)


class CountingProvider:
    provider_name = "counting"
    model_name = "fixture"

    def __init__(self, extraction: ExtractionResult) -> None:
        self.extraction = extraction
        self.calls = 0

    async def extract(self, packet):
        self.calls += 1
        return self.extraction.model_copy(deep=True)


class FailingProvider:
    provider_name = "failing"
    model_name = "fixture"

    async def extract(self, packet):
        raise AIProviderError("redacted fixture failure")


def observation(observation_id: str, account: str, *, digest: str) -> SourceObservation:
    return SourceObservation(
        id=observation_id,
        source=SourceRef(
            id=observation_id,
            kind=SourceKind.WEIBO,
            url=f"https://weibo.com/1/{observation_id}",
            account_name=account,
            published_at=NOW,
            first_seen_at=NOW,
            content_hash=digest,
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


class ObservationPipelineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = StateStore(Path(self.temporary.name))
        self.store.initialize()
        self.pipeline = ObservationPipeline("Asia/Shanghai")
        self.ip = RegisteredIp(slug="genshin-impact", name="原神", aliases=["Genshin"])

    def test_public_extraction_input_builder_keeps_ai_text_only(self) -> None:
        item = observation("weibo-a", "原神", digest="public-digest")
        item.media_urls = ["https://img.example/poster.jpg"]
        item.extracted_media_text = ["海报公开文字"]
        item.outbound_urls = ["https://example.com/event"]

        packet = build_extraction_input(item, self.ip)

        self.assertEqual(packet.observation_id, "weibo-a")
        self.assertEqual(packet.platform, "weibo")
        self.assertEqual(packet.text, item.text)
        self.assertEqual(packet.ocr_text, [])
        self.assertEqual(packet.media_urls, [])
        self.assertEqual(
            [str(url) for url in item.media_urls],
            ["https://img.example/poster.jpg"],
        )
        self.assertEqual(
            [str(url) for url in packet.external_links],
            ["https://example.com/event"],
        )
        self.assertEqual(packet.ip_slug_hint, "genshin-impact")
        self.assertEqual(packet.ip_name_hint, "原神")

    async def test_duplicate_sources_use_one_ai_call_and_one_campaign(self) -> None:
        digest = hashlib.sha256(b"same-material").hexdigest()
        observations = [
            observation("weibo-a", "IP官号", digest=digest),
            observation("weibo-b", "品牌官号", digest=digest),
        ]
        provider = CountingProvider(extraction())

        result = await self.pipeline.process(
            store=self.store,
            ip=self.ip,
            observations=observations,
            now=NOW,
            provider=provider,
        )

        self.assertEqual(provider.calls, 1)
        self.assertEqual(result.campaigns_created, 1)
        campaigns = self.store.list_campaigns()
        self.assertEqual(len(campaigns), 1)
        self.assertEqual(len(campaigns[0].sources), 2)
        self.assertEqual(len(self.store.load_queue_jobs(date(2026, 8, 30))), 1)
        self.assertEqual(len(self.store.load_queue_jobs(date(2026, 9, 7))), 1)

    async def test_unchanged_second_run_does_not_call_ai_again(self) -> None:
        digest = hashlib.sha256(b"same-material").hexdigest()
        observations = [observation("weibo-a", "IP官号", digest=digest)]
        provider = CountingProvider(extraction())
        arguments = dict(
            store=self.store,
            ip=self.ip,
            observations=observations,
            now=NOW,
            provider=provider,
        )

        await self.pipeline.process(**arguments)
        second = await self.pipeline.process(**arguments)

        self.assertEqual(provider.calls, 1)
        self.assertEqual(second.unchanged_observations, 1)
        self.assertEqual(len(self.store.list_campaigns()), 1)

    async def test_missing_provider_queues_candidate_without_losing_source(self) -> None:
        digest = hashlib.sha256(b"candidate").hexdigest()
        item = observation("weibo-a", "IP官号", digest=digest)

        result = await self.pipeline.process(
            store=self.store,
            ip=self.ip,
            observations=[item],
            now=NOW,
            provider=None,
        )

        self.assertEqual(result.pending_extractions, 1)
        self.assertEqual(result.observations_saved, 1)
        pending_files = list((Path(self.temporary.name) / "pending-extraction").rglob("*.json"))
        self.assertEqual(len(pending_files), 1)
        self.assertEqual(self.store.list_campaigns(), [])

    async def test_pending_candidate_is_retried_after_provider_is_configured(self) -> None:
        digest = hashlib.sha256(b"candidate-retry").hexdigest()
        item = observation("weibo-retry", "IP官号", digest=digest)

        await self.pipeline.process(
            store=self.store,
            ip=self.ip,
            observations=[item],
            now=NOW,
            provider=None,
        )
        provider = CountingProvider(extraction())
        result = await self.pipeline.process(
            store=self.store,
            ip=self.ip,
            observations=[item],
            now=NOW,
            provider=provider,
        )

        self.assertEqual(result.unchanged_observations, 1)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(result.campaigns_created, 1)
        self.assertEqual(self.store.list_pending_extractions(), [])

    async def test_ai_failure_remains_pending_for_a_later_run(self) -> None:
        digest = hashlib.sha256(b"candidate-failure").hexdigest()
        item = observation("weibo-failure", "IP官号", digest=digest)

        result = await self.pipeline.process(
            store=self.store,
            ip=self.ip,
            observations=[item],
            now=NOW,
            provider=FailingProvider(),
        )

        self.assertEqual(result.pending_extractions, 1)
        self.assertEqual(len(self.store.list_pending_extractions()), 1)
        self.assertEqual(self.store.list_campaigns(), [])
        jobs = self.store.load_queue_jobs(NOW.date())
        self.assertEqual(len(jobs), 1)
        self.assertIsNotNone(jobs[0].candidate_id)
        self.assertIsNone(jobs[0].expected_at)

    async def test_old_suppressed_pending_gets_one_fallback_and_one_resolved_notice(self):
        item = observation('historical', '原神', digest='b' * 64)
        kwargs = dict(store=self.store, ip=self.ip, observations=[item], now=NOW,
            suppress_immediate_for={item.id})
        await self.pipeline.process(**kwargs, provider=None)
        pending = self.store.list_pending_extractions()[0]
        self.assertFalse(pending.notify_immediately)
        await self.pipeline.process(**kwargs, provider=CountingProvider(extraction()))
        await self.pipeline.process(**kwargs, provider=CountingProvider(extraction()))
        self.assertEqual(len(self.store.load_queue_jobs(NOW.date())), 1)
        self.assertEqual(self.store.list_pending_extractions(), [])


if __name__ == "__main__":
    unittest.main()
