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

    def test_same_source_edit_retains_campaign_identity(self) -> None:
        existing = campaign("existing", sources=[source("same")])
        incoming = campaign("renamed", sources=[source("same")])
        self.assertEqual(self.resolver.compare(existing, incoming).kind, IdentityKind.MATCH)

    def test_shared_source_does_not_merge_different_known_partners(self):
        a = campaign('one', partner='品牌A', sources=[source('same')])
        b = campaign('two', partner='品牌B', sources=[source('same')])
        self.assertEqual(self.resolver.compare(a, b).kind, IdentityKind.DISTINCT)

    def test_location_enrichment_preserves_activity_and_action_ids(self) -> None:
        from herald.models import Venue
        existing = campaign("existing", action_at=BASE + timedelta(days=3))
        incoming = campaign("existing", action_at=BASE + timedelta(days=4),
            updated_at=BASE + timedelta(days=1))
        existing.sources = [source('same-post')]
        incoming.sources = [source('same-post', BASE + timedelta(days=1))]
        incoming.activities[0].id = "new-generated-id"
        incoming.activities[0].venues = [Venue(id="new-venue", city="上海")]
        incoming.activities[0].actions[0].id = "new-generated-action-id"
        result = CampaignMerger().merge(existing, incoming, BASE + timedelta(days=1))
        self.assertEqual(len(result.campaign.activities), 1)
        activity = result.campaign.activities[0]
        self.assertEqual(activity.id, "national-sale")
        self.assertEqual(activity.actions[0].id, "sale-open")
        self.assertEqual(activity.actions[0].at, BASE + timedelta(days=4))

    def test_different_city_activities_are_not_merged_by_title(self) -> None:
        from herald.models import Venue
        existing = campaign("existing")
        incoming = campaign("existing")
        existing.activities[0].venues = [Venue(id="sh", city="上海")]
        incoming.activities[0].id = "beijing"
        incoming.activities[0].venues = [Venue(id="bj", city="北京")]
        result = CampaignMerger().merge(existing, incoming, BASE)
        self.assertEqual(len(result.campaign.activities), 2)

    def test_same_partner_title_and_date_is_a_match(self) -> None:
        day = BASE + timedelta(days=9)
        existing = campaign("existing", action_at=day)
        incoming = campaign("incoming", action_at=day)

        decision = self.resolver.compare(existing, incoming)

        self.assertEqual(decision.kind, IdentityKind.MATCH)

    def test_same_partner_uses_one_campaign_across_editions(self) -> None:
        existing = campaign("existing", title="第一期联动")
        incoming = campaign("incoming", title="第二期联动")

        decision = self.resolver.compare(existing, incoming)

        self.assertEqual(decision.kind, IdentityKind.MATCH)

    def test_unknown_partners_do_not_merge_by_title(self):
        a = campaign('one', partner=None)
        b = campaign('two', partner=None)
        self.assertEqual(self.resolver.compare(a, b).kind, IdentityKind.DISTINCT)

    def test_separate_batches_keep_separate_activity_ids(self):
        a = campaign('one', action_at=BASE)
        b = campaign('two', action_at=BASE + timedelta(days=30))
        b.activities[0].id = 'second-batch'
        a.sources = [source('first')]
        b.sources = [source('second')]
        result = CampaignMerger().merge(a, b, BASE)
        self.assertEqual([x.id for x in result.campaign.activities], ['national-sale', 'second-batch'])

    def test_different_partner_is_distinct(self) -> None:
        existing = campaign("existing", partner="品牌A")
        incoming = campaign("incoming", partner="品牌B")

        decision = self.resolver.compare(existing, incoming)

        self.assertEqual(decision.kind, IdentityKind.DISTINCT)


class CampaignMergerTests(unittest.TestCase):
    def test_explicit_campaign_cancellation_is_preserved(self):
        from herald.models import EventStatus
        a = campaign('one')
        b = campaign('two', updated_at=BASE + timedelta(days=1))
        b.status = EventStatus.CANCELLED
        result = CampaignMerger().merge(a, b, BASE + timedelta(days=1))
        self.assertEqual(result.campaign.status, EventStatus.CANCELLED)

    def test_existing_source_receives_summary(self):
        a = source('s', BASE)
        b = source('s', BASE)
        b.summary = '预约安排'
        CampaignMerger._merge_sources([a], [b])
        self.assertEqual(a.summary, '预约安排')

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
