"""Collect bounded public Weibo history for human-reviewed AI samples."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import httpx

from .candidates import CandidateFilter
from .models import SourceKind
from .pipeline import build_extraction_input
from .registry import IpRegistry
from .sources.base import SourceAccessError
from .sources.weibo import MOBILE_API, WeiboTimelineClient


CHINA_TIME = timezone(timedelta(hours=8))


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect bounded public official-account history for review."
    )
    parser.add_argument("--lookback-days", type=int, default=45)
    parser.add_argument("--start-date", type=_iso_date)
    parser.add_argument("--end-date", type=_iso_date)
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _resolve_date_window(
    args: argparse.Namespace, *, now: datetime
) -> tuple[datetime, datetime]:
    has_start = args.start_date is not None
    has_end = args.end_date is not None
    if has_start != has_end:
        raise ValueError("--start-date and --end-date must be provided together")
    if has_start:
        if args.end_date < args.start_date:
            raise ValueError("--end-date must be on or after --start-date")
        published_from = datetime.combine(
            args.start_date, time.min, tzinfo=CHINA_TIME
        )
        published_before = datetime.combine(
            args.end_date + timedelta(days=1), time.min, tzinfo=CHINA_TIME
        )
        return published_from, published_before
    if args.lookback_days < 1:
        raise ValueError("--lookback-days must be at least 1")
    return now - timedelta(days=args.lookback_days), now


def _sample_published_at(item: dict[str, object]) -> str:
    packet = item.get("extraction_input")
    if not isinstance(packet, dict):
        raise ValueError("sample record is missing extraction_input")
    return str(packet["published_at"])


async def collect(args: argparse.Namespace) -> dict[str, object]:
    if args.max_pages < 1:
        raise ValueError("--max-pages must be at least 1")

    now = datetime.now(timezone.utc)
    published_from, published_before = _resolve_date_window(args, now=now)
    cookie = os.environ.get("WEIBO_COOKIE", "").strip() or None
    registry = IpRegistry.load_builtin()
    candidate_filter = CandidateFilter()
    public_items: list[dict[str, object]] = []
    warnings: list[str] = []
    fetched_count = 0

    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as http_client:
        timeline = WeiboTimelineClient(http_client, cookie=cookie)
        for ip in registry.entries:
            for source in ip.sources:
                if source.kind != SourceKind.WEIBO:
                    continue
                try:
                    fetched = await timeline.fetch_history(
                        account_id=source.account_id,
                        account_name=source.account_name,
                        first_seen_at=now,
                        published_from=published_from,
                        published_before=published_before,
                        max_pages=args.max_pages,
                    )
                except SourceAccessError:
                    warnings.append(f"official source fetch failed: {source.account_id}")
                    continue
                fetched_count += len(fetched)
                for item in fetched:
                    observation = item.observation
                    packet = build_extraction_input(observation, ip)
                    decision = candidate_filter.evaluate(
                        observation,
                        ip_names=[ip.name, *ip.aliases],
                        from_official_ip_account=True,
                    )
                    public_items.append(
                        {
                            "account_id": source.account_id,
                            "extraction_input": packet.model_dump(mode="json"),
                            "candidate": {
                                "relevant": decision.relevant,
                                "score": decision.score,
                                "matched_terms": list(decision.matched_terms),
                            },
                        }
                    )

    public_items.sort(key=_sample_published_at, reverse=True)
    payload = {
        "schema_version": 2,
        "generated_at": now.isoformat(),
        "published_from": published_from.isoformat(),
        "published_before": published_before.isoformat(),
        "lookback_days": (
            args.lookback_days if args.start_date is None else None
        ),
        "source_transport": MOBILE_API,
        "source_url_policy": "canonical https://weibo.com/{account_id}/{bid}",
        "items": public_items,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "accounts": sum(len(ip.sources) for ip in registry.entries),
        "fetched": fetched_count,
        "candidates": sum(
            1 for item in public_items if item["candidate"]["relevant"]
        ),
        "output": str(args.output),
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = asyncio.run(collect(args))
    except (OSError, ValueError) as exc:
        print(f"weibo-samples: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
