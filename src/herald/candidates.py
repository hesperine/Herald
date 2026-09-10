"""Cheap rule-based filtering before any AI call."""

from __future__ import annotations

from dataclasses import dataclass

from .models import SourceObservation


COLLABORATION_TERMS = {
    "联动",
    "联名",
    "合作",
    "合作企划",
    "主题活动",
    "主题店",
    "快闪",
    "限定套餐",
}
ACTION_TERMS = {
    "预约",
    "开售",
    "发售",
    "抢购",
    "抽签",
    "取号",
    "门店",
    "周边",
    "巡展",
    "补货",
}

OFFLINE_EVENT_TERMS = {
    '漫展', '嘉年华', '演唱会', '音乐会', '巡演', '巡展',
    '主题展', '线下活动', '线下展会', '线下演出', '见面会',
}


@dataclass(frozen=True, slots=True)
class CandidateDecision:
    relevant: bool
    score: int
    matched_terms: tuple[str, ...]
    reason: str


class CandidateFilter:
    def evaluate(
        self,
        observation: SourceObservation,
        *,
        ip_names: list[str],
        from_official_ip_account: bool,
    ) -> CandidateDecision:
        material = "\n".join(
            [observation.text, *observation.extracted_media_text]
        ).casefold()
        matched_collaboration = sorted(
            term for term in COLLABORATION_TERMS | OFFLINE_EVENT_TERMS if term.casefold() in material
        )
        matched_actions = sorted(term for term in ACTION_TERMS if term.casefold() in material)
        matched_ips = sorted(name for name in ip_names if name.casefold() in material)

        score = 0
        if matched_collaboration:
            score += 3
        if matched_actions:
            score += 1
        if matched_ips:
            score += 2
        if from_official_ip_account:
            score += 1

        relevant = bool(matched_collaboration) and (
            from_official_ip_account or bool(matched_ips)
        )
        terms = tuple([*matched_collaboration, *matched_actions, *matched_ips])
        reason = (
            "collaboration or offline event signal for the configured IP"
            if relevant
            else "no reliable collaboration or offline event signal"
        )
        return CandidateDecision(relevant, score, terms, reason)
