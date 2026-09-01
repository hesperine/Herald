"""Cookie-free official-account adapter for Miyoushe public posts.

The author timeline is used only to discover post ids. Every in-range item is
expanded through ``getPostFull`` before it becomes a source observation so a
truncated list excerpt can never be mistaken for the complete announcement.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from herald.models import SourceKind, SourceObservation, SourceRef
from herald.sources.base import FetchBatch, FetchedObservation, SourceAccessError


TIMELINE_API = "https://bbs-api.miyoushe.com/painter/wapi/userPostList"
DETAIL_API = "https://bbs-api.miyoushe.com/post/wapi/getPostFull"
SUBSITE_PATTERN = re.compile(r"^https://www\.miyoushe\.com/([^/]+)/")


class MiyousheCursor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latest_post_id: str | None = None


def _http_urls(values: list[str]) -> list[str]:
    return list(
        dict.fromkeys(
            value for value in values if value.startswith(("https://", "http://"))
        )
    )


def _timestamp(value: object) -> datetime:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError) as exc:
        raise SourceAccessError(
            "Miyoushe returned an unsupported publication time"
        ) from exc


def _post_object(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _post_id(item: dict[str, Any]) -> str:
    post = _post_object(item.get("post"))
    return str(post.get("post_id") or item.get("post_id") or "")


def _post_time(item: dict[str, Any]) -> datetime:
    post = _post_object(item.get("post"))
    return _timestamp(post.get("created_at") or item.get("created_at"))


def _decode_content(post: dict[str, Any]) -> tuple[str, list[str], list[str]]:
    """Return plain text, embedded image URLs, and public outbound links."""

    structured = post.get("structured_content")
    if isinstance(structured, str) and structured.strip():
        try:
            nodes = json.loads(structured)
        except ValueError as exc:
            raise SourceAccessError("Miyoushe post content could not be decoded") from exc
        if not isinstance(nodes, list):
            raise SourceAccessError("Miyoushe post content has an unsupported shape")

        text_parts: list[str] = []
        images: list[str] = []
        outbound: list[str] = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            insert = node.get("insert")
            attributes = node.get("attributes")
            if isinstance(insert, str):
                text_parts.append(insert)
                if isinstance(attributes, dict) and isinstance(
                    attributes.get("link"), str
                ):
                    outbound.append(attributes["link"])
            elif isinstance(insert, dict):
                for key in ("image", "image_url"):
                    value = insert.get(key)
                    if isinstance(value, str):
                        images.append(value)
                for key in ("link", "url"):
                    value = insert.get(key)
                    if isinstance(value, str):
                        outbound.append(value)
        return "".join(text_parts).strip(), _http_urls(images), _http_urls(outbound)

    legacy = post.get("content")
    if not isinstance(legacy, str) or not legacy.strip():
        return "", [], []
    try:
        decoded = json.loads(legacy)
    except ValueError:
        return legacy.strip(), [], []
    if isinstance(decoded, dict) and isinstance(decoded.get("describe"), str):
        return decoded["describe"].strip(), [], []
    if isinstance(decoded, str):
        return decoded.strip(), [], []
    return "", [], []


def _image_urls(post_wrapper: dict[str, Any], post: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for collection in (post_wrapper.get("image_list"), post.get("images")):
        if not isinstance(collection, list):
            continue
        for image in collection:
            if isinstance(image, str):
                urls.append(image)
            elif isinstance(image, dict) and isinstance(image.get("url"), str):
                urls.append(image["url"])
    return _http_urls(urls)


class MiyousheTimelineClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def fetch_account(
        self,
        *,
        account_id: str,
        account_name: str,
        account_url: str,
        cursor: MiyousheCursor | None,
        first_seen_at: datetime,
        published_since: datetime,
        max_pages: int = 6,
    ) -> FetchBatch[MiyousheCursor]:
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        if published_since.tzinfo is None or published_since.utcoffset() is None:
            raise ValueError("published_since must include a timezone")
        match = SUBSITE_PATTERN.match(account_url)
        if match is None:
            raise ValueError("account_url must be a Miyoushe account page")
        subsite = match.group(1)
        current_cursor = cursor or MiyousheCursor()
        boundary_id = current_cursor.latest_post_id
        offset: str | None = None
        fetched: list[FetchedObservation] = []
        seen_ids: set[str] = set()
        encountered: list[tuple[str, datetime]] = []

        for _page in range(max_pages):
            parameters = {"uid": account_id, "size": "20"}
            if offset:
                parameters["offset"] = offset
            payload = await self._get_json(TIMELINE_API, parameters)
            data = payload.get("data")
            if not isinstance(data, dict):
                raise SourceAccessError("Miyoushe timeline data was not found")
            raw_items = data.get("list")
            if not isinstance(raw_items, list) or not raw_items:
                break

            page_items: list[tuple[str, datetime]] = []
            stop_at_boundary = False
            for raw_item in raw_items:
                if not isinstance(raw_item, dict):
                    continue
                post_id = _post_id(raw_item)
                if not post_id or post_id in seen_ids:
                    continue
                seen_ids.add(post_id)
                published_at = _post_time(raw_item)
                page_items.append((post_id, published_at))
                encountered.append((post_id, published_at))

                if published_at >= published_since or post_id == boundary_id:
                    fetched.append(
                        await self._fetch_detail(
                            post_id=post_id,
                            account_id=account_id,
                            account_name=account_name,
                            subsite=subsite,
                            first_seen_at=first_seen_at,
                        )
                    )
                if post_id == boundary_id:
                    stop_at_boundary = True
                    break

            if stop_at_boundary or not page_items:
                break
            if all(published_at < published_since for _, published_at in page_items):
                break
            next_offset = str(data.get("next_offset") or "")
            if (
                data.get("is_last") is True
                or not next_offset
                or next_offset == offset
            ):
                break
            offset = next_offset

        fetched.sort(
            key=lambda item: item.observation.source.published_at, reverse=True
        )
        newest = max(encountered, key=lambda item: item[1], default=None)
        return FetchBatch(
            items=tuple(fetched),
            cursor=MiyousheCursor(
                latest_post_id=newest[0] if newest else current_cursor.latest_post_id
            ),
        )

    async def _fetch_detail(
        self,
        *,
        post_id: str,
        account_id: str,
        account_name: str,
        subsite: str,
        first_seen_at: datetime,
    ) -> FetchedObservation:
        payload = await self._get_json(DETAIL_API, {"post_id": post_id})
        data = payload.get("data")
        post_wrapper = data.get("post") if isinstance(data, dict) else None
        if not isinstance(post_wrapper, dict):
            raise SourceAccessError("Miyoushe post detail was not found")
        post = post_wrapper.get("post")
        if not isinstance(post, dict):
            raise SourceAccessError("Miyoushe post detail was not found")

        actual_post_id = str(post.get("post_id") or post_id)
        subject = str(post.get("subject") or "").strip()
        body, embedded_images, outbound_urls = _decode_content(post)
        text = "\n".join(part for part in (subject, body) if part)
        published_at = _timestamp(post.get("created_at"))
        media_urls = _http_urls(
            [*_image_urls(post_wrapper, post), *embedded_images]
        )
        media_hashes = [
            hashlib.sha256(url.encode("utf-8")).hexdigest() for url in media_urls
        ]
        content_hash = hashlib.sha256(
            "\n".join([text, actual_post_id, *media_urls]).encode("utf-8")
        ).hexdigest()
        source_id = f"miyoushe-{actual_post_id}"
        observation = SourceObservation(
            id=source_id,
            source=SourceRef(
                id=source_id,
                kind=SourceKind.MIYOUSHE,
                url=f"https://www.miyoushe.com/{subsite}/article/{actual_post_id}",
                account_name=account_name,
                account_id=account_id,
                canonical_content_id=actual_post_id,
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
        return FetchedObservation(
            observation=observation, media_urls=tuple(media_urls)
        )

    async def _get_json(
        self, endpoint: str, parameters: dict[str, str]
    ) -> dict[str, Any]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.miyoushe.com/",
            "User-Agent": "Mozilla/5.0 HERALD/0.1",
        }
        try:
            response = await self.client.get(
                endpoint, params=parameters, headers=headers
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceAccessError("Miyoushe request failed") from exc
        if not isinstance(payload, dict) or payload.get("retcode") != 0:
            raise SourceAccessError("Miyoushe returned an unavailable response")
        return payload
