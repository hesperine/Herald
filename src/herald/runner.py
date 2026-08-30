"""One complete daily run, independent from GitHub Actions plumbing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Protocol

from pydantic import ValidationError

from .ai import AIProvider
from .config import RuntimeSettings
from .models import RunReport, SourceKind, SourceObservation
from .notifications import DeliveryResult, EmailSender, NotificationService
from .pipeline import ObservationPipeline, PipelineResult
from .registry import IpRegistry, RegisteredIp, RegisteredSource
from .site import StaticSiteBuilder
from .sources.base import FetchBatch, SourceAccessError
from .sources.weibo import WeiboCursor
from .storage import StateStore


class WeiboAccountFetcher(Protocol):
    async def fetch_account(
        self,
        *,
        account_id: str,
        account_name: str,
        cursor: WeiboCursor | None,
        first_seen_at: datetime,
        published_since: datetime,
    ) -> FetchBatch[WeiboCursor]: ...


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
    ) -> DailyRunResult:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("daily run time must include a timezone")

        store.initialize()
        resolution = self.registry.resolve(settings.public)
        if not resolution.supported:
            raise ValueError("none of WATCH_IPS are supported by the built-in registry")

        warnings = [
            f"unsupported WATCH_IPS entry: {name}" for name in resolution.unsupported
        ]
        pipeline = ObservationPipeline(settings.public.timezone)
        pipeline_results: list[PipelineResult] = []

        pending_by_ip = self._pending_observations(store) if provider is not None else {}
        for ip in resolution.supported:
            observations = list(pending_by_ip.get(ip.slug, []))
            observations_by_id = {item.id: item for item in observations}
            if not ip.sources:
                warnings.append(f"no official source is configured for IP: {ip.name}")

            for source in ip.sources:
                fetched = await self._fetch_source(
                    store=store,
                    ip=ip,
                    source=source,
                    now=now,
                    weibo_client=weibo_client,
                    warnings=warnings,
                )
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
                provider=provider,
                remind_day_before=settings.public.remind_day_before,
            )
            pipeline_results.append(result)

        notification_service = NotificationService(settings.public.timezone)
        local_day = now.astimezone(notification_service.timezone).date()
        delivery: DeliveryResult | None = None
        recipient = self._secret(settings.private.notify_email)
        if recipient and email_sender is not None:
            try:
                delivery = notification_service.deliver_due(
                    store=store,
                    day=local_day,
                    generated_at=now,
                    sender=email_sender,
                    recipient=recipient,
                )
            except Exception:
                # Delivery credentials and provider errors must never enter state/logs.
                warnings.append("email delivery failed; no receipt was recorded")
        else:
            due, _ = notification_service.collect_due(store, local_day)
            if due:
                missing = "NOTIFY_EMAIL" if not recipient else "complete SMTP settings"
                warnings.append(
                    f"{len(due)} notification(s) are due but {missing} are not configured"
                )

        watched_slugs = {ip.slug for ip in resolution.supported}
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
            now=now,
            watched_ip_slugs=watched_slugs,
            origin_city=origin_city,
            reachable_cities=reachable_cities,
            forbidden_values=forbidden_values,
        )

        report = RunReport(
            started_at=now,
            finished_at=self.clock(),
            observations=sum(item.observations_saved for item in pipeline_results),
            duplicates=sum(item.unchanged_observations for item in pipeline_results),
            campaigns_created=sum(item.campaigns_created for item in pipeline_results),
            campaigns_updated=sum(item.campaigns_updated for item in pipeline_results),
            jobs_created=sum(item.jobs_written for item in pipeline_results),
            notifications_sent=len(delivery.sent) if delivery is not None else 0,
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
        warnings: list[str],
    ) -> list[SourceObservation]:
        if source.kind is not SourceKind.WEIBO:
            warnings.append(
                f"unsupported source kind for {ip.name}: {source.kind.value}"
            )
            return []
        if weibo_client is None:
            warnings.append(f"Weibo client is unavailable for IP: {ip.name}")
            return []

        source_id = f"weibo-{source.account_id}"
        cursor_payload = store.load_source_cursor(source_id)
        cursor: WeiboCursor | None = None
        if cursor_payload is not None:
            try:
                cursor = WeiboCursor.model_validate(cursor_payload)
            except ValidationError:
                warnings.append(f"invalid source cursor was ignored: {source_id}")

        try:
            batch = await weibo_client.fetch_account(
                account_id=source.account_id,
                account_name=source.account_name,
                cursor=cursor,
                first_seen_at=now,
                published_since=now - timedelta(hours=72),
            )
        except SourceAccessError:
            warnings.append(f"official source fetch failed: {source_id}")
            return []

        store.save_source_cursor(source_id, batch.cursor.model_dump(mode="json"))
        return [item.observation for item in batch.items]

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
