"""Core observation-to-state processing pipeline."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from .ai import AIProvider, AIProviderError, ExtractionInput, ExtractionResult
from .assembly import CampaignAssembler
from .candidates import CandidateFilter
from .candidate_stage import prepare_candidates
from .dedupe import ObservationDeduplicator
from .merge import CampaignIdentityResolver, CampaignMerger
from .models import PendingExtraction, SourceObservation, SourceRef
from .candidate_notices import register_candidates, resolve_candidates
from .registry import RegisteredIp
from .retry import record_failure
from .scheduler import ScheduleCompiler
from .storage import StateStore


@dataclass(frozen=True, slots=True)
class PipelineResult:
    observations_saved: int = 0
    unchanged_observations: int = 0
    ai_calls: int = 0
    extraction_cache_hits: int = 0
    campaigns_created: int = 0
    campaigns_updated: int = 0
    pending_extractions: int = 0
    pending_reviews: int = 0
    changes_written: int = 0
    jobs_written: int = 0


def build_extraction_input(
    observation: SourceObservation, ip: RegisteredIp
) -> ExtractionInput:
    """Build the exact public packet sent to every AI provider."""

    return ExtractionInput(
        observation_id=observation.id,
        platform=observation.source.kind.value,
        account_name=observation.source.account_name,
        published_at=observation.source.published_at,
        text=observation.text,
        # First runnable version is deliberately text-only. Public image
        # metadata remains on the observation and is published separately.
        ocr_text=[],
        media_urls=[],
        external_links=observation.outbound_urls,
        source_url=observation.source.url,
        ip_slug_hint=ip.slug,
        ip_name_hint=ip.name,
    )


class ObservationPipeline:
    def __init__(self, timezone_name: str = "Asia/Shanghai") -> None:
        self.deduplicator = ObservationDeduplicator()
        self.candidate_filter = CandidateFilter()
        self.assembler = CampaignAssembler()
        self.identity = CampaignIdentityResolver()
        self.merger = CampaignMerger()
        self.scheduler = ScheduleCompiler(timezone_name)

    async def process(
        self, *, store, ip, observations, now, provider, remind_day_before=True,
        suppress_immediate_for=None,
    ) -> PipelineResult:
        before = {j.id for j in store.list_queue_jobs()}
        register_candidates(store, ip, observations, now, str(self.scheduler.timezone))
        result = await self._process(store=store, ip=ip, observations=observations, now=now,
            provider=provider, remind_day_before=remind_day_before,
            suppress_immediate_for=suppress_immediate_for)
        resolve_candidates(store, now, str(self.scheduler.timezone))
        from dataclasses import replace
        return replace(result, jobs_written=len({j.id for j in store.list_queue_jobs()} - before))

    async def _process(
        self,
        *,
        store: StateStore,
        ip: RegisteredIp,
        observations: list[SourceObservation],
        now: datetime,
        provider: AIProvider | None,
        remind_day_before: bool = True,
        suppress_immediate_for: set[str] | None = None,
    ) -> PipelineResult:
        if provider is not None and callable(getattr(provider, 'extract_batch', None)):
            from .semantic_pipeline import process_semantic
            return await process_semantic(self, store=store, ip=ip, observations=observations,
                now=now, provider=provider, remind_day_before=remind_day_before,
                suppress_immediate_for=suppress_immediate_for or set())
        counters = {
            field: 0 for field in PipelineResult.__dataclass_fields__
        }
        by_id = {observation.id: observation for observation in observations}
        existing_campaigns = store.list_campaigns()
        suppressed_observation_ids = suppress_immediate_for or set()

        for selection in prepare_candidates(observations, [ip.name, *ip.aliases]):
            group = selection.group
            group_items = [by_id[item_id] for item_id in group.observation_ids]
            pending_id = self._id("pending-ai", *group.observation_ids)
            pending = store.load_pending_extraction(pending_id)
            changed_items = [
                item
                for item in group_items
                if store.observation_content_hash(item.id) != item.source.content_hash
            ]
            notify_immediately = (
                pending.notify_immediately
                if pending is not None and not changed_items
                else not all(
                    observation_id in suppressed_observation_ids
                    for observation_id in group.observation_ids
                )
            )
            if not changed_items:
                counters["unchanged_observations"] += len(group_items)
                if pending is None or provider is None:
                    continue

            for item in changed_items:
                store.save_observation(item)
                counters["observations_saved"] += 1

            primary = by_id[group.primary_id]
            if not selection.accepted:
                continue

            if pending is not None and not changed_items and pending.next_retry_at and pending.next_retry_at > now:
                counters["pending_extractions"] += 1
                continue
            pending = pending if pending is not None and not changed_items else PendingExtraction(
                id=pending_id, observation_ids=list(group.observation_ids), ip_slug=ip.slug,
                queued_at=now, reason="AI extraction pending", notify_immediately=notify_immediately)
            store.save_pending_extraction(pending)

            if provider is None:
                store.save_pending_extraction(
                    PendingExtraction(
                        id=pending_id,
                        observation_ids=list(group.observation_ids),
                        ip_slug=ip.slug,
                        queued_at=now,
                        reason="AI provider is not configured",
                        notify_immediately=notify_immediately,
                    )
                )
                counters["pending_extractions"] += 1
                continue

            cached = store.load_extraction(primary.source.content_hash)
            if cached is not None:
                extraction = ExtractionResult.model_validate(cached)
                counters["extraction_cache_hits"] += 1
            else:
                packet = build_extraction_input(primary, ip)
                try:
                    extraction = await provider.extract(packet)
                except AIProviderError as error:
                    record_failure(store, pending, now, error)
                    counters["pending_extractions"] += 1
                    continue
                store.save_extraction(
                    primary.source.content_hash, extraction.model_dump(mode="json")
                )
                counters["ai_calls"] += 1

            packet = build_extraction_input(primary, ip)
            draft = self.assembler.assemble(
                packet=packet,
                observation=primary,
                extraction=extraction,
                now=now,
            )
            if draft is None:
                store.delete_pending_extraction(pending_id)
                continue
            draft.sources = self._group_sources(group_items, extraction.source_summaries)

            matched, ambiguous = self.identity.find_match(draft, existing_campaigns)

            outcome = self.merger.merge(matched, draft, now)
            store.save_campaign(outcome.campaign)
            if matched is None:
                existing_campaigns.append(outcome.campaign)
                counters["campaigns_created"] += 1
            else:
                existing_campaigns = [
                    outcome.campaign if item.id == matched.id else item
                    for item in existing_campaigns
                ]
                counters["campaigns_updated"] += 1

            for change in outcome.changes:
                store.save_change(change)
                counters["changes_written"] += 1
            # Candidate notices own news delivery; changes remain an audit trail.
            future_jobs = self.scheduler.reconcile_campaign(
                store,
                outcome.campaign,
                now,
                remind_day_before=remind_day_before,
            )
            counters["jobs_written"] += len(future_jobs)
            store.delete_pending_extraction(pending.id)

        return PipelineResult(**counters)

    @staticmethod
    def _group_sources(observations: list[SourceObservation], summaries: dict[str, str] | None = None) -> list[SourceRef]:
        sources = []
        seen = set()
        for observation in observations:
            if observation.source.id in seen:
                continue
            source = observation.source.model_copy(deep=True)
            source.summary = (summaries or {}).get(observation.id, source.summary)
            source.media_urls = list(
                dict.fromkeys([*source.media_urls, *observation.media_urls])
            )
            source.media_hashes = list(
                dict.fromkeys([*source.media_hashes, *observation.media_hashes])
            )
            sources.append(source)
            seen.add(observation.source.id)
        return sources

    @staticmethod
    def _id(prefix: str, *parts: str) -> str:
        digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:20]
        return f"{prefix}-{digest}"
