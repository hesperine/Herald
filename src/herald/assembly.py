"""Convert validated AI extraction results into deterministic domain drafts."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime

from .ai import ExtractionInput, ExtractionResult
from .models import (
    Activity,
    Campaign,
    EventAction,
    EventStatus,
    Evidence,
    SourceObservation,
    Venue,
)


NON_WORD = re.compile(r"[^0-9A-Za-z\u3400-\u9fff]+")


def _normalized(value: str | None) -> str:
    return NON_WORD.sub("", value or "").casefold()


def _stable_id(prefix: str, *parts: str) -> str:
    material = "\x1f".join(_normalized(part) for part in parts)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


class CampaignAssembler:
    def assemble(
        self,
        *,
        packet: ExtractionInput,
        observation: SourceObservation,
        extraction: ExtractionResult,
        now: datetime,
    ) -> Campaign | None:
        if not extraction.relevant:
            return None
        campaign_title = extraction.campaign_title or f"{packet.ip_name_hint} 联动"
        campaign_id = _stable_id(
            "campaign",
            packet.ip_slug_hint,
            extraction.partner or "unknown-partner",
            campaign_title,
        )
        claims = [
            Evidence(
                source_id=observation.source.id,
                field_path=claim.field_path,
                quote=claim.quote,
            )
            for claim in extraction.claims
        ]

        activities: list[Activity] = []
        for activity_index, extracted_activity in enumerate(extraction.activities):
            location_hint = self._location_hint(extracted_activity)
            activity_id = _stable_id(
                "activity",
                packet.ip_slug_hint,
                extraction.partner or "unknown-partner",
                extracted_activity.kind.value,
                extracted_activity.title,
                location_hint,
            )
            venues: list[Venue] = []
            for venue_index, extracted_venue in enumerate(extracted_activity.venues):
                venue_id = _stable_id(
                    "venue",
                    activity_id,
                    extracted_venue.city or "",
                    extracted_venue.name or "",
                    extracted_venue.online_platform or "",
                )
                venues.append(
                    Venue(
                        id=venue_id,
                        **extracted_venue.model_dump(),
                        evidence=self._claims_for_prefix(
                            claims,
                            f"activities[{activity_index}].venues[{venue_index}]",
                        ),
                    )
                )

            actions: list[EventAction] = []
            for action_index, extracted_action in enumerate(extracted_activity.actions):
                # Time is intentionally not part of the identity: a changed time
                # must update the same action instead of creating a second action.
                action_id = _stable_id(
                    "action",
                    activity_id,
                    extracted_action.kind.value,
                    extracted_action.title,
                )
                actions.append(
                    EventAction(
                        id=action_id,
                        **extracted_action.model_dump(),
                        evidence=self._claims_for_prefix(
                            claims,
                            f"activities[{activity_index}].actions[{action_index}]",
                        ),
                    )
                )

            activities.append(
                Activity(
                    id=activity_id,
                    kind=extracted_activity.kind,
                    title=extracted_activity.title,
                    status=self._activity_status(
                        extracted_activity.start_at, extracted_activity.end_at, now
                    ),
                    start_at=extracted_activity.start_at,
                    end_at=extracted_activity.end_at,
                    venues=venues,
                    actions=actions,
                    evidence=self._claims_for_prefix(
                        claims, f"activities[{activity_index}]"
                    ),
                )
            )

        campaign_status = self._campaign_status(activities)
        source = observation.source.model_copy(deep=True)
        source.media_urls = list(
            dict.fromkeys([*source.media_urls, *observation.media_urls])
        )
        source.media_hashes = list(
            dict.fromkeys([*source.media_hashes, *observation.media_hashes])
        )
        return Campaign(
            id=campaign_id,
            ip_slug=packet.ip_slug_hint,
            ip_name=packet.ip_name_hint,
            partner=extraction.partner,
            title=campaign_title,
            status=campaign_status,
            announced_at=observation.source.published_at,
            first_seen_at=observation.source.first_seen_at,
            updated_at=now,
            activities=activities,
            sources=[source],
        )

    @staticmethod
    def _claims_for_prefix(claims: list[Evidence], prefix: str) -> list[Evidence]:
        return [
            claim.model_copy(deep=True)
            for claim in claims
            if claim.field_path == prefix or claim.field_path.startswith(prefix + ".")
        ]

    @staticmethod
    def _location_hint(activity: object) -> str:
        venues = getattr(activity, "venues", [])
        values = []
        for venue in venues:
            values.extend(
                [
                    venue.city or "",
                    venue.name or "",
                    venue.online_platform or "",
                    "nationwide" if venue.nationwide else "",
                ]
            )
        return "|".join(values)

    @staticmethod
    def _activity_status(
        start_at: datetime | None, end_at: datetime | None, now: datetime
    ) -> EventStatus:
        if end_at is not None and end_at < now:
            return EventStatus.ENDED
        if start_at is not None and start_at <= now:
            return EventStatus.ONGOING
        if start_at is not None:
            return EventStatus.UPCOMING
        return EventStatus.DETAILS_PENDING

    @staticmethod
    def _campaign_status(activities: list[Activity]) -> EventStatus:
        if not activities:
            return EventStatus.DETAILS_PENDING
        visible = [
            activity
            for activity in activities
            if activity.status not in {EventStatus.ENDED, EventStatus.CANCELLED}
        ]
        if not visible:
            return EventStatus.ENDED
        if any(activity.status is EventStatus.ONGOING for activity in visible):
            return EventStatus.ONGOING
        if any(activity.status is EventStatus.UPCOMING for activity in visible):
            return EventStatus.UPCOMING
        return EventStatus.DETAILS_PENDING
