from __future__ import annotations

import hashlib
import unittest
from datetime import datetime, timezone

from herald.dedupe import DuplicateKind, ObservationDeduplicator, canonicalize_url
from herald.models import SourceKind, SourceObservation, SourceRef


UTC = timezone.utc


def make_observation(
    observation_id: str,
    *,
    text: str,
    account: str,
    content_hash: str | None = None,
    urls: list[str] | None = None,
    media_hashes: list[str] | None = None,
) -> SourceObservation:
    now = datetime(2026, 8, 30, 12, tzinfo=UTC)
    digest = content_hash or hashlib.sha256(
        f"{observation_id}:{text}".encode("utf-8")
    ).hexdigest()
    return SourceObservation(
        id=observation_id,
        source=SourceRef(
            id=f"source-{observation_id}",
            kind=SourceKind.WEIBO,
            url=f"https://weibo.com/1/{observation_id}",
            account_name=account,
            account_id=account,
            published_at=now,
            first_seen_at=now,
            content_hash=digest,
            excerpt=text,
        ),
        text=text,
        outbound_urls=urls or [],
        media_hashes=media_hashes or [],
    )


class ObservationDeduplicatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.deduplicator = ObservationDeduplicator()

    def test_same_content_from_two_accounts_is_exact_duplicate(self) -> None:
        digest = hashlib.sha256("same".encode()).hexdigest()
        left = make_observation("a", text="联动正式公布", account="IP", content_hash=digest)
        right = make_observation(
            "b", text="联动正式公布", account="品牌", content_hash=digest
        )

        decision = self.deduplicator.compare(left, right)

        self.assertEqual(decision.kind, DuplicateKind.EXACT)

    def test_tracking_parameters_do_not_split_same_link(self) -> None:
        left = make_observation(
            "a",
            text="查看活动说明",
            account="IP",
            urls=["https://example.com/event?id=7&utm_source=weibo"],
        )
        right = make_observation(
            "b",
            text="品牌联动活动说明",
            account="品牌",
            urls=["https://example.com/event?id=7&from=share"],
        )

        decision = self.deduplicator.compare(left, right)

        self.assertEqual(decision.kind, DuplicateKind.SAME_MATERIAL)
        self.assertEqual(
            canonicalize_url("https://EXAMPLE.com/event/?utm_source=x&id=7#top"),
            "https://example.com/event?id=7",
        )

    def test_shared_image_and_similar_text_is_same_material(self) -> None:
        left = make_observation(
            "a",
            text="原神联动快闪店九月八日正式开始",
            account="IP",
            media_hashes=["poster-hash"],
        )
        right = make_observation(
            "b",
            text="原神联动快闪店将于九月八日开始",
            account="场地",
            media_hashes=["poster-hash"],
        )

        decision = self.deduplicator.compare(left, right)

        self.assertEqual(decision.kind, DuplicateKind.SAME_MATERIAL)

    def test_similar_topic_without_strong_signal_stays_distinct(self) -> None:
        left = make_observation("a", text="原神联动第一弹", account="IP")
        right = make_observation("b", text="原神联动第二弹", account="IP")

        decision = self.deduplicator.compare(left, right)

        self.assertEqual(decision.kind, DuplicateKind.DISTINCT)

    def test_group_keeps_duplicate_sources_as_one_material_group(self) -> None:
        digest = hashlib.sha256("same".encode()).hexdigest()
        first = make_observation("a", text="活动公布", account="IP", content_hash=digest)
        second = make_observation(
            "b", text="活动公布", account="品牌", content_hash=digest
        )
        third = make_observation("c", text="另一个活动", account="IP")

        groups = self.deduplicator.group([first, second, third])

        self.assertEqual(
            [group.observation_ids for group in groups], [("a", "b"), ("c",)]
        )


if __name__ == "__main__":
    unittest.main()
