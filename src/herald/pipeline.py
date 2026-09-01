"""Core observation-to-state processing pipeline."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from .ai import AIProvider, AIProviderError, ExtractionInput, ExtractionResult
from .assembly import CampaignAssembler
from .candidates import CandidateFilter
from .dedupe import ObservationDeduplicator
from .merge import CampaignIdentityResolver, CampaignMerger
from .models import PendingExtraction, PendingReview, SourceObservation, SourceRef
from .registry import RegisteredIp
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
        ocr_text=observation.extracted_media_text,
        media_urls=observation.media_urls,
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
        self,
        *,
        store: StateStore,
        ip: RegisteredIp,
        observations: list[SourceObservation],
        now: datetime,
        provider: AIProvider | None,
        remind_day_before: bool = True,
    ) -> PipelineResult:
        counters = {
            field: 0 for field in PipelineResult.__dataclass_fields__
        }
        by_id = {observation.id: observation for observation in observations}
        existing_campaigns = store.list_campaigns()

        for group in self.deduplicator.group(observations):
            group_items = [by_id[item_id] for item_id in group.observation_ids]
            pending_id = self._id("pending-ai", *group.observation_ids)
            pending = store.load_pending_extraction(pending_id)
            changed_items = [
                item
                for item in group_items
                if store.observation_content_hash(item.id) != item.source.content_hash
            ]
            if not changed_items:
                counters["unchanged_observations"] += len(group_items)
                if pending is None or provider is None:
                    continue

            for item in changed_items:
                store.save_observation(item)
                counters["observations_saved"] += 1

            primary = by_id[group.primary_id]
            decision = self.candidate_filter.evaluate(
                primary,
                ip_names=[ip.name, *ip.aliases],
                from_official_ip_account=True,
            )
            if not decision.relevant:
                continue

            if provider is None:
                store.save_pending_extraction(
                    PendingExtraction(
                        id=pending_id,
                        observation_ids=list(group.observation_ids),
                        ip_slug=ip.slug,
                        queued_at=now,
                        reason="AI provider is not configured",
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
                except AIProviderError:
                    store.save_pending_extraction(
                        PendingExtraction(
                            id=pending_id,
                            observation_ids=list(group.observation_ids),
                            ip_slug=ip.slug,
                            queued_at=now,
                            reason="AI extraction failed; retry required",
                        )
                    )
                    counters["pending_extractions"] += 1
                    continue
                store.save_extraction(
                    primary.source.content_hash, extraction.model_dump(mode="json")
                )
                counters["ai_calls"] += 1
            store.delete_pending_extraction(pending_id)

            packet = build_extraction_input(primary, ip)
            draft = self.assembler.assemble(
                packet=packet,
                observation=primary,
                extraction=extraction,
                now=now,
            )
            if draft is None:
                continue
            draft.sources = self._group_sources(group_items)

            matched, ambiguous = self.identity.find_match(draft, existing_campaigns)
            if matched is None and ambiguous:
                pending_id = self._id("pending-review", draft.id, *[item.id for item in ambiguous])
                store.save_pending_review(
                    PendingReview(
                        id=pending_id,
                        candidate=draft,
                        possible_campaign_ids=[item.id for item in ambiguous],
                        queued_at=now,
                        reason="campaign identity is ambiguous",
                    )
                )
                counters["pending_reviews"] += 1
                continue

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
            for job in self.scheduler.jobs_for_changes(list(outcome.material_changes)):
                store.save_queue_job(job)
                counters["jobs_written"] += 1
            future_jobs = self.scheduler.reconcile_campaign(
                store,
                outcome.campaign,
                now,
                remind_day_before=remind_day_before,
            )
            counters["jobs_written"] += len(future_jobs)

        return PipelineResult(**counters)

    @staticmethod
    def _group_sources(observations: list[SourceObservation]) -> list[SourceRef]:
        sources = []
        seen = set()
        for observation in observations:
            if observation.source.id in seen:
                continue
            sources.append(observation.source.model_copy(deep=True))
            seen.add(observation.source.id)
        return sources

    @staticmethod
    def _id(prefix: str, *parts: str) -> str:
        digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:20]
        return f"{prefix}-{digest}"
