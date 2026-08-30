"""Independent official-account timeline adapter for mobile Weibo JSON.

The endpoint is isolated behind this module because it is unofficial and may
change. The implementation accepts an injected ``httpx.AsyncClient`` so all
parsing and cursor behavior can be tested without network access.
"""

from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from herald.models import SourceKind, SourceObservation, SourceRef
from herald.sources.base import FetchBatch, FetchedObservation, SourceAccessError


TAG_PATTERN = re.compile(r"<[^>]+>")
MOBILE_API = "https://m.weibo.cn/api/container/getIndex"


class WeiboCursor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    container_id: str | None = None
    latest_post_id: str | None = None


def _clean_text(value: str) -> str:
    return html.unescape(TAG_PATTERN.sub("", value)).strip()


def _parse_created_at(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%a %b %d %H:%M:%S %z %Y")
    except ValueError as exc:
        raise SourceAccessError("Weibo returned an unsupported publication time") from exc


def _valid_http_urls(values: list[str]) -> list[str]:
    return [value for value in values if value.startswith(("https://", "http://"))]


def _media(mblog: dict[str, Any]) -> tuple[list[str], list[str]]:
    urls: list[str] = []
    tokens: list[str] = []
    for picture in mblog.get("pics") or []:
        if not isinstance(picture, dict):
            continue
        large = picture.get("large")
        value = (large or {}).get("url") if isinstance(large, dict) else None
        if isinstance(value, str) and value.startswith(("https://", "http://")):
            urls.append(value)
        token = picture.get("pid") or value
        if token:
            tokens.append(str(token))
    return urls, tokens


def _outbound_urls(mblog: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for url_item in mblog.get("url_struct") or []:
        if not isinstance(url_item, dict):
            continue
        value = url_item.get("long_url") or url_item.get("url_title")
        if isinstance(value, str):
            urls.append(value)
    page_info = mblog.get("page_info")
    if isinstance(page_info, dict) and isinstance(page_info.get("page_url"), str):
        urls.append(page_info["page_url"])
    return _valid_http_urls(urls)


class WeiboTimelineClient:
    def __init__(self, client: httpx.AsyncClient, cookie: str | None = None) -> None:
        self.client = client
        self.cookie = cookie

    async def fetch_account(
        self,
        *,
        account_id: str,
        account_name: str,
        cursor: WeiboCursor | None,
        first_seen_at: datetime,
        published_since: datetime,
    ) -> FetchBatch[WeiboCursor]:
        current_cursor = cursor or WeiboCursor()
        container_id = current_cursor.container_id or await self._resolve_container(
            account_id
        )
        parameters: dict[str, str] = {"containerid": container_id}
        payload = await self._get_json(parameters)

        data = payload.get("data") or {}
        cards = data.get("cards") or []
        fetched: list[FetchedObservation] = []
        encountered: list[FetchedObservation] = []
        for card in cards:
            if card.get("card_type") != 9 or not isinstance(card.get("mblog"), dict):
                continue
            item = self._parse_mblog(
                card["mblog"],
                account_id=account_id,
                account_name=account_name,
                first_seen_at=first_seen_at,
            )
            encountered.append(item)
            if item.observation.source.published_at < published_since:
                if item.observation.id == f"weibo-{current_cursor.latest_post_id}":
                    break
                continue
            fetched.append(item)
            if item.observation.id == f"weibo-{current_cursor.latest_post_id}":
                # Include the boundary once so a same-ID edit is still detected.
                break

        fetched.sort(
            key=lambda item: item.observation.source.published_at, reverse=True
        )
        newest = max(
            encountered,
            key=lambda item: item.observation.source.published_at,
            default=None,
        )
        return FetchBatch(
            items=tuple(fetched),
            cursor=WeiboCursor(
                container_id=container_id,
                latest_post_id=(
                    newest.observation.id.removeprefix("weibo-")
                    if newest is not None
                    else current_cursor.latest_post_id
                ),
            ),
        )

    async def _resolve_container(self, account_id: str) -> str:
        payload = await self._get_json({"type": "uid", "value": account_id})
        tabs = ((payload.get("data") or {}).get("tabsInfo") or {}).get("tabs") or []
        for tab in tabs:
            if tab.get("tab_type") == "weibo" and tab.get("containerid"):
                return str(tab["containerid"])
        raise SourceAccessError("Weibo account timeline container was not found")

    async def _get_json(self, parameters: dict[str, str]) -> dict[str, Any]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://m.weibo.cn/",
            "User-Agent": "Mozilla/5.0 HERALD/0.1",
        }
        if self.cookie:
            headers["Cookie"] = self.cookie
        try:
            response = await self.client.get(MOBILE_API, params=parameters, headers=headers)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceAccessError("Weibo request failed") from exc
        if not isinstance(payload, dict) or payload.get("ok") != 1:
            raise SourceAccessError("Weibo returned an unavailable response")
        return payload

    @staticmethod
    def _parse_mblog(
        mblog: dict[str, Any],
        *,
        account_id: str,
        account_name: str,
        first_seen_at: datetime,
    ) -> FetchedObservation:
        post_id = str(mblog.get("id") or "")
        bid = str(mblog.get("bid") or post_id)
        if not post_id:
            raise SourceAccessError("Weibo post is missing an id")
        raw_text = str(mblog.get("text_raw") or mblog.get("text") or "")
        text_parts = [_clean_text(raw_text)]
        published_at = _parse_created_at(str(mblog.get("created_at") or ""))
        repost = mblog.get("retweeted_status")
        canonical_content_id = (
            str(repost.get("id")) if isinstance(repost, dict) and repost.get("id") else post_id
        )

        media_urls, media_tokens = _media(mblog)
        outbound_urls = _outbound_urls(mblog)
        if isinstance(repost, dict):
            repost_text = _clean_text(
                str(repost.get("text_raw") or repost.get("text") or "")
            )
            if repost_text:
                text_parts.append(f"转发内容：{repost_text}")
            repost_urls, repost_tokens = _media(repost)
            media_urls.extend(repost_urls)
            media_tokens.extend(repost_tokens)
            outbound_urls.extend(_outbound_urls(repost))

        text = "\n".join(part for part in text_parts if part)
        media_urls = list(dict.fromkeys(media_urls))
        outbound_urls = list(dict.fromkeys(outbound_urls))
        media_hashes = [
            hashlib.sha256(token.encode("utf-8")).hexdigest()
            for token in dict.fromkeys(media_tokens)
        ]

        content_material = "\n".join([text, canonical_content_id, *media_urls])
        content_hash = hashlib.sha256(content_material.encode("utf-8")).hexdigest()
        source_id = f"weibo-{post_id}"
        observation = SourceObservation(
            id=source_id,
            source=SourceRef(
                id=source_id,
                kind=SourceKind.WEIBO,
                url=f"https://weibo.com/{account_id}/{bid}",
                account_name=account_name,
                account_id=account_id,
                canonical_content_id=canonical_content_id,
                published_at=published_at,
                first_seen_at=first_seen_at,
                content_hash=content_hash,
                excerpt=text[:500],
            ),
            text=text,
            media_urls=media_urls,
            media_hashes=media_hashes,
            outbound_urls=outbound_urls,
        )
        return FetchedObservation(observation=observation, media_urls=tuple(media_urls))
