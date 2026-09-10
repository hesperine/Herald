"""Validated, evidence-backed scalar updates; no filesystem or model calls.

Paths address stable entity IDs, never array offsets. New entities are handled
separately by the assembler. Missing fields are not deletion instructions.
"""

from datetime import datetime
from typing import Any

from pydantic import Field

from .models import Campaign, FactProvenance, SourceObservation, StrictModel


class FactUpdate(StrictModel):
    field_path: str
    value: Any
    observation_id: str
    quote: str = Field(min_length=1, max_length=1000)


FIELDS = {
    'campaign': {'title', 'partner', 'announced_at', 'status'},
    'activities': {'title', 'kind', 'start_at', 'end_at', 'status'},
    'actions': {'title', 'kind', 'at', 'end_at', 'platform', 'url', 'requires_reservation', 'requires_rush', 'cancelled'},
    'venues': {'name', 'country', 'province', 'city', 'address', 'online_platform', 'business_hours', 'nationwide'},
}


def _target(campaign: Campaign, path: str):
    parts = path.split('.')
    target = campaign
    scope = 'campaign'
    while len(parts) > 1:
        collection, identifier, *parts = parts
        allowed = {'activities'} if scope == 'campaign' else {'actions', 'venues'} if scope == 'activities' else set()
        if collection not in allowed:
            raise ValueError('invalid fact target')
        matches = [item for item in getattr(target, collection) if item.id == identifier]
        if len(matches) != 1:
            raise ValueError('fact target must identify one existing entity')
        target, scope = matches[0], collection
    if not parts or parts[0] not in FIELDS[scope]:
        raise ValueError('field is not editable')
    return target, parts[0]


def apply_fact_updates(campaign: Campaign, updates: list[FactUpdate], observations: list[SourceObservation], now: datetime) -> Campaign:
    """Return a validated copy, or fail atomically without changing the input.

Publication time is a conflict guard, not proof that the model interpreted a
correction correctly. Quote checks establish provenance, not semantic truth.
"""
    result = campaign.model_copy(deep=True)
    materials = {item.id: item for item in observations}
    seen = set()
    changed = False
    for update in updates:
        if update.field_path in seen:
            raise ValueError('duplicate field operation')
        seen.add(update.field_path)
        material = materials.get(update.observation_id)
        from .evidence import quote_matches
        if material is None or not quote_matches(update.quote, material.text):
            raise ValueError('fact must cite supplied original text')
        if update.value is None:
            raise ValueError('null is not a deletion instruction')
        target, field = _target(result, update.field_path)
        if field == 'status' and update.value not in {'cancelled', 'ended', 'announced'}:
            raise ValueError('only explicit official status is editable')
        old_value = getattr(target, field)
        # Validate/coerce using the target model before comparing values.
        candidate = target.model_copy(deep=True)
        setattr(candidate, field, update.value)
        value = getattr(candidate, field)
        if old_value == value:
            continue
        previous = result.fact_provenance.get(update.field_path)
        if old_value is not None and previous:
            if material.source.published_at < previous.published_at:
                continue
            if material.source.published_at == previous.published_at:
                raise ValueError('same-time conflicting evidence requires review')
        setattr(target, field, value)
        result.fact_provenance[update.field_path] = FactProvenance(
            observation_id=material.id, source_id=material.source.id,
            content_hash=material.source.content_hash,
            published_at=material.source.published_at, quote=update.quote,
        )
        if not any(source.id == material.source.id for source in result.sources):
            result.sources.append(material.source.model_copy(deep=True))
        changed = True
    for activity in result.activities:
        for start, end in [(activity.start_at, activity.end_at), *((a.at, a.end_at) for a in activity.actions)]:
            if start is not None and end is not None and end < start:
                raise ValueError('end precedes start')
    if changed:
        result.revision += 1
        result.updated_at = now
    return Campaign.model_validate(result.model_dump())
