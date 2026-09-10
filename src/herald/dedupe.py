"""Conservative source-observation deduplication.

This layer groups repeated source material. It does not decide whether two
different announcements belong to the same campaign; campaign identity is a
separate concern.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import SourceObservation


TRACKING_PARAMETERS = {
    "from",
    "share",
    "source",
    "spm",
    "timestamp",
}
WHITESPACE_OR_PUNCTUATION = re.compile(r"[^0-9A-Za-z\u3400-\u9fff]+")


class DuplicateKind(StrEnum):
    EXACT = "exact"
    SAME_MATERIAL = "same_material"
    DISTINCT = "distinct"


@dataclass(frozen=True, slots=True)
class DuplicateDecision:
    kind: DuplicateKind
    reason: str

    @property
    def is_duplicate(self) -> bool:
        return self.kind is not DuplicateKind.DISTINCT


@dataclass(frozen=True, slots=True)
class ObservationGroup:
    primary_id: str
    observation_ids: tuple[str, ...]


def canonicalize_url(value: str) -> str:
    parts = urlsplit(value)
    filtered_query = []
    for key, item in parse_qsl(parts.query, keep_blank_values=True):
        lowered = key.casefold()
        if lowered.startswith("utm_") or lowered in TRACKING_PARAMETERS:
            continue
        filtered_query.append((key, item))
    return urlunsplit(
        (
            parts.scheme.casefold(),
            parts.netloc.casefold(),
            parts.path.rstrip("/"),
            urlencode(filtered_query),
            "",
        )
    )


def normalize_text(value: str) -> str:
    return WHITESPACE_OR_PUNCTUATION.sub("", value).casefold()


def _shingles(value: str, size: int = 2) -> set[str]:
    normalized = normalize_text(value)
    if len(normalized) <= size:
        return {normalized} if normalized else set()
    return {normalized[index : index + size] for index in range(len(normalized) - size + 1)}


def _similarity(left: str, right: str) -> float:
    left_items = _shingles(left)
    right_items = _shingles(right)
    if not left_items or not right_items:
        return 0.0
    return len(left_items & right_items) / len(left_items | right_items)


class ObservationDeduplicator:
    def compare(
        self, left: SourceObservation, right: SourceObservation
    ) -> DuplicateDecision:
        if left.id == right.id or left.source.id == right.source.id:
            return DuplicateDecision(DuplicateKind.EXACT, "same source identity")

        if (
            left.source.canonical_content_id
            and left.source.canonical_content_id == right.source.canonical_content_id
        ):
            return DuplicateDecision(DuplicateKind.EXACT, "same canonical source post")

        if left.source.content_hash == right.source.content_hash:
            return DuplicateDecision(DuplicateKind.EXACT, "same content hash")

        if normalize_text(left.text) and normalize_text(left.text) == normalize_text(right.text):
            return DuplicateDecision(DuplicateKind.EXACT, "same normalized text")

        left_urls = {canonicalize_url(str(url)) for url in left.outbound_urls}
        right_urls = {canonicalize_url(str(url)) for url in right.outbound_urls}
        if left_urls & right_urls:
            return DuplicateDecision(
                DuplicateKind.SAME_MATERIAL, "shared canonical outbound URL"
            )

        shared_media = set(left.media_hashes) & set(right.media_hashes)
        if shared_media and _similarity(left.text, right.text) >= 0.30:
            return DuplicateDecision(
                DuplicateKind.SAME_MATERIAL, "shared media with similar text"
            )

        return DuplicateDecision(DuplicateKind.DISTINCT, "no reliable duplicate signal")

    def group(self, observations: list[SourceObservation]) -> list[ObservationGroup]:
        """Return stable connected components using conservative pair matches."""

        parents = list(range(len(observations)))

        def find(index: int) -> int:
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index

        def union(left_index: int, right_index: int) -> None:
            left_root = find(left_index)
            right_root = find(right_index)
            if left_root != right_root:
                parents[right_root] = left_root

        for left_index, left in enumerate(observations):
            for right_index in range(left_index + 1, len(observations)):
                # Shared links/posters are association hints, not permission to
                # discard complementary announcements in the extraction stage.
                if self.compare(left, observations[right_index]).kind is DuplicateKind.EXACT:
                    union(left_index, right_index)

        grouped: dict[int, list[SourceObservation]] = {}
        for index, observation in enumerate(observations):
            grouped.setdefault(find(index), []).append(observation)

        result: list[ObservationGroup] = []
        for items in grouped.values():
            ordered = sorted(
                items,
                key=lambda item: (item.source.published_at, item.source.id, item.id),
            )
            result.append(
                ObservationGroup(
                    primary_id=ordered[0].id,
                    observation_ids=tuple(item.id for item in ordered),
                )
            )
        by_id = {item.id: item for item in observations}
        return sorted(result, key=lambda group: (
            by_id[group.primary_id].source.published_at, group.primary_id
        ))
