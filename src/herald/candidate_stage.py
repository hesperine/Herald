"""Shared production boundary: source deduplication followed by rule filtering."""
from dataclasses import dataclass

from .candidates import CandidateFilter
from .dedupe import ObservationDeduplicator, ObservationGroup
from .models import SourceObservation


@dataclass(frozen=True)
class CandidateGroup:
    group: ObservationGroup
    accepted: bool


def prepare_candidates(observations: list[SourceObservation], ip_names: list[str]) -> list[CandidateGroup]:
    by_id = {o.id: o for o in observations}
    rule = CandidateFilter()
    return [CandidateGroup(group, any(rule.evaluate(by_id[i], ip_names=ip_names,
        from_official_ip_account=by_id[i].source.is_official).relevant
        for i in group.observation_ids)) for group in ObservationDeduplicator().group(observations)]
