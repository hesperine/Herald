"""Conservative campaign identity resolution and deterministic merging."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from enum import StrEnum

from .models import (
    Activity,
    Campaign,
    ChangeKind,
    ChangeRecord,
    EventAction,
    SourceRef,
    Venue,
)


NAME_NOISE = re.compile(r"[^0-9A-Za-z\u3400-\u9fff]+")


def normalize_name(value: str | None) -> str:
    return NAME_NOISE.sub("", value or "").casefold()


class IdentityKind(StrEnum):
    MATCH = "match"
    AMBIGUOUS = "ambiguous"
    DISTINCT = "distinct"


@dataclass(frozen=True, slots=True)
class IdentityDecision:
    kind: IdentityKind
    score: float
    reason: str


@dataclass(frozen=True, slots=True)
class MergeConflict:
    field_path: str
    existing_value: object
    incoming_value: object
    reason: str


@dataclass(frozen=True, slots=True)
class MergeOutcome:
    campaign: Campaign
    changes: tuple[ChangeRecord, ...]
    conflicts: tuple[MergeConflict, ...]

    @property
    def material_changes(self) -> tuple[ChangeRecord, ...]:
        return tuple(
            change for change in self.changes if change.kind is not ChangeKind.SOURCE_ADDED
        )


def _campaign_dates(campaign: Campaign) -> set[object]:
    result: set[object] = set()
    for activity in campaign.activities:
        if activity.start_at:
            result.add(activity.start_at.date())
        if activity.end_at:
            result.add(activity.end_at.date())
        for action in activity.actions:
            if action.at:
                result.add(action.at.date())
    return result


def _campaign_cities(campaign: Campaign) -> set[str]:
    return {
        normalize_name(venue.city)
        for activity in campaign.activities
        for venue in activity.venues
        if venue.city
    }


class CampaignIdentityResolver:
    """Resolve only strong matches; uncertain pairs must stay separate."""

    def compare(self, existing: Campaign, incoming: Campaign) -> IdentityDecision:
        if existing.id == incoming.id:
            return IdentityDecision(IdentityKind.MATCH, 1.0, "same campaign id")

        if existing.ip_slug != incoming.ip_slug:
            return IdentityDecision(IdentityKind.DISTINCT, 0.0, "different IP")

        if {s.id for s in existing.sources} & {s.id for s in incoming.sources}:
            return IdentityDecision(IdentityKind.MATCH, 1.0, "same source identity")

        existing_partner = normalize_name(existing.partner)
        incoming_partner = normalize_name(incoming.partner)
        if existing_partner and incoming_partner and existing_partner != incoming_partner:
            return IdentityDecision(IdentityKind.DISTINCT, 0.0, "different partner")

        score = 0.0
        reasons: list[str] = []
        if existing_partner and existing_partner == incoming_partner:
            score += 0.45
            reasons.append("same partner")

        title_similarity = SequenceMatcher(
            None, normalize_name(existing.title), normalize_name(incoming.title)
        ).ratio()
        if title_similarity >= 0.75:
            score += 0.25
            reasons.append("similar title")

        existing_dates = _campaign_dates(existing)
        incoming_dates = _campaign_dates(incoming)
        if existing_dates and incoming_dates and existing_dates & incoming_dates:
            score += 0.20
            reasons.append("shared activity date")

        existing_cities = _campaign_cities(existing)
        incoming_cities = _campaign_cities(incoming)
        if existing_cities and incoming_cities and existing_cities & incoming_cities:
            score += 0.10
            reasons.append("shared city")

        reason = ", ".join(reasons) if reasons else "insufficient identity evidence"
        if score >= 0.75:
            return IdentityDecision(IdentityKind.MATCH, score, reason)
        if score >= 0.40:
            return IdentityDecision(IdentityKind.AMBIGUOUS, score, reason)
        return IdentityDecision(IdentityKind.DISTINCT, score, reason)

    def find_match(
        self, incoming: Campaign, existing_campaigns: list[Campaign]
    ) -> tuple[Campaign | None, list[Campaign]]:
        matches: list[Campaign] = []
        ambiguous: list[Campaign] = []
        for existing in existing_campaigns:
            decision = self.compare(existing, incoming)
            if decision.kind is IdentityKind.MATCH:
                matches.append(existing)
            elif decision.kind is IdentityKind.AMBIGUOUS:
                ambiguous.append(existing)
        if len(matches) == 1:
            return matches[0], ambiguous
        return None, [*matches, *ambiguous]


class CampaignMerger:
    def merge(
        self, existing: Campaign | None, incoming: Campaign, detected_at: datetime
    ) -> MergeOutcome:
        if existing is None:
            change = self._change(
                campaign=incoming,
                detected_at=detected_at,
                kind=ChangeKind.NEW_CAMPAIGN,
                summary=f"新增联动：{incoming.title}",
                semantic_suffix="new-campaign",
                source_ids=[source.id for source in incoming.sources],
            )
            return MergeOutcome(incoming, (change,), ())

        merged = existing.model_copy(deep=True)
        changes: list[ChangeRecord] = []
        conflicts: list[MergeConflict] = []
        incoming_is_newer = incoming.updated_at > existing.updated_at

        source_added = self._merge_sources(merged.sources, incoming.sources)
        if source_added:
            changes.append(
                self._change(
                    campaign=merged,
                    detected_at=detected_at,
                    kind=ChangeKind.SOURCE_ADDED,
                    summary="新增官方证据来源",
                    semantic_suffix="source-added-" + "-".join(sorted(source_added)),
                    source_ids=source_added,
                )
            )

        for field_name in ("partner", "announced_at"):
            self._merge_scalar(
                merged,
                incoming,
                field_name,
                f"campaign.{field_name}",
                incoming_is_newer,
                changes,
                conflicts,
                detected_at,
            )

        if incoming_is_newer:
            for field_name in ("title", "status"):
                self._merge_scalar(
                    merged,
                    incoming,
                    field_name,
                    f"campaign.{field_name}",
                    True,
                    changes,
                    conflicts,
                    detected_at,
                )

        by_activity_id = {activity.id: activity for activity in merged.activities}
        for incoming_activity in incoming.activities:
            activity = by_activity_id.get(incoming_activity.id)
            if activity is None:
                # Only reuse a unique identity with the same title/type and no
                # conflicting city. Dates and newly supplied addresses can change.
                candidates = [a for a in merged.activities
                    if a.kind == incoming_activity.kind
                    and normalize_name(a.title) == normalize_name(incoming_activity.title)
                    and self._compatible_cities(a, incoming_activity)]
                if len(candidates) == 1:
                    activity = candidates[0]
            if activity is None:
                merged.activities.append(incoming_activity.model_copy(deep=True))
                by_activity_id[incoming_activity.id] = merged.activities[-1]
                changes.append(
                    self._change(
                        campaign=merged,
                        activity_id=incoming_activity.id,
                        detected_at=detected_at,
                        kind=ChangeKind.NEW_ACTIVITY,
                        summary=f"新增活动：{incoming_activity.title}",
                        semantic_suffix=f"new-activity-{incoming_activity.id}",
                        source_ids=self._evidence_source_ids(incoming_activity),
                    )
                )
                continue
            self._merge_activity(
                merged,
                activity,
                incoming_activity,
                incoming_is_newer,
                changes,
                conflicts,
                detected_at,
            )

        if changes:
            merged.revision = existing.revision + 1
            merged.updated_at = max(existing.updated_at, incoming.updated_at, detected_at)
        return MergeOutcome(merged, tuple(changes), tuple(conflicts))

    def _merge_activity(
        self,
        campaign: Campaign,
        existing: Activity,
        incoming: Activity,
        incoming_is_newer: bool,
        changes: list[ChangeRecord],
        conflicts: list[MergeConflict],
        detected_at: datetime,
    ) -> None:
        for field_name in ("start_at", "end_at", "start_date", "end_date", "rules", "related_offers"):
            self._merge_scalar(
                existing,
                incoming,
                field_name,
                f"activities.{existing.id}.{field_name}",
                incoming_is_newer,
                changes,
                conflicts,
                detected_at,
                campaign,
                existing.id,
                ChangeKind.SCHEDULE_CHANGED,
            )
        if incoming_is_newer:
            for field_name in ("title", "status", "kind"):
                self._merge_scalar(
                    existing,
                    incoming,
                    field_name,
                    f"activities.{existing.id}.{field_name}",
                    True,
                    changes,
                    conflicts,
                    detected_at,
                    campaign,
                    existing.id,
                )

        self._merge_evidence(existing.evidence, incoming.evidence)
        self._merge_entities(existing.venues, incoming.venues, Venue)

        actions_by_id = {action.id: action for action in existing.actions}
        for incoming_action in incoming.actions:
            action = actions_by_id.get(incoming_action.id)
            if action is None:
                candidates = [a for a in existing.actions
                    if a.kind == incoming_action.kind
                    and normalize_name(a.title) == normalize_name(incoming_action.title)]
                if len(candidates) == 1:
                    action = candidates[0]
            if action is None:
                existing.actions.append(incoming_action.model_copy(deep=True))
                changes.append(
                    self._change(
                        campaign=campaign,
                        activity_id=existing.id,
                        detected_at=detected_at,
                        kind=ChangeKind.FIELD_ADDED,
                        summary=f"新增时间节点：{incoming_action.title}",
                        semantic_suffix=f"action-added-{incoming_action.id}",
                        source_ids=self._evidence_source_ids(incoming_action),
                        field_paths=[f"activities.{existing.id}.actions.{incoming_action.id}"],
                    )
                )
                continue
            self._merge_action(
                campaign,
                existing,
                action,
                incoming_action,
                incoming_is_newer,
                changes,
                conflicts,
                detected_at,
            )

    def _merge_action(
        self,
        campaign: Campaign,
        activity: Activity,
        existing: EventAction,
        incoming: EventAction,
        incoming_is_newer: bool,
        changes: list[ChangeRecord],
        conflicts: list[MergeConflict],
        detected_at: datetime,
    ) -> None:
        for field_name in ("at", "end_at", "start_date", "end_date", "rules", "scope", "quantity_limit", "end_condition"):
            self._merge_scalar(
                existing,
                incoming,
                field_name,
                f"activities.{activity.id}.actions.{existing.id}.{field_name}",
                incoming_is_newer,
                changes,
                conflicts,
                detected_at,
                campaign,
                activity.id,
                ChangeKind.SCHEDULE_CHANGED,
            )
        if incoming_is_newer:
            for field_name in (
                "title",
                "kind",
                "platform",
                "url",
                "requires_reservation",
                "requires_rush",
                "cancelled",
            ):
                self._merge_scalar(
                    existing,
                    incoming,
                    field_name,
                    f"activities.{activity.id}.actions.{existing.id}.{field_name}",
                    True,
                    changes,
                    conflicts,
                    detected_at,
                    campaign,
                    activity.id,
                )
        if incoming_is_newer and incoming.ended:
            self._merge_scalar(existing, incoming, 'ended',
                f'activities.{activity.id}.actions.{existing.id}.ended', True,
                changes, conflicts, detected_at, campaign, activity.id)
        self._merge_evidence(existing.evidence, incoming.evidence)

    def _merge_scalar(
        self,
        existing_object: object,
        incoming_object: object,
        field_name: str,
        field_path: str,
        incoming_is_newer: bool,
        changes: list[ChangeRecord],
        conflicts: list[MergeConflict],
        detected_at: datetime,
        campaign: Campaign | None = None,
        activity_id: str | None = None,
        change_kind: ChangeKind = ChangeKind.FIELD_UPDATED,
    ) -> None:
        existing_value = getattr(existing_object, field_name)
        incoming_value = getattr(incoming_object, field_name)
        if incoming_value is None or existing_value == incoming_value:
            return
        target_campaign = campaign if campaign is not None else existing_object
        if not isinstance(target_campaign, Campaign):
            raise TypeError("campaign merge requires a Campaign target")

        if existing_value is None or incoming_is_newer:
            setattr(existing_object, field_name, incoming_value)
            changes.append(
                self._change(
                    campaign=target_campaign,
                    activity_id=activity_id,
                    detected_at=detected_at,
                    kind=change_kind if existing_value is not None else ChangeKind.FIELD_ADDED,
                    summary=f"更新字段：{field_path}",
                    semantic_suffix=f"{field_path}-{self._value_hash(incoming_value)}",
                    field_paths=[field_path],
                )
            )
            return

        conflicts.append(
            MergeConflict(
                field_path=field_path,
                existing_value=existing_value,
                incoming_value=incoming_value,
                reason="incoming fact is not newer than existing fact",
            )
        )

    @staticmethod
    def _compatible_cities(left: Activity, right: Activity) -> bool:
        left_cities = {normalize_name(v.city) for v in left.venues if v.city}
        right_cities = {normalize_name(v.city) for v in right.venues if v.city}
        return not left_cities or not right_cities or left_cities == right_cities

    @staticmethod
    def _merge_sources(existing: list[SourceRef], incoming: list[SourceRef]) -> list[str]:
        known = {source.id: source for source in existing}
        added: list[str] = []
        for source in incoming:
            current = known.get(source.id)
            if current is not None:
                if source.summary:
                    current.summary = source.summary
                CampaignMerger._merge_source_media(current, source)
                continue
            existing.append(source.model_copy(deep=True))
            known[source.id] = existing[-1]
            added.append(source.id)
        return added

    @staticmethod
    def _merge_source_media(existing: SourceRef, incoming: SourceRef) -> None:
        known_urls = {str(url) for url in existing.media_urls}
        for url in incoming.media_urls:
            if str(url) not in known_urls:
                existing.media_urls.append(url)
                known_urls.add(str(url))
        known_hashes = set(existing.media_hashes)
        for digest in incoming.media_hashes:
            if digest not in known_hashes:
                existing.media_hashes.append(digest)
                known_hashes.add(digest)

    @staticmethod
    def _merge_evidence(existing: list[object], incoming: list[object]) -> None:
        known = {
            (getattr(item, "source_id", None), getattr(item, "field_path", None), getattr(item, "quote", None))
            for item in existing
        }
        for item in incoming:
            key = (item.source_id, item.field_path, item.quote)
            if key not in known:
                existing.append(item.model_copy(deep=True))
                known.add(key)

    @staticmethod
    def _merge_entities(existing: list[object], incoming: list[object], entity_type: type) -> None:
        by_id = {getattr(item, "id"): item for item in existing}
        for item in incoming:
            if item.id not in by_id:
                existing.append(item.model_copy(deep=True))

    @staticmethod
    def _evidence_source_ids(value: object) -> list[str]:
        evidence = getattr(value, "evidence", [])
        return sorted({item.source_id for item in evidence})

    @staticmethod
    def _value_hash(value: object) -> str:
        return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]

    def _change(
        self,
        *,
        campaign: Campaign,
        detected_at: datetime,
        kind: ChangeKind,
        summary: str,
        semantic_suffix: str,
        activity_id: str | None = None,
        source_ids: list[str] | None = None,
        field_paths: list[str] | None = None,
    ) -> ChangeRecord:
        semantic_key = f"{campaign.id}:{semantic_suffix}"
        digest = hashlib.sha256(semantic_key.encode("utf-8")).hexdigest()[:16]
        return ChangeRecord(
            id=f"chg-{digest}",
            campaign_id=campaign.id,
            activity_id=activity_id,
            kind=kind,
            summary=summary,
            published_at=campaign.announced_at,
            detected_at=detected_at,
            source_ids=source_ids or [],
            field_paths=field_paths or [],
            semantic_key=semantic_key,
        )
