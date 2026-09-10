"""State-aware extraction: account-local bootstrap and sequential daily updates."""

import hashlib
import re

from pydantic import ValidationError

from .ai import AIProviderError
from .evidence import quote_matches
from .candidate_stage import prepare_candidates
from .fact_updates import apply_fact_updates
from .models import ChangeKind, PendingExtraction, FactProvenance, EventAction, Venue, Evidence
from .merge import MergeOutcome
from .pipeline import PipelineResult, build_extraction_input


BATCH_CHAR_BUDGET = 24000
BATCH_POST_LIMIT = 2


def summaries(store, ip):
    return [{
        'id': c.id, 'title': c.title, 'partner': c.partner,
        'activities': [{'id': a.id, 'title': a.title, 'kind': a.kind.value,
                        'start_at': a.start_at.isoformat() if a.start_at else None,
                        'end_at': a.end_at.isoformat() if a.end_at else None}
                       for a in c.activities],
    } for c in store.list_campaigns() if c.ip_slug == ip.slug]


async def process_semantic(pipeline, *, store, ip, observations, now, provider,
                           remind_day_before, suppress_immediate_for):
    counters = {key: 0 for key in PipelineResult.__dataclass_fields__}
    by_id = {o.id: o for o in observations}
    ready = []
    for selection in prepare_candidates(observations, [ip.name, *ip.aliases]):
        group = selection.group
        items = [by_id[i] for i in group.observation_ids]
        pending_id = pipeline._id('pending-ai', *group.observation_ids)
        pending = store.load_pending_extraction(pending_id)
        changed = [i for i in items if store.observation_content_hash(i.id) != i.source.content_hash]
        if not changed and pending is None:
            counters['unchanged_observations'] += len(items)
            continue
        historical = (not pending.notify_immediately) if pending else all(i.id in suppress_immediate_for for i in items)
        for item in changed:
            store.save_observation(item)
            counters['observations_saved'] += 1
        if not selection.accepted:
            store.delete_pending_extraction(pending_id)
            continue
        # Persist work before any external request. Failed merge stays retryable.
        store.save_pending_extraction(PendingExtraction(id=pending_id,
            observation_ids=list(group.observation_ids), ip_slug=ip.slug,
            queued_at=now, reason='semantic extraction pending', notify_immediately=not historical))
        ready.append((items, pending_id, historical))

    # Only historical packets from the same platform/account share a batch.
    units = []
    buckets = {}
    for entry in ready:
        item = entry[0][0]
        if entry[2]:
            buckets.setdefault((item.source.kind, item.source.account_id or item.source.account_name), []).append(entry)
        else:
            units.append([entry])
    for entries in buckets.values():
        chunk, size = [], 0
        for entry in entries:
            length = len(entry[0][0].text)
            if chunk and (size + length > BATCH_CHAR_BUDGET or len(chunk) >= BATCH_POST_LIMIT):
                units.append(chunk)
                chunk, size = [], 0
            chunk.append(entry)
            size += length
        if chunk:
            units.append(chunk)
    units.sort(key=lambda u: (u[0][0][0].source.published_at, u[0][0][0].id))

    for unit in units:
        packets = [build_extraction_input(entry[0][0], ip) for entry in unit]
        candidates = summaries(store, ip)
        try:
            if any(len(p.text) > BATCH_CHAR_BUDGET for p in packets):
                raise AIProviderError('source text exceeds extraction budget')
            if unit[0][2]:
                counters['ai_calls'] += 1
                batch = await provider.extract_batch(packets, candidates)
                groups = [(g.observation_ids, g.extraction) for g in batch.groups]
            else:
                packets[0].campaign_candidates = candidates
                counters['ai_calls'] += 1
                extraction = await provider.extract(packets[0])
                groups = [([packets[0].observation_id], extraction)]
            for ids, extraction in groups:
                members = [o for entry in unit if entry[0][0].id in ids for o in entry[0]]
                await _apply_group(pipeline, store, ip, members, extraction, now,
                                   provider, not unit[0][2], remind_day_before, counters)
            for _, pending_id, _ in unit:
                store.delete_pending_extraction(pending_id)
        except (AIProviderError, ValidationError, ValueError) as exc:
            if hasattr(provider, 'diagnostics'):
                safe_reason = str(exc) if str(exc) in {'unverified extraction quote', 'invalid batch source coverage', 'invalid campaign candidate', 'source text exceeds extraction budget'} else None
                provider.diagnostics.append({'stage': 'semantic_pipeline',
                    'exception_type': type(exc).__name__, 'reason': safe_reason})
            counters['pending_extractions'] += len(unit)
    return PipelineResult(**counters)


async def _apply_group(pipeline, store, ip, members, extraction, now, provider,
                       notify, remind_day_before, counters):
    if not extraction.relevant:
        return
    for claim in extraction.claims:
        if not any(quote_matches(claim.quote, item.text) for item in members):
            raise ValueError('unverified extraction quote')
    packets = [build_extraction_input(o, ip) for o in members]
    primary = members[0]
    draft = pipeline.assembler.assemble(packet=packets[0], observation=primary,
                                        extraction=extraction, now=now)
    draft.sources = pipeline._group_sources(members)
    # Translate extraction array paths into stable-ID paths, retaining the
    # actual quoted source date rather than the batch processing date.
    for claim in extraction.claims:
        path = claim.field_path.replace('campaign_title', 'title')
        target = draft
        segments = []
        valid = True
        for segment in path.split('.'):
            indexed = re.fullmatch(r'(activities|actions|venues)\[(\d+)\]', segment)
            if indexed:
                collection, index = indexed.group(1), int(indexed.group(2))
                values = getattr(target, collection, [])
                if index >= len(values):
                    valid = False
                    break
                target = values[index]
                segments.extend([collection, target.id])
            else:
                segments.append(segment)
        if valid and hasattr(target, segments[-1]):
            item = max((o for o in members if quote_matches(claim.quote, o.text)), key=lambda o: o.source.published_at)
            draft.fact_provenance['.'.join(segments)] = FactProvenance(
                observation_id=item.id, source_id=item.source.id,
                content_hash=item.source.content_hash, published_at=item.source.published_at, quote=claim.quote)
    candidate_id = extraction.candidate_campaign_id
    matched = store.load_campaign(candidate_id) if candidate_id else None
    if candidate_id and (matched is None or matched.ip_slug != ip.slug):
        raise ValueError('invalid campaign candidate')
    if matched:
        counters['ai_calls'] += 1
        decision = await provider.merge_campaign(matched, packets, extraction)
        if decision.decision == 'create_new':
            if decision.updates or decision.new_activities or decision.new_actions or decision.new_venues:
                raise ValueError('new campaign decision cannot contain updates')
            matched = None
        else:
            # Old data without field provenance cannot use processing time as
            # evidence priority. Conservatively guard existing values with the
            # latest available public source time until rebuilt from sources.
            for operation in decision.updates:
                if operation.field_path not in matched.fact_provenance and matched.sources:
                    cited = next((o for o in members if o.id == operation.observation_id), None)
                    if cited and cited.source.published_at < max(s.published_at for s in matched.sources):
                        from .fact_updates import _target
                        target, field = _target(matched, operation.field_path)
                        if getattr(target, field) is not None:
                            raise ValueError('older conflicting fact without provenance')
            updated = apply_fact_updates(matched, decision.updates, members, now)
            changes = []
            for operation in decision.updates:
                if updated.fact_provenance.get(operation.field_path) == matched.fact_provenance.get(operation.field_path):
                    continue
                parts = operation.field_path.split('.')
                changes.append(pipeline.merger._change(campaign=updated, detected_at=now,
                    kind=ChangeKind.SCHEDULE_CHANGED if parts[-1] in {'at', 'start_at', 'end_at'} else ChangeKind.FIELD_UPDATED,
                    activity_id=parts[1] if parts[0] == 'activities' else None,
                    summary=f'更新：{operation.field_path}',
                    semantic_suffix=f'revision-{updated.revision}-{operation.field_path}',
                    source_ids=[updated.fact_provenance[operation.field_path].source_id],
                    field_paths=[operation.field_path]))
            if decision.new_activities:
                if any(a not in extraction.activities for a in decision.new_activities):
                    raise ValueError('new activities must come from first-round extraction')
                additions = extraction.model_copy(update={'activities': decision.new_activities})
                assembled = pipeline.assembler.assemble(packet=packets[0], observation=primary, extraction=additions, now=now)
                for activity in assembled.activities:
                    if any(a.id == activity.id for a in updated.activities):
                        raise ValueError('new activity already exists')
                    updated.activities.append(activity)
                    changes.append(pipeline.merger._change(campaign=updated, activity_id=activity.id,
                        detected_at=now, kind=ChangeKind.NEW_ACTIVITY, summary=f'新增活动：{activity.title}',
                        semantic_suffix=f'new-activity-{activity.id}'))
            for collection, additions, model, value_field in [
                ('actions', decision.new_actions, EventAction, 'action'),
                ('venues', decision.new_venues, Venue, 'venue'),
            ]:
                for addition in additions:
                    activity = next((a for a in updated.activities if a.id == addition.activity_id), None)
                    item = next((o for o in members if o.id == addition.observation_id), None)
                    if activity is None or item is None or not quote_matches(addition.quote, item.text):
                        raise ValueError('invalid addition target or evidence')
                    value = getattr(addition, value_field)
                    identity = f'{activity.id}:{collection}:{item.id}:{value.model_dump_json()}'
                    entity_id = collection + '-' + hashlib.sha256(identity.encode()).hexdigest()[:16]
                    entities = getattr(activity, collection)
                    if any(e.id == entity_id for e in entities):
                        continue
                    entity = model(id=entity_id, **value.model_dump(), evidence=[Evidence(
                        source_id=item.source.id, field_path=f'activities.{activity.id}.{collection}.{entity_id}', quote=addition.quote)])
                    entities.append(entity)
                    changes.append(pipeline.merger._change(campaign=updated, activity_id=activity.id,
                        detected_at=now, kind=ChangeKind.FIELD_ADDED,
                        summary=f'新增：{getattr(value, "title", None) or getattr(value, "name", None) or collection}',
                        semantic_suffix=f'entity-added-{entity_id}', source_ids=[item.source.id]))
            added = pipeline.merger._merge_sources(updated.sources, draft.sources)
            for activity in updated.activities:
                for start, end in [(activity.start_at, activity.end_at), *((a.at, a.end_at) for a in activity.actions)]:
                    if start and end and end < start:
                        raise ValueError('invalid merged time interval')
            if changes or added:
                updated.revision = matched.revision + 1
                updated.updated_at = now
            outcome = MergeOutcome(updated, tuple(changes), ())
    if matched is None:
        for activity in draft.activities:
            if activity.start_at and activity.end_at and activity.end_at < activity.start_at:
                raise ValueError('invalid extracted time interval')
        # New editions must not overwrite a same-title stored campaign.
        seed = '\x1f'.join(sorted(o.id for o in members))
        draft.id += '-' + hashlib.sha256(seed.encode()).hexdigest()[:8]
        previous = store.load_campaign(draft.id)
        if previous is not None:
            return  # A completed group replay after a later group failed.
        outcome = pipeline.merger.merge(None, draft, now)
    store.save_campaign(outcome.campaign)
    counters['campaigns_created' if matched is None else 'campaigns_updated'] += 1
    for change in outcome.changes:
        store.save_change(change)
        counters['changes_written'] += 1
    if notify:
        for job in pipeline.scheduler.jobs_for_changes(list(outcome.material_changes)):
            store.save_queue_job(job)
            counters['jobs_written'] += 1
    jobs = pipeline.scheduler.reconcile_campaign(store, outcome.campaign, now,
                                                remind_day_before=remind_day_before)
    counters['jobs_written'] += len(jobs)
