"""Cheap rule-based filtering before any AI call."""

from __future__ import annotations

import re
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


def _named_crossovers(material: str, ip_names: list[str]) -> set[str]:
    """Recognize same-line name pairs without treating quantities as partners."""
    decorations = re.escape(" \t\u3000《》「」『』【】（）()[]“”‘’\"'@")
    edge = rf"[{decorations}]*"
    # A partner must start with a letter; the left boundary also excludes
    # matching only the unit suffix of a quantity such as '800份'.
    partner = r"[^\W\d_][\w·・&.'’\-]*"
    space = r"[ \t\u3000]"
    separator = (
        rf"(?:{space}*×{space}*|{space}+[xｘ]{space}+|"
        r"(?<=[\u3400-\u9fff])[xｘ](?=[\u3400-\u9fff]))"
    )
    matches: set[str] = set()
    for name in ip_names:
        if not name.strip():
            continue
        escaped_name = re.escape(name.strip().casefold())
        patterns = (
            rf"(?<![a-z0-9_]){escaped_name}{edge}{separator}{edge}{partner}",
            rf"(?<!\w){partner}{edge}{separator}{edge}{escaped_name}(?![a-z0-9_])",
        )
        for pattern in patterns:
            matches.update(match.group() for match in re.finditer(pattern, material))
    return matches


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
            {term for term in COLLABORATION_TERMS | OFFLINE_EVENT_TERMS if term.casefold() in material}
            | _named_crossovers(material, ip_names)
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
