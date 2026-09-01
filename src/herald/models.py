"""Core domain models.

The model deliberately separates source observations, canonical campaigns,
activities, user-facing changes, and delivery jobs. A source post is evidence;
it is never treated as an event by itself.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class SourceKind(StrEnum):
    WEIBO = "weibo"
    MIYOUSHE = "miyoushe"
    SKLAND = "skland"
    RSS = "rss"
    WEBSITE = "website"


class ActivityKind(StrEnum):
    PRODUCT = "product"
    FOOD = "food"
    POPUP = "popup"
    EXHIBITION = "exhibition"
    THEME_STORE = "theme_store"
    MALL_EVENT = "mall_event"
    CITY_TOUR = "city_tour"
    MERCHANDISE = "merchandise"
    ONLINE = "online"
    IN_GAME = "in_game"
    OTHER = "other"


class EventStatus(StrEnum):
    ANNOUNCED = "announced"
    DETAILS_PENDING = "details_pending"
    UPCOMING = "upcoming"
    ONGOING = "ongoing"
    ENDED = "ended"
    CANCELLED = "cancelled"


class ActionKind(StrEnum):
    ANNOUNCEMENT = "announcement"
    RESERVATION_OPEN = "reservation_open"
    RESERVATION_CLOSE = "reservation_close"
    LOTTERY_OPEN = "lottery_open"
    LOTTERY_RESULT = "lottery_result"
    SALE_OPEN = "sale_open"
    SALE_CLOSE = "sale_close"
    QUEUE_RELEASE = "queue_release"
    EVENT_START = "event_start"
    EVENT_END = "event_end"
    OTHER = "other"


class ChangeKind(StrEnum):
    NEW_CAMPAIGN = "new_campaign"
    NEW_ACTIVITY = "new_activity"
    FIELD_ADDED = "field_added"
    FIELD_UPDATED = "field_updated"
    LOCATION_ADDED = "location_added"
    SCHEDULE_CHANGED = "schedule_changed"
    CANCELLED = "cancelled"
    SOURCE_ADDED = "source_added"


class NotificationKind(StrEnum):
    ANNOUNCEMENT = "announcement"
    UPDATE = "update"
    DAY_BEFORE = "day_before"
    SAME_DAY = "same_day"
    DEADLINE = "deadline"


class Reachability(StrEnum):
    ONLINE = "online"
    NATIONWIDE = "nationwide"
    LOCAL = "local"
    REACHABLE = "reachable"
    CROSS_REGION = "cross_region"
    UNKNOWN = "unknown"


class SourceRef(StrictModel):
    """A public, non-secret reference to one source item."""

    id: str
    kind: SourceKind
    url: HttpUrl
    account_name: str
    account_id: str | None = None
    canonical_content_id: str | None = None
    is_official: bool = True
    published_at: AwareDatetime
    first_seen_at: AwareDatetime
    updated_at: AwareDatetime | None = None
    content_hash: str
    excerpt: str = Field(default="", max_length=500)
    media_urls: list[HttpUrl] = Field(default_factory=list)
    media_hashes: list[str] = Field(default_factory=list)


class SourceObservation(StrictModel):
    """Normalized material fetched from a source.

    Public media URLs are retained so a candidate queued without an AI key can
    still be processed later. Image binaries are never copied into state.
    """

    id: str
    source: SourceRef
    text: str
    media_urls: list[HttpUrl] = Field(default_factory=list)
    media_hashes: list[str] = Field(default_factory=list)
    extracted_media_text: list[str] = Field(default_factory=list)
    outbound_urls: list[HttpUrl] = Field(default_factory=list)


class Evidence(StrictModel):
    source_id: str
    field_path: str
    quote: str = Field(max_length=1000)


class Venue(StrictModel):
    id: str
    name: str | None = None
    country: str = "CN"
    province: str | None = None
    city: str | None = None
    address: str | None = None
    online_platform: str | None = None
    business_hours: str | None = None
    nationwide: bool = False
    evidence: list[Evidence] = Field(default_factory=list)


class EventAction(StrictModel):
    id: str
    kind: ActionKind
    title: str
    at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None
    platform: str | None = None
    url: HttpUrl | None = None
    requires_reservation: bool | None = None
    requires_rush: bool | None = None
    cancelled: bool = False
    evidence: list[Evidence] = Field(default_factory=list)


class Activity(StrictModel):
    id: str
    kind: ActivityKind
    title: str
    status: EventStatus = EventStatus.ANNOUNCED
    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None
    venues: list[Venue] = Field(default_factory=list)
    actions: list[EventAction] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)

    def is_visible(self, now: datetime) -> bool:
        if self.status in {EventStatus.ENDED, EventStatus.CANCELLED}:
            return False
        return self.end_at is None or self.end_at >= now


class Campaign(StrictModel):
    id: str
    ip_slug: str
    ip_name: str
    partner: str | None = None
    title: str
    status: EventStatus = EventStatus.ANNOUNCED
    announced_at: AwareDatetime | None = None
    first_seen_at: AwareDatetime
    updated_at: AwareDatetime
    revision: int = 1
    activities: list[Activity] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)

    def is_visible(self, now: datetime) -> bool:
        if self.status in {EventStatus.ENDED, EventStatus.CANCELLED}:
            return False
        if not self.activities:
            return True
        return any(activity.is_visible(now) for activity in self.activities)


class FieldProposal(StrictModel):
    """One AI-proposed fact; deterministic code decides whether to apply it."""

    field_path: str
    value: Any
    evidence: list[Evidence]
    confidence: float = Field(ge=0, le=1)


class EventPatch(StrictModel):
    observation_ids: list[str]
    campaign_hint: str | None = None
    ip_slug: str
    partner: str | None = None
    proposals: list[FieldProposal] = Field(default_factory=list)


class ChangeRecord(StrictModel):
    id: str
    campaign_id: str
    activity_id: str | None = None
    kind: ChangeKind
    summary: str
    published_at: AwareDatetime | None = None
    detected_at: AwareDatetime
    source_ids: list[str] = Field(default_factory=list)
    field_paths: list[str] = Field(default_factory=list)
    semantic_key: str


class QueueJob(StrictModel):
    id: str
    campaign_id: str
    activity_id: str | None = None
    action_id: str | None = None
    change_id: str | None = None
    kind: NotificationKind
    due_date: date
    expected_at: AwareDatetime | None = None
    semantic_key: str
    summary: str


class NotificationReceipt(StrictModel):
    job_id: str
    semantic_key: str
    sent_at: AwareDatetime
    delivery_day: date
    channel: str = "email"


class ScheduledJobRef(StrictModel):
    job_id: str
    due_date: date


class ScheduleManifest(StrictModel):
    campaign_id: str
    campaign_revision: int
    jobs: list[ScheduledJobRef] = Field(default_factory=list)


class ObservationIndexRecord(StrictModel):
    observation_id: str
    content_hash: str
    stored_day: date


class PendingExtraction(StrictModel):
    id: str
    observation_ids: list[str]
    ip_slug: str
    queued_at: AwareDatetime
    reason: str


class PendingExtractionIndexRecord(StrictModel):
    pending_id: str
    queued_day: date


class PendingReview(StrictModel):
    id: str
    candidate: Campaign
    possible_campaign_ids: list[str]
    queued_at: AwareDatetime
    reason: str


class RunReport(StrictModel):
    started_at: AwareDatetime
    finished_at: AwareDatetime
    observations: int = 0
    duplicates: int = 0
    campaigns_created: int = 0
    campaigns_updated: int = 0
    jobs_created: int = 0
    notifications_sent: int = 0
    warnings: list[str] = Field(default_factory=list)
