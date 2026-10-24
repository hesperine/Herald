"""Durable two-pass plans: public evidence -> campaign -> stable entity updates."""
import hashlib
import json
from pydantic import ValidationError
from .ai import AIProviderError
from .candidate_stage import prepare_candidates
from .contextual_ai import RoutingResult
from .models import PendingExtraction
from .pipeline import PipelineResult, build_extraction_input
from .retry import record_failure
from .semantic_pipeline import _apply_group
from .merge import normalize_name


async def process_contextual(pipeline, *, store, ip, observations, now, provider, remind_day_before):
    counters = {key: 0 for key in PipelineResult.__dataclass_fields__}
    by_id = {o.id: o for o in observations}
    ready = []
    for selection in prepare_candidates(observations, [ip.name, *ip.aliases]):
        members = [by_id[i] for i in selection.group.observation_ids]
        pending_id = pipeline._id('pending-ai', *selection.group.observation_ids)
        pending = store.load_pending_extraction(pending_id)
        group_ids = set(selection.group.observation_ids)
        aliases = [p for p in store.list_pending_extractions() if p.ip_slug == ip.slug
                   and p.id != pending_id and set(p.observation_ids).issubset(group_ids)]
        if pending is None and aliases:
            pending = aliases[0].model_copy(update={'id': pending_id,
                                                   'observation_ids': list(selection.group.observation_ids)})
        changed = [o for o in members if store.observation_content_hash(o.id) != o.source.content_hash]
        for item in changed:
            store.save_observation(item)
            counters['observations_saved'] += 1
        if not selection.accepted:
            store.delete_pending_extraction(pending_id)
            for alias in aliases:
                store.delete_pending_extraction(alias.id)
            continue
        if not changed and pending is None:
            counters['unchanged_observations'] += len(members)
            continue
        if not changed and pending and pending.next_retry_at and pending.next_retry_at > now:
            counters['pending_extractions'] += 1
            continue
        pending = pending if pending and not changed else PendingExtraction(id=pending_id,
            observation_ids=[o.id for o in members], ip_slug=ip.slug, queued_at=now, reason='contextual extraction pending')
        store.save_pending_extraction(pending)
        for alias in aliases:
            store.delete_pending_extraction(alias.id)
        tokens = {(o.source.id, o.source.content_hash) for o in members}
        for notice in store.list_candidates():
            if notice.ip_slug == ip.slug and tokens.intersection((s.id, s.content_hash) for s in notice.sources):
                notice.resolution_version = 2
                if not notice.campaign_ids:
                    notice.status = 'pending'
                elif notice.campaign_id:
                    notice.status = 'structured'
                store.save_candidate(notice)
        ready.append((members, pending_id))
    # Whole source groups stay together; bounded batches across official accounts.
    units, unit, size = [], [], 0
    for entry in sorted(ready, key=lambda e: (e[0][0].source.published_at, e[0][0].id)):
        length = sum(len(o.text) for o in entry[0])
        if unit and (size + length > 60000 or len(unit) >= 24):
            units.append(unit)
            unit, size = [], 0
        unit.append(entry)
        size += length
    if unit:
        units.append(unit)
    for unit in units:
        members = {o.id: o for entry in unit for o in entry[0]}
        signature = hashlib.sha256(json.dumps(sorted((o.id, o.source.content_hash) for o in members.values())).encode()).hexdigest()
        path = store.root / 'contextual-plans' / (signature + '.json')
        try:
            if path.exists():
                plan = json.loads(path.read_text(encoding='utf-8'))
            else:
                catalog = [{'id': c.id, 'phrase': c.title, 'partner': c.partner}
                           for c in store.list_campaigns() if c.ip_slug == ip.slug]
                counters['ai_calls'] += 1
                routes = await provider.route_campaigns([build_extraction_input(o, ip) for o in members.values()], catalog)
                plan = {'routes': routes.model_dump(mode='json'), 'completed': {}}
                store._atomic_json_write(path, plan)
            routes = RoutingResult.model_validate(plan['routes'])
            for index, route in enumerate(routes.groups):
                if str(index) in plan['completed']:
                    continue
                existing = store.load_campaign(route.campaign_id) if route.campaign_id else None
                if route.campaign_id and (not existing or existing.ip_slug != ip.slug):
                    raise ValueError('invalid campaign reference')
                if existing is None and normalize_name(route.partner):
                    existing = next((c for c in store.list_campaigns() if c.ip_slug == ip.slug
                        and normalize_name(c.partner) == normalize_name(route.partner)), None)
                selected = [members[i] for i in route.observation_ids]
                counters['ai_calls'] += 1
                resolution = await provider.resolve_campaign(route,
                    [build_extraction_input(o, ip) for o in selected], existing)
                resolution.extraction.candidate_campaign_id = existing.id if existing else None
                cid = await _apply_group(pipeline, store, ip, selected, resolution.extraction, now,
                    provider, True, remind_day_before, counters, resolution=resolution, group_key=route.phrase)
                plan['completed'][str(index)] = cid
                store._atomic_json_write(path, plan)
            # Associate only after every group succeeded: partial failure stays fallback.
            for notice in store.list_candidates():
                tokens = {(s.id, s.content_hash) for s in notice.sources}
                supplied = {o.id for o in members.values() if (o.source.id, o.source.content_hash) in tokens}
                if notice.ip_slug != ip.slug or not supplied:
                    continue
                ids = list(dict.fromkeys(plan['completed'][str(i)] for i, route in enumerate(routes.groups)
                    if supplied.intersection(route.observation_ids) and plan['completed'][str(i)]))
                notice.campaign_ids = ids
                notice.campaign_id = ids[0] if ids else None
                notice.resolution_version = 2
                notice.status = 'structured' if ids else 'unconfirmed'
                notice.review_reason = next((u.reason for u in routes.unconfirmed if u.observation_id in supplied), None)
                store.save_candidate(notice)
            for _, pending_id in unit:
                store.delete_pending_extraction(pending_id)
        except (AIProviderError, ValidationError, ValueError, KeyError) as error:
            safe_reasons = {'date requires field evidence', 'invalid scope exclusion evidence',
                'scope exclusion must refer to legacy activity', 'older conflicting fact without provenance',
                'unverified extraction quote', 'context budget exceeded', 'conflicting contextual partner',
                'new activity already exists', 'new activity must identify one extracted activity',
                'same-time conflicting evidence requires review', 'invalid addition target or evidence',
                'existing campaign requires reconciliation', 'invalid fact target', 'field is not editable',
                'fact target must identify one existing entity', 'null is not a deletion instruction',
                'invalid campaign reference', 'date requires field evidence'}
            code = str(error) if str(error) in safe_reasons else type(error).__name__
            for _, pending_id in unit:
                record_failure(store, store.load_pending_extraction(pending_id), now,
                               error)
                pending = store.load_pending_extraction(pending_id)
                if getattr(error, 'category', None) != 'deferred':
                    store.save_pending_extraction(pending.model_copy(update={'reason': 'contextual: ' + code}))
            counters['pending_extractions'] += len(unit)
    return PipelineResult(**counters)
