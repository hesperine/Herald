"""Command-line entry point used locally and by GitHub Actions."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .ai import AIProvider, OpenAICompatibleProvider, ZhipuOpenAIProvider
from .config import RuntimeSettings, load_settings
from .media import PublicMediaCache
from .notifications import EmailSender, SmtpEmailSender
from .runner import DailyRunner, RunPhase
from .sources.miyoushe import MiyousheTimelineClient
from .sources.skland import SklandTimelineClient
from .sources.weibo import WeiboTimelineClient
from .storage import StateStore


def _secret(value: object | None) -> str | None:
    if value is None:
        return None
    getter = getattr(value, "get_secret_value", None)
    raw = getter() if callable(getter) else str(value)
    normalized = raw.strip()
    return normalized or None


def _parse_now(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("--now must include a timezone offset")
    return parsed


def _make_provider(
    settings: RuntimeSettings, client: httpx.AsyncClient
) -> AIProvider | None:
    if not settings.ai_enabled:
        return None
    api_key = _secret(settings.private.ai_api_key)
    if api_key is None or settings.public.ai_model is None:
        return None
    provider_type = (
        ZhipuOpenAIProvider
        if settings.public.ai_provider == "zhipu_openai"
        else OpenAICompatibleProvider
    )
    return provider_type(
        client=client,
        base_url=settings.public.ai_base_url,
        model=settings.public.ai_model,
        api_key=api_key,
        supports_json_object=settings.public.ai_json_mode,
    )


def _make_email_sender(settings: RuntimeSettings) -> EmailSender | None:
    username = _secret(settings.private.smtp_username)
    password = _secret(settings.private.smtp_password)
    if not settings.public.smtp_host or not username or not password:
        return None
    return SmtpEmailSender(
        host=settings.public.smtp_host,
        port=settings.public.smtp_port,
        username=username,
        password=password,
        use_ssl=settings.public.smtp_use_ssl,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="herald",
        description="Run one official-source scan and publish the static result.",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path(".herald-state"),
        help="checked-out state branch directory",
    )
    parser.add_argument(
        "--page-dir",
        type=Path,
        default=Path(".herald-page"),
        help="checked-out page branch directory",
    )
    parser.add_argument(
        "--now",
        help="timezone-aware ISO timestamp for deterministic local runs",
    )
    parser.add_argument(
        "--phase",
        choices=[phase.value for phase in RunPhase],
        default=RunPhase.FULL.value,
        help="fetch sources only, extract queued materials only, or run everything",
    )
    return parser


async def _run(args: argparse.Namespace) -> dict[str, object]:
    settings = load_settings()
    now = _parse_now(args.now)
    cookie = _secret(settings.private.weibo_cookie)
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as source_client:
        async with httpx.AsyncClient(follow_redirects=True, timeout=60) as ai_client:
            result = await DailyRunner().run(
                settings=settings,
                store=StateStore(args.state_dir),
                page_dir=args.page_dir,
                now=now,
                weibo_client=WeiboTimelineClient(source_client, cookie=cookie),
                miyoushe_client=MiyousheTimelineClient(source_client),
                skland_client=SklandTimelineClient(source_client),
                provider=_make_provider(settings, ai_client),
                email_sender=_make_email_sender(settings),
                media_cache=PublicMediaCache(source_client),
                phase=RunPhase(args.phase),
            )
    return {
        "phase": args.phase,
        "report": result.report.model_dump(mode="json"),
        "published_campaigns": result.published_campaigns,
        "ai_calls": sum(item.ai_calls for item in result.pipeline_results),
        "pending_extractions": sum(
            item.pending_extractions for item in result.pipeline_results
        ),
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = asyncio.run(_run(args))
    except (ValueError, OSError) as exc:
        print(f"herald: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0
