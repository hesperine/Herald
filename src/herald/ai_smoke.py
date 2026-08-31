"""Run one real AI extraction against a fixed public historical sample."""

from __future__ import annotations

import asyncio
import json
import sys

import httpx

from .ai import AIProviderError, ExtractionInput, ExtractionResult
from .cli import _make_provider
from .config import load_settings


SAMPLE_NAME = "genshin-meituan-dianping-2026-08"


def historical_collaboration_packet() -> ExtractionInput:
    return ExtractionInput(
        observation_id="weibo-historical-collaboration",
        platform="weibo",
        account_name="原神",
        published_at="2026-08-16T12:00:00+08:00",
        text=(
            "原神 × 美团丨大众点评联名活动正式开启："
            "2026年8月16日美团快闪预约开启，"
            "8月19日大众点评打卡活动开启，"
            "8月21日美团原神专属会场开启。"
        ),
        source_url="https://weibo.com/6593199887/RdDIG4Sea",
        ip_slug_hint="genshin-impact",
        ip_name_hint="原神",
    )


def evaluate_historical_result(
    packet: ExtractionInput, result: ExtractionResult
) -> dict[str, bool]:
    campaign_identity = " ".join(
        item for item in [result.campaign_title, result.partner] if item
    )
    all_activities = result.activities
    no_invented_times = all(
        activity.start_at is None
        and activity.end_at is None
        and all(
            action.at is None and action.end_at is None
            for action in activity.actions
        )
        for activity in all_activities
    )
    no_invented_venues = all(not activity.venues for activity in all_activities)
    no_invented_action_urls = all(
        action.url is None
        for activity in all_activities
        for action in activity.actions
    )
    return {
        "relevant": result.relevant,
        "campaign_entities": all(
            name in campaign_identity for name in ("原神", "美团", "大众点评")
        ),
        "three_activities": len(all_activities) == 3,
        "no_invented_times": no_invented_times,
        "no_invented_venues": no_invented_venues,
        "no_invented_action_urls": no_invented_action_urls,
        "source_quotes": bool(result.claims)
        and all(claim.quote in packet.text for claim in result.claims),
        "missing_time_of_day_reported": any(
            any(marker in uncertainty for marker in ("具体时刻", "具体时间点", "小时"))
            for uncertainty in result.uncertainties
        ),
    }


async def run_smoke_test() -> dict[str, object]:
    settings = load_settings()
    packet = historical_collaboration_packet()
    async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
        provider = _make_provider(settings, client)
        if provider is None:
            raise ValueError("AI_MODEL and AI_API_KEY are required for AI smoke test")
        result = await provider.extract(packet)

    checks = evaluate_historical_result(packet, result)
    return {
        "mode": "ai-extraction-smoke",
        "sample": SAMPLE_NAME,
        "sample_has_image": bool(packet.media_urls),
        "provider": provider.provider_name,
        "model": provider.model_name,
        "passed": all(checks.values()),
        "checks": checks,
        "result": result.model_dump(mode="json"),
    }


def main() -> int:
    try:
        report = asyncio.run(run_smoke_test())
    except (AIProviderError, OSError, ValueError) as exc:
        print(f"ai-smoke: {exc}", file=sys.stderr)
        return 2
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
