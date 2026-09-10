"""Cookie-free official-account adapter for public Skland posts.

Skland does not require a user's login cookie for public material, but it does
require a short-lived device id and signed API requests. The device identity
and signing sequence is derived from Danbooru's BSD-2-Clause Skland extractor;
HERALD adds author timeline pagination, incremental cursors, and its own domain
normalization.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import hmac
import json
import secrets
import string
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from uuid import UUID, uuid4

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding as asymmetric_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
from pydantic import BaseModel, ConfigDict

from herald.models import SourceKind, SourceObservation, SourceRef
from herald.sources.base import FetchBatch, FetchedObservation, SourceAccessError


API_BASE = "https://zonai.skland.com"
TIMELINE_PATH = "/web/v2/user/items"
DETAIL_PATH = "/web/v1/item"
REFRESH_PATH = "/web/v1/auth/refresh"
DEVICE_PROFILE_API = "https://fp-it.portal101.cn/deviceprofile/v4"
SHUMEI_ORGANIZATION = "UWXspnCCJN4sfYlNfqps"
SHUMEI_PUBLIC_KEY = (
    "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCmxMNr7n8ZeT0tE1R9j/"
    "mPixoinPkeM+k4VGIn/s0k7N5rJAfnZ0eMER+QhwFvshzo0LNmeUkpR8uIl"
    "U/GEVr8mN28sKmwd2gpygqj0ePnBmOW4v0ZVwbSYK+izkhVFk2V/doLoMbW"
    "y6b+UnA8mkjvg0iYWRByfRsK2gdl7llqCwIDAQAB"
)


class SklandCursor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latest_post_id: str | None = None


def _random_list_id(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


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
            "Skland returned an unsupported publication time"
        ) from exc


def _null_pad(value: bytes, block_size: int) -> bytes:
    padding = (block_size - len(value) % block_size) % block_size
    return value + b"\0" * padding


def _encrypt(value: bytes, cipher: Cipher, block_size: int) -> bytes:
    encryptor = cipher.encryptor()
    return encryptor.update(_null_pad(value, block_size)) + encryptor.finalize()


class _SklandDeviceIdentity:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        uuid_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self.client = client
        self.uid = str(uuid_factory())
        self._device_id: str | None = None

    async def get(self) -> str:
        if self._device_id is not None:
            return self._device_id
        payload = {
            "appId": "default",
            "organization": SHUMEI_ORGANIZATION,
            "ep": self._encrypted_uuid(),
            "data": self._encrypted_fingerprint(),
            "os": "web",
            "encode": 5,
            "compress": 2,
        }
        try:
            response = await self.client.post(
                DEVICE_PROFILE_API,
                json=payload,
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Referer": "https://www.skland.com/",
                    "User-Agent": "Mozilla/5.0 HERALD/0.1",
                },
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceAccessError("Skland device identity request failed") from exc
        raw_device_id = (
            body.get("detail", {}).get("deviceId")
            if isinstance(body, dict) and isinstance(body.get("detail"), dict)
            else None
        )
        if not isinstance(raw_device_id, str) or not raw_device_id:
            raise SourceAccessError("Skland device identity was not available")
        self._device_id = f"B{raw_device_id}"
        return self._device_id

    def _encrypted_uuid(self) -> str:
        public_key = serialization.load_der_public_key(
            base64.b64decode(SHUMEI_PUBLIC_KEY)
        )
        encrypted = public_key.encrypt(  # type: ignore[union-attr]
            self.uid.encode("ascii"), asymmetric_padding.PKCS1v15()
        )
        return base64.b64encode(encrypted).decode("ascii")

    def _encrypted_fingerprint(self) -> str:
        fingerprint = {
            "protocol": 102,
            "pj": self._triple_des_base64("web", "je6vk6t4"),
            "lo": self._triple_des_base64("all", "x8o2h2bl"),
        }
        raw = json.dumps(
            fingerprint, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        compressed = base64.b64encode(gzip.compress(raw))
        key = hashlib.md5(self.uid.encode("ascii")).hexdigest()[:16].encode("ascii")
        cipher = Cipher(algorithms.AES(key), modes.CBC(b"0102030405060708"))
        return _encrypt(compressed, cipher, 16).hex()

    @staticmethod
    def _triple_des_base64(value: str, key: str) -> str:
        cipher = Cipher(TripleDES((key * 3).encode("ascii")), modes.ECB())
        encrypted = _encrypt(value.encode("utf-8"), cipher, 8)
        return base64.b64encode(encrypted).decode("ascii")


def _timeline_item(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    item = value.get("item")
    return item if isinstance(item, dict) else value


def _item_id(item: dict[str, Any]) -> str:
    return str(item.get("id") or item.get("itemId") or "")


def _item_time(item: dict[str, Any]) -> datetime:
    return _timestamp(
        item.get("publishedAtTs")
        or item.get("createdAtTs")
        or item.get("publishAtTs")
    )


def _slice_map(value: object, *, content_key: str) -> dict[str, str]:
    result: dict[str, str] = {}
    if not isinstance(value, list):
        return result
    for item in value:
        if not isinstance(item, dict):
            continue
        identifier = item.get("id")
        content = item.get(content_key)
        if identifier is not None and isinstance(content, str):
            result[str(identifier)] = content
    return result


def _format_text(item: dict[str, Any]) -> str:
    text_slices = _slice_map(item.get("textSlice"), content_key="c")
    raw_format = item.get("format")
    if isinstance(raw_format, str) and raw_format.strip():
        try:
            parsed_format = json.loads(raw_format)
        except ValueError as exc:
            raise SourceAccessError("Skland post content could not be decoded") from exc
        nodes = parsed_format.get("data") if isinstance(parsed_format, dict) else None
        if not isinstance(nodes, list):
            raise SourceAccessError("Skland post content has an unsupported shape")
        paragraphs: list[str] = []
        for node in nodes:
            if not isinstance(node, dict) or node.get("type") != "paragraph":
                continue
            contents = node.get("contents")
            if not isinstance(contents, list):
                continue
            parts: list[str] = []
            for content in contents:
                if not isinstance(content, dict):
                    continue
                content_type = content.get("type")
                if content_type == "text":
                    content_id = str(content.get("contentId") or "")
                    parts.append(
                        text_slices.get(
                            content_id, str(content.get("text") or "")
                        )
                    )
                elif content_type == "emoji" and content.get("id"):
                    parts.append(f":{content['id']}:")
                elif content_type == "link":
                    content_id = str(content.get("contentId") or "")
                    parts.append(
                        text_slices.get(
                            content_id, str(content.get("text") or "")
                        )
                    )
            paragraph = "".join(parts).strip()
            if paragraph:
                paragraphs.append(paragraph)
        if paragraphs:
            return "\n".join(paragraphs)

    caption = item.get("caption")
    if isinstance(caption, str) and caption.strip():
        return caption.strip()
    return "\n".join(value for value in text_slices.values() if value).strip()


def _media_urls(item: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for collection in (item.get("imageListSlice"), item.get("imageList")):
        if not isinstance(collection, list):
            continue
        for image in collection:
            if isinstance(image, str):
                urls.append(image)
            elif isinstance(image, dict) and isinstance(image.get("url"), str):
                urls.append(image["url"])
    return _http_urls(urls)


def _outbound_urls(item: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    links = item.get("linkSlice")
    if isinstance(links, list):
        for link in links:
            if not isinstance(link, dict):
                continue
            for key in ("url", "href", "link"):
                value = link.get(key)
                if isinstance(value, str):
                    urls.append(value)
    return _http_urls(urls)


class SklandTimelineClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        clock: Callable[[], datetime] | None = None,
        device_uuid_factory: Callable[[], UUID] = uuid4,
        list_id_factory: Callable[[], object] = _random_list_id,
    ) -> None:
        self.client = client
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.device = _SklandDeviceIdentity(
            client, uuid_factory=device_uuid_factory
        )
        self.list_id_factory = list_id_factory

    async def fetch_account(
        self,
        *,
        account_id: str,
        account_name: str,
        cursor: SklandCursor | None,
        first_seen_at: datetime,
        published_since: datetime,
        max_pages: int = 6,
        detail_predicate: Callable[[dict], bool] | None = None,
        published_before: datetime | None = None,
    ) -> FetchBatch[SklandCursor]:
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        self.last_fetch_complete = False
        if published_before is not None and (published_before.tzinfo is None or published_before <= published_since):
            raise ValueError('published_before must be aware and after published_since')
        if published_since.tzinfo is None or published_since.utcoffset() is None:
            raise ValueError("published_since must include a timezone")
        current_cursor = cursor or SklandCursor()
        boundary_id = current_cursor.latest_post_id
        page_token: str | None = None
        list_id = str(self.list_id_factory())
        fetched: list[FetchedObservation] = []
        seen_ids: set[str] = set()
        encountered: list[tuple[str, datetime]] = []

        for _page in range(max_pages):
            parameters = {
                "pageSize": "10",
                "listId": list_id,
                "userId": account_id,
                "sortType": "2",
            }
            if page_token:
                parameters["pageToken"] = page_token
            payload = await self._signed_get(TIMELINE_PATH, parameters)
            data = payload.get("data")
            if not isinstance(data, dict):
                raise SourceAccessError("Skland timeline data was not found")
            raw_items = data.get("list")
            if not isinstance(raw_items, list) or not raw_items:
                self.last_fetch_complete = isinstance(raw_items, list)
                break

            page_items: list[tuple[str, datetime]] = []
            stop_at_boundary = False
            for raw_item in raw_items:
                item = _timeline_item(raw_item)
                post_id = _item_id(item)
                if not post_id or post_id in seen_ids:
                    continue
                seen_ids.add(post_id)
                published_at = _item_time(item)
                page_items.append((post_id, published_at))
                encountered.append((post_id, published_at))
                if (published_at >= published_since or post_id == boundary_id) and (
                    detail_predicate is None or detail_predicate(item)
                ) and (
                    published_before is None or published_at < published_before
                ):
                    fetched.append(
                        await self._fetch_detail(
                            post_id=post_id,
                            account_id=account_id,
                            account_name=account_name,
                            first_seen_at=first_seen_at,
                        )
                    )
                if post_id == boundary_id:
                    stop_at_boundary = True
                    break

            if stop_at_boundary or not page_items:
                self.last_fetch_complete = stop_at_boundary
                break
            if all(published_at < published_since for _, published_at in page_items):
                self.last_fetch_complete = True
                break
            next_token = str(data.get("pageToken") or data.get("page_token") or "")
            if (
                data.get("hasMore") is False
                or not next_token
                or next_token == page_token
            ):
                self.last_fetch_complete = data.get('hasMore') is False
                break
            page_token = next_token

        fetched.sort(
            key=lambda value: value.observation.source.published_at, reverse=True
        )
        newest = max(encountered, key=lambda value: value[1], default=None)
        return FetchBatch(
            items=tuple(fetched),
            cursor=SklandCursor(
                latest_post_id=newest[0] if newest else current_cursor.latest_post_id
            ),
        )

    async def _fetch_detail(
        self,
        *,
        post_id: str,
        account_id: str,
        account_name: str,
        first_seen_at: datetime,
    ) -> FetchedObservation:
        payload = await self._signed_get(DETAIL_PATH, {"id": post_id})
        data = payload.get("data")
        if not isinstance(data, dict):
            raise SourceAccessError("Skland post detail was not found")
        item = data.get("item")
        if not isinstance(item, dict):
            raise SourceAccessError("Skland post detail was not found")

        actual_post_id = _item_id(item) or post_id
        title = str(item.get("title") or "").strip()
        body = _format_text(item)
        text = "\n".join(part for part in (title, body) if part)
        published_at = _item_time(item)
        media_urls = _media_urls(item)
        outbound_urls = _outbound_urls(item)
        media_hashes = [
            hashlib.sha256(url.encode("utf-8")).hexdigest() for url in media_urls
        ]
        content_hash = hashlib.sha256(
            "\n".join([text, actual_post_id, *media_urls]).encode("utf-8")
        ).hexdigest()
        source_id = f"skland-{actual_post_id}"
        observation = SourceObservation(
            id=source_id,
            source=SourceRef(
                id=source_id,
                kind=SourceKind.SKLAND,
                url=f"https://www.skland.com/article?id={actual_post_id}",
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

    async def _base_headers(self) -> dict[str, str]:
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Skland request clock must include a timezone")
        return {
            "platform": "3",
            "timestamp": str(int(now.timestamp())),
            "dId": await self.device.get(),
            "vName": "1.0.0",
        }

    async def _refresh_token(self, headers: dict[str, str]) -> str:
        payload = await self._get_json(f"{API_BASE}{REFRESH_PATH}", headers=headers)
        data = payload.get("data")
        token = data.get("token") if isinstance(data, dict) else None
        if payload.get("code") != 0 or not isinstance(token, str) or not token:
            raise SourceAccessError("Skland temporary authorization was not available")
        # Skland rejects even small local clock drift. Its unsigned refresh
        # response provides the authoritative Unix timestamp for the signed
        # request that immediately follows.
        server_timestamp = payload.get("timestamp")
        if isinstance(server_timestamp, (int, str)) and str(
            server_timestamp
        ).isdigit():
            headers["timestamp"] = str(server_timestamp)
        return token

    async def _signed_get(
        self, path: str, parameters: dict[str, str]
    ) -> dict[str, Any]:
        headers = await self._base_headers()
        # The temporary token is issued for this exact timestamp and device
        # header set, so the refresh and signed request must reuse them.
        token = await self._refresh_token(headers)
        query = urlencode(parameters)
        header_json = json.dumps(
            headers, ensure_ascii=False, separators=(",", ":")
        )
        material = f"{path}{query}{headers['timestamp']}{header_json}"
        hmac_digest = hmac.new(
            token.encode("utf-8"), material.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        headers["sign"] = hashlib.md5(hmac_digest.encode("ascii")).hexdigest()
        payload = await self._get_json(
            f"{API_BASE}{path}?{query}", headers=headers
        )
        if payload.get("code") != 0:
            raise SourceAccessError("Skland returned an unavailable response")
        return payload

    async def _get_json(
        self, url: str, *, headers: dict[str, str]
    ) -> dict[str, Any]:
        request_headers = {
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.skland.com",
            "Referer": "https://www.skland.com/",
            "User-Agent": "Mozilla/5.0 HERALD/0.1",
            **headers,
        }
        try:
            response = await self.client.get(url, headers=request_headers)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceAccessError("Skland request failed") from exc
        if not isinstance(payload, dict):
            raise SourceAccessError("Skland returned an unavailable response")
        return payload
