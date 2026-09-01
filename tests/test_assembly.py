from __future__ import annotations

import hashlib
import unittest
from datetime import datetime, timezone

from herald.ai import (
    ExtractedAction,
    ExtractedActivity,
    ExtractedClaim,
    ExtractedVenue,
    ExtractionInput,
    ExtractionResult,
)
from herald.assembly import CampaignAssembler
from herald.models import ActionKind, ActivityKind, EventStatus, SourceKind, SourceObservation, SourceRef


UTC = timezone.utc
NOW = datetime(2026, 8, 30, 12, tzinfo=UTC)


def packet() -> ExtractionInput:
    return ExtractionInput(
        observation_id="weibo-a",
        platform="weibo",
        account_name="原神官方微博",
        published_at=NOW,
        text="原神与示例品牌联动，9月8日10:00开售",
        source_url="https://weibo.com/1/a",
        ip_slug_hint="genshin-impact",
        ip_name_hint="原神",
    )


def observation() -> SourceObservation:
    return SourceObservation(
        id="weibo-a",
        source=SourceRef(
            id="weibo-a",
            kind=SourceKind.WEIBO,
            url="https://weibo.com/1/a",
            account_name="原神官方微博",
            published_at=NOW,
            first_seen_at=NOW,
            content_hash=hashlib.sha256(b"material").hexdigest(),
        ),
        text="原神与示例品牌联动，9月8日10:00开售",
        media_urls=[
            "https://wx1.sinaimg.cn/mw2000/public-poster.jpg",
            "https://wx2.sinaimg.cn/mw2000/public-detail.png",
        ],
        media_hashes=["poster-token-hash", "detail-token-hash"],
    )


def extraction(action_time: str = "2026-09-08T10:00:00+08:00") -> ExtractionResult:
    return ExtractionResult(
        relevant=True,
        campaign_title="原神 × 示例品牌联动",
        partner="示例品牌",
        activities=[
            ExtractedActivity(
                kind=ActivityKind.POPUP,
                title="上海主题快闪",
                start_at=action_time,
                venues=[ExtractedVenue(city="上海", name="示例商场")],
                actions=[
                    ExtractedAction(
                        kind=ActionKind.SALE_OPEN,
                        title="联动商品开售",
                        at=action_time,
                        requires_rush=True,
                    )
                ],
            )
        ],
        claims=[
            ExtractedClaim(
                field_path="activities[0].actions[0].at",
                quote="9月8日10:00开售",
                confidence=1,
            )
        ],
    )


class CampaignAssemblerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assembler = CampaignAssembler()

    def test_builds_campaign_activity_action_and_evidence(self) -> None:
        campaign = self.assembler.assemble(
            packet=packet(), observation=observation(), extraction=extraction(), now=NOW
        )

        self.assertIsNotNone(campaign)
        self.assertEqual(campaign.status, EventStatus.UPCOMING)
        self.assertEqual(campaign.activities[0].venues[0].city, "上海")
        action = campaign.activities[0].actions[0]
        self.assertTrue(action.requires_rush)
        self.assertEqual(action.evidence[0].quote, "9月8日10:00开售")
        self.assertEqual(
            [str(url) for url in campaign.sources[0].media_urls],
            [
                "https://wx1.sinaimg.cn/mw2000/public-poster.jpg",
                "https://wx2.sinaimg.cn/mw2000/public-detail.png",
            ],
        )
        self.assertEqual(
            campaign.sources[0].media_hashes,
            ["poster-token-hash", "detail-token-hash"],
        )

    def test_changed_time_keeps_stable_campaign_activity_and_action_ids(self) -> None:
        first = self.assembler.assemble(
            packet=packet(), observation=observation(), extraction=extraction(), now=NOW
        )
        changed = self.assembler.assemble(
            packet=packet(),
            observation=observation(),
            extraction=extraction("2026-09-10T10:00:00+08:00"),
            now=NOW,
        )

        self.assertEqual(first.id, changed.id)
        self.assertEqual(first.activities[0].id, changed.activities[0].id)
        self.assertEqual(first.activities[0].actions[0].id, changed.activities[0].actions[0].id)

    def test_irrelevant_result_does_not_create_campaign(self) -> None:
        result = ExtractionResult(relevant=False)

        campaign = self.assembler.assemble(
            packet=packet(), observation=observation(), extraction=result, now=NOW
        )

        self.assertIsNone(campaign)

    def test_relevant_announcement_without_details_remains_pending(self) -> None:
        result = ExtractionResult(
            relevant=True,
            campaign_title="原神 × 示例品牌联动",
            partner="示例品牌",
        )

        campaign = self.assembler.assemble(
            packet=packet(), observation=observation(), extraction=result, now=NOW
        )

        self.assertEqual(campaign.status, EventStatus.DETAILS_PENDING)
        self.assertEqual(campaign.activities, [])


if __name__ == "__main__":
    unittest.main()
