"""One complete daily run, independent from GitHub Actions plumbing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone, tzinfo
from enum import StrEnum
from pathlib import Path
from typing import Callable, Protocol

from pydantic import ValidationError

from .ai import AIProvider
from .config import RuntimeSettings
from .media import CachedMediaAsset, MediaCacheResult
from .models import RunReport, SourceKind, SourceObservation
from .notifications import DeliveryResult, EmailSender, NotificationService
from .pipeline import ObservationPipeline, PipelineResult
from .registry import IpRegistry, RegisteredIp, RegisteredSource
from .retry import BudgetedProvider
from .site import StaticSiteBuilder
from .sources.base import FetchBatch, SourceAccessError
from .sources.miyoushe import MiyousheCursor
from .sources.skland import SklandCursor
from .sources.weibo import WeiboCursor
from .storage import StateStore
from .compatibility import repair_state


class WeiboAccountFetcher(Protocol):
    async def fetch_account(
        self,
        *,
        account_id: str,
        account_name: str,
        cursor: WeiboCursor | None,
        first_seen_at: datetime,
        published_since: datetime,
        max_pages: int,
    ) -> FetchBatch[WeiboCursor]: ...


class MiyousheAccountFetcher(Protocol):
    async def fetch_account(
        self,
        *,
        account_id: str,
        account_name: str,
        account_url: str,
        cursor: MiyousheCursor | None,
        first_seen_at: datetime,
        published_since: datetime,
        max_pages: int,
    ) -> FetchBatch[MiyousheCursor]: ...


class SklandAccountFetcher(Protocol):
    async def fetch_account(
        self,
        *,
        account_id: str,
        account_name: str,
        cursor: SklandCursor | None,
        first_seen_at: datetime,
        published_since: datetime,
        max_pages: int,
    ) -> FetchBatch[SklandCursor]: ...


class MediaCache(Protocol):
    async def cache(
        self, urls: list[str], output_dir: Path | str
    ) -> MediaCacheResult: ...


class RunPhase(StrEnum):
    FETCH = "fetch"
    EXTRACT = "extract"
    FULL = "full"
    RETRY = "retry"


PAGES_PER_NATURAL_DAY = 2
INCREMENTAL_NATURAL_DAYS = 2


@dataclass(frozen=True, slots=True)
class DailyRunResult:
    report: RunReport
    pipeline_results: tuple[PipelineResult, ...]
    published_campaigns: int
    delivery: DeliveryResult | None


class DailyRunner:
    """Coordinates source updates, historical jobs, notifications, and pages."""

    def __init__(
        self,
        *,
        registry: IpRegistry | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.registry = registry or IpRegistry.load_builtin()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    async def run(
        self,
        *,
        settings: RuntimeSettings,
        store: StateStore,
        page_dir: Path | str,
        now: datetime,
        weibo_client: WeiboAccountFetcher | None,
        provider: AIProvider | None,
        email_sender: EmailSender | None,
        media_cache: MediaCache | None = None,
        miyoushe_client: MiyousheAccountFetcher | None = None,
        skland_client: SklandAccountFetcher | None = None,
        phase: RunPhase = RunPhase.FULL,
    ) -> DailyRunResult:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("daily run time must include a timezone")

        from time import monotonic
        started = monotonic()
        phase = RunPhase(phase)
        store.initialize()
        initial_job_ids = {job.id for job in store.list_queue_jobs()}
        resolution = self.registry.resolve(settings.public)
        if not resolution.supported:
            raise ValueError("none of WATCH_IPS are supported by the built-in registry")
        if phase is RunPhase.EXTRACT and provider is None:
            raise ValueError("extract phase requires a configured AI provider")

        if provider is not None:
            provider = BudgetedProvider(provider, store, now, started=started)

        warnings = [
            f"unsupported WATCH_IPS entry: {name}" for name in resolution.unsupported
        ]
        pipeline = ObservationPipeline(settings.public.timezone)
        pipeline_results: list[PipelineResult] = []
        notification_service = NotificationService(settings.public.timezone)

        should_fetch = phase in {RunPhase.FETCH, RunPhase.FULL}
        should_extract = phase in {RunPhase.EXTRACT, RunPhase.RETRY, RunPhase.FULL}
        if phase in {RunPhase.FULL, RunPhase.RETRY}:
            repair_state(store, resolution.supported, now, timezone_name=settings.public.timezone,
                         remind_day_before=settings.public.remind_day_before)
        pending_by_ip = (
            self._pending_observations(store)
            if should_extract and provider is not None
            else {}
        )
        for ip in resolution.supported:
            fetched_cursors = {}
            observations = list(pending_by_ip.get(ip.slug, []))
            observations_by_id = {item.id: item for item in observations}
            suppress_immediate_for: set[str] = set()
            if should_fetch and not ip.sources:
                warnings.append(f"no official source is configured for IP: {ip.name}")

            if should_fetch:
                for source in ip.sources:
                    fetched, historical_ids = await self._fetch_source(
                        store=store,
                        ip=ip,
                        source=source,
                        now=now,
                        timezone_info=notification_service.timezone,
                        initial_lookback_days=(
                            settings.public.initial_lookback_days
                        ),
                        weibo_client=weibo_client,
                        miyoushe_client=miyoushe_client,
                        skland_client=skland_client,
                        warnings=warnings,
                        fetched_cursors=fetched_cursors,
                    )
                    suppress_immediate_for.update(historical_ids)
                    for observation in fetched:
                        observation = self._preserve_observation_history(
                            store, observation, now
                        )
                        observations_by_id[observation.id] = observation

            result = await pipeline.process(
                store=store,
                ip=ip,
                observations=list(observations_by_id.values()),
                now=now,
                provider=provider if should_extract else None,
                remind_day_before=settings.public.remind_day_before,
                suppress_immediate_for=suppress_immediate_for,
            )
            pipeline_results.append(result)
            for source_id, cursor_payload in fetched_cursors.items():
                store.save_source_cursor(source_id, cursor_payload)

        delivery_now = max(now, self.clock())
        local_day = delivery_now.astimezone(notification_service.timezone).date()
        delivery: DeliveryResult | None = None
        published: list[object] = []
        if phase in {RunPhase.FULL, RunPhase.RETRY}:
            repair_state(store, resolution.supported, delivery_now, timezone_name=settings.public.timezone,
                         remind_day_before=settings.public.remind_day_before)
            recipient = self._secret(settings.private.notify_email)
            if recipient and email_sender is not None:
                try:
                    delivery = notification_service.deliver_due(
                        store=store,
                        day=local_day,
                        generated_at=delivery_now,
                        sender=email_sender,
                        recipient=recipient,
                        send_empty_digest=phase is RunPhase.FULL and settings.public.always_send_daily_digest,
                        watched_ip_slugs={ip.slug for ip in resolution.supported},
                    )
                except Exception:
                    # Delivery credentials and provider errors must never enter state/logs.
                    warnings.append("email delivery failed; no receipt was recorded")
            else:
                due, _ = notification_service.collect_due(store, local_day, now=delivery_now,
                    watched_ip_slugs={ip.slug for ip in resolution.supported})
                if due or settings.public.always_send_daily_digest:
                    missing = (
                        "NOTIFY_EMAIL"
                        if not recipient
                        else "complete SMTP settings"
                    )
                    if due:
                        warnings.append(
                            f"{len(due)} notification(s) are due but {missing} are not configured"
                        )
                    else:
                        warnings.append(
                            f"daily digest is enabled but {missing} are not configured"
                        )

        if phase in {RunPhase.FULL, RunPhase.RETRY}:
            watched_slugs = {ip.slug for ip in resolution.supported}
            media_assets: dict[str, CachedMediaAsset] = {}
            if media_cache is not None:
                media_urls = self._visible_media_urls(
                    store=store,
                    watched_ip_slugs=watched_slugs,
                    now=now,
                )
                cache_result = await media_cache.cache(media_urls, page_dir)
                media_assets = cache_result.assets
                if cache_result.failed_count:
                    warnings.append(
                        f"{cache_result.failed_count} public image(s) could not be cached"
                    )
            origin_city = None
            reachable_cities: list[str] = []
            if settings.public.publish_reachability:
                origin_city = self._secret(settings.private.origin_city)
                reachable_cities = self._split_private_list(
                    self._secret(settings.private.reachable_cities)
                )
            forbidden_values = [
                value
                for value in (
                    self._secret(settings.private.notify_email),
                    self._secret(settings.private.ai_api_key),
                    self._secret(settings.private.weibo_cookie),
                    self._secret(settings.private.smtp_username),
                    self._secret(settings.private.smtp_password),
                )
                if value
            ]
            published = StaticSiteBuilder(settings.public.timezone).build(
                store=store,
                output_dir=page_dir,
                now=delivery_now,
                watched_ip_slugs=watched_slugs,
                origin_city=origin_city,
                reachable_cities=reachable_cities,
                forbidden_values=forbidden_values,
                media_assets=media_assets,
            )

        report = RunReport(
            started_at=now,
            finished_at=self.clock(),
            observations=sum(item.observations_saved for item in pipeline_results),
            duplicates=sum(item.unchanged_observations for item in pipeline_results),
            campaigns_created=sum(item.campaigns_created for item in pipeline_results),
            campaigns_updated=sum(item.campaigns_updated for item in pipeline_results),
            jobs_created=len({job.id for job in store.list_queue_jobs()} - initial_job_ids),
            notifications_sent=len(delivery.sent) if delivery is not None else 0,
            emails_sent=(
                1 if delivery is not None and delivery.email_sent else 0
            ),
            warnings=warnings,
        )
        store.save_run_report(report)
        return DailyRunResult(
            report=report,
            pipeline_results=tuple(pipeline_results),
            published_campaigns=len(published),
            delivery=delivery,
        )

    async def _fetch_source(
        self,
        *,
        store: StateStore,
        ip: RegisteredIp,
        source: RegisteredSource,
        now: datetime,
        weibo_client: WeiboAccountFetcher | None,
        miyoushe_client: MiyousheAccountFetcher | None,
        skland_client: SklandAccountFetcher | None,
        warnings: list[str],
        fetched_cursors: dict,
        timezone_info: tzinfo,
        initial_lookback_days: int,
    ) -> tuple[list[SourceObservation], set[str]]:
        cursor_type: type[WeiboCursor | MiyousheCursor | SklandCursor]
        client: object | None
        if source.kind is SourceKind.WEIBO:
            cursor_type = WeiboCursor
            client = weibo_client
        elif source.kind is SourceKind.MIYOUSHE:
            cursor_type = MiyousheCursor
            client = miyoushe_client
        elif source.kind is SourceKind.SKLAND:
            cursor_type = SklandCursor
            client = skland_client
        else:
            warnings.append(
                f"unsupported source kind for {ip.name}: {source.kind.value}"
            )
            return [], set()
        if client is None:
            warnings.append(
                f"{source.kind.value} client is unavailable for IP: {ip.name}"
            )
            return [], set()

        source_id = f"{source.kind.value}-{source.account_id}"
        cursor_payload = store.load_source_cursor(source_id)
        cursor: WeiboCursor | MiyousheCursor | SklandCursor | None = None
        has_valid_cursor = False
        if cursor_payload is not None:
            try:
                cursor = cursor_type.model_validate(cursor_payload)
                has_valid_cursor = True
            except ValidationError:
                warnings.append(f"invalid source cursor was ignored: {source_id}")

        natural_days = (
            INCREMENTAL_NATURAL_DAYS
            if has_valid_cursor
            else initial_lookback_days
        )
        local_day = now.astimezone(timezone_info).date()
        first_local_day = local_day - timedelta(days=natural_days - 1)
        published_since = datetime.combine(
            first_local_day, time.min, tzinfo=timezone_info
        )

        parameters = {
            "account_id": source.account_id,
            "account_name": source.account_name,
            "cursor": cursor,
            "first_seen_at": now,
            "published_since": published_since,
            "max_pages": natural_days * PAGES_PER_NATURAL_DAY,
        }
        if source.kind is SourceKind.MIYOUSHE:
            parameters["account_url"] = source.url
        try:
            batch = await client.fetch_account(**parameters)  # type: ignore[union-attr]
        except SourceAccessError:
            warnings.append(f"official source fetch failed: {source_id}")
            return [], set()

        fetched_cursors[source_id] = batch.cursor.model_dump(mode="json")
        observations = [item.observation for item in batch.items]
        historical_ids = {
            item.id
            for item in observations
            if not has_valid_cursor
            and item.source.published_at.astimezone(timezone_info).date() < local_day
        }
        return observations, historical_ids

    @staticmethod
    def _pending_observations(
        store: StateStore,
    ) -> dict[str, list[SourceObservation]]:
        by_ip: dict[str, list[SourceObservation]] = {}
        seen: set[tuple[str, str]] = set()
        for pending in store.list_pending_extractions():
            for observation_id in pending.observation_ids:
                key = (pending.ip_slug, observation_id)
                if key in seen:
                    continue
                observation = store.load_observation(observation_id)
                if observation is not None:
                    by_ip.setdefault(pending.ip_slug, []).append(observation)
                    seen.add(key)
        return by_ip

    @staticmethod
    def _preserve_observation_history(
        store: StateStore, observation: SourceObservation, now: datetime
    ) -> SourceObservation:
        existing = store.load_observation(observation.id)
        if existing is None:
            return observation
        updated = observation.model_copy(deep=True)
        updated.source.first_seen_at = existing.source.first_seen_at
        if updated.source.content_hash != existing.source.content_hash:
            updated.source.updated_at = now
        else:
            updated.source.updated_at = existing.source.updated_at
        return updated

    @staticmethod
    def _secret(value: object | None) -> str | None:
        if value is None:
            return None
        getter = getattr(value, "get_secret_value", None)
        raw = getter() if callable(getter) else str(value)
        normalized = raw.strip()
        return normalized or None

    @staticmethod
    def _split_private_list(value: str | None) -> list[str]:
        if not value:
            return []
        normalized = value.replace("\r", "\n").replace(",", "\n")
        return [item.strip() for item in normalized.split("\n") if item.strip()]

    @staticmethod
    def _visible_media_urls(
        *,
        store: StateStore,
        watched_ip_slugs: set[str],
        now: datetime,
    ) -> list[str]:
        return list(
            dict.fromkeys(
                str(url)
                for campaign in store.list_campaigns()
                if campaign.ip_slug in watched_ip_slugs
                and campaign.is_visible(now)
                for source in campaign.sources
                for url in source.media_urls
            )
        )
