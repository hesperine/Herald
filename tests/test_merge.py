from __future__ import annotations

import hashlib
import unittest
from datetime import datetime, timedelta, timezone

from herald.merge import CampaignIdentityResolver, CampaignMerger, IdentityKind
from herald.models import (
    ActionKind,
    Activity,
    ActivityKind,
    Campaign,
    ChangeKind,
    EventAction,
    SourceKind,
    SourceRef,
)


UTC = timezone.utc
BASE = datetime(2026, 8, 30, 12, tzinfo=UTC)


def source(source_id: str, published_at: datetime = BASE) -> SourceRef:
    return SourceRef(
        id=source_id,
        kind=SourceKind.WEIBO,
        url=f"https://weibo.com/1/{source_id}",
        account_name=source_id,
        published_at=published_at,
        first_seen_at=published_at,
        content_hash=hashlib.sha256(source_id.encode()).hexdigest(),
    )


def campaign(
    campaign_id: str,
    *,
    partner: str = "示例品牌",
    title: str = "原神 × 示例品牌联动",
    action_at: datetime | None = None,
    updated_at: datetime = BASE,
    sources: list[SourceRef] | None = None,
) -> Campaign:
    actions = []
    if action_at:
        actions.append(
            EventAction(
                id="sale-open",
                kind=ActionKind.SALE_OPEN,
                title="联动开售",
                at=action_at,
            )
        )
    return Campaign(
        id=campaign_id,
        ip_slug="genshin-impact",
        ip_name="原神",
        partner=partner,
        title=title,
        first_seen_at=BASE,
        updated_at=updated_at,
        activities=[
            Activity(
                id="national-sale",
                kind=ActivityKind.PRODUCT,
                title="全国产品联动",
                start_at=action_at,
                actions=actions,
            )
        ],
        sources=sources or [],
    )


class CampaignIdentityResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.resolver = CampaignIdentityResolver()

    def test_same_partner_title_and_date_is_a_match(self) -> None:
        day = BASE + timedelta(days=9)
        existing = campaign("existing", action_at=day)
        incoming = campaign("incoming", action_at=day)

        decision = self.resolver.compare(existing, incoming)

        self.assertEqual(decision.kind, IdentityKind.MATCH)

    def test_same_partner_without_other_evidence_is_ambiguous(self) -> None:
        existing = campaign("existing", title="第一期联动")
        incoming = campaign("incoming", title="第二期联动")

        decision = self.resolver.compare(existing, incoming)

        self.assertEqual(decision.kind, IdentityKind.AMBIGUOUS)

    def test_different_partner_is_distinct(self) -> None:
        existing = campaign("existing", partner="品牌A")
        incoming = campaign("incoming", partner="品牌B")

        decision = self.resolver.compare(existing, incoming)

        self.assertEqual(decision.kind, IdentityKind.DISTINCT)


class CampaignMergerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.merger = CampaignMerger()

    def test_new_campaign_creates_immediate_change(self) -> None:
        incoming = campaign("campaign-a", sources=[source("ip-official")])

        outcome = self.merger.merge(None, incoming, BASE)

        self.assertEqual(outcome.campaign, incoming)
        self.assertEqual(outcome.material_changes[0].kind, ChangeKind.NEW_CAMPAIGN)

    def test_duplicate_source_only_adds_evidence_without_material_notification(self) -> None:
        existing = campaign("campaign-a", sources=[source("ip-official")])
        incoming = campaign(
            "campaign-a",
            sources=[source("ip-official"), source("brand-official")],
        )

        outcome = self.merger.merge(existing, incoming, BASE)

        self.assertEqual([item.id for item in outcome.campaign.sources], ["ip-official", "brand-official"])
        self.assertEqual(outcome.material_changes, ())
        self.assertEqual(outcome.changes[0].kind, ChangeKind.SOURCE_ADDED)

    def test_existing_source_accumulates_new_public_media(self) -> None:
        original = source("ip-official")
        original.media_urls = ["https://img.example/first.jpg"]
        original.media_hashes = ["first-token"]
        updated = source("ip-official")
        updated.media_urls = [
            "https://img.example/first.jpg",
            "https://img.example/second.jpg",
        ]
        updated.media_hashes = ["first-token", "second-token"]

        outcome = self.merger.merge(
            campaign("campaign-a", sources=[original]),
            campaign("campaign-a", sources=[updated]),
            BASE,
        )

        merged_source = outcome.campaign.sources[0]
        self.assertEqual(
            [str(url) for url in merged_source.media_urls],
            [
                "https://img.example/first.jpg",
                "https://img.example/second.jpg",
            ],
        )
        self.assertEqual(
            merged_source.media_hashes,
            ["first-token", "second-token"],
        )

    def test_newer_announcement_updates_schedule_and_records_change(self) -> None:
        original_time = BASE + timedelta(days=9)
        changed_time = original_time + timedelta(days=2)
        existing = campaign("campaign-a", action_at=original_time)
        incoming = campaign(
            "campaign-a",
            action_at=changed_time,
            updated_at=BASE + timedelta(days=1),
        )

        outcome = self.merger.merge(existing, incoming, BASE + timedelta(days=1))

        merged_action = outcome.campaign.activities[0].actions[0]
        self.assertEqual(merged_action.at, changed_time)
        self.assertTrue(
            any(change.kind is ChangeKind.SCHEDULE_CHANGED for change in outcome.changes)
        )
        self.assertEqual(outcome.conflicts, ())

    def test_older_conflicting_schedule_is_not_applied(self) -> None:
        original_time = BASE + timedelta(days=9)
        older_time = original_time - timedelta(days=2)
        existing = campaign(
            "campaign-a", action_at=original_time, updated_at=BASE + timedelta(days=1)
        )
        incoming = campaign("campaign-a", action_at=older_time, updated_at=BASE)

        outcome = self.merger.merge(existing, incoming, BASE + timedelta(days=1))

        self.assertEqual(outcome.campaign.activities[0].actions[0].at, original_time)
        self.assertTrue(outcome.conflicts)


if __name__ == "__main__":
    unittest.main()
