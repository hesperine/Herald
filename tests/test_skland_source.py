from __future__ import annotations

import hashlib
import hmac
import json
import unittest
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from uuid import UUID

import httpx

from herald.models import SourceKind
from herald.sources.base import SourceAccessError
from herald.sources.skland import SklandCursor, SklandTimelineClient


UTC = timezone.utc
NOW = datetime(2026, 9, 2, 2, tzinfo=UTC)
TIMESTAMP = str(int(NOW.timestamp()))
DEVICE_UUID = UUID("11111111-2222-3333-4444-555555555555")
LIST_ID = "AbCdEf0123456789"


def expected_sign(path: str, parameters: dict[str, str]) -> str:
    headers = {
        "platform": "3",
        "timestamp": TIMESTAMP,
        "dId": "Bpublic-device-id",
        "vName": "1.0.0",
    }
    material = (
        path
        + urlencode(parameters)
        + TIMESTAMP
        + json.dumps(headers, ensure_ascii=False, separators=(",", ":"))
    )
    digest = hmac.new(
        b"temporary-token", material.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hashlib.md5(digest.encode("ascii")).hexdigest()


class SklandTimelineClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_signed_timeline_pages_and_expands_full_posts(self) -> None:
        timeline_tokens: list[str | None] = []
        detail_ids: list[str] = []
        fingerprint_payload: dict[str, object] = {}

        def detail(post_id: str) -> dict[str, object]:
            image_list = (
                [
                    {
                        "id": "poster",
                        "url": "https://bbs.hycdn.cn/image/poster.webp?x-oss-process=style/item_style",
                    }
                ]
                if post_id == "203"
                else []
            )
            return {
                "code": 0,
                "data": {
                    "item": {
                        "id": post_id,
                        "title": f"完整标题 {post_id}",
                        "publishedAtTs": int(NOW.timestamp()),
                        "format": json.dumps(
                            {
                                "data": [
                                    {
                                        "type": "paragraph",
                                        "contents": [
                                            {"type": "text", "contentId": "1"},
                                            {"type": "emoji", "id": "amiya-smile"},
                                        ],
                                    },
                                    {"type": "image", "imageId": "poster"},
                                ]
                            }
                        ),
                        "textSlice": [{"id": "1", "c": "联动完整正文"}],
                        "imageListSlice": image_list,
                        "linkSlice": [
                            {"id": "reserve", "url": "https://example.com/reserve"}
                        ],
                    },
                    "user": {"id": "3737967211133", "nickname": "明日方舟终末地"},
                    "tags": [],
                },
            }

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "fp-it.portal101.cn":
                fingerprint_payload.update(json.loads(request.content))
                return httpx.Response(
                    200, json={"detail": {"deviceId": "public-device-id"}}
                )
            if request.url.path == "/web/v1/auth/refresh":
                self.assertEqual(request.headers["did"], "Bpublic-device-id")
                self.assertNotIn("sign", request.headers)
                return httpx.Response(
                    200, json={"code": 0, "data": {"token": "temporary-token"}}
                )
            if request.url.path == "/web/v2/user/items":
                parameters = dict(request.url.params)
                self.assertEqual(
                    request.headers["sign"], expected_sign(request.url.path, parameters)
                )
                timeline_tokens.append(request.url.params.get("pageToken"))
                if request.url.params.get("pageToken") is None:
                    entries = [
                        {
                            "item": {
                                "id": "203",
                                "publishedAtTs": int(NOW.timestamp()),
                            }
                        }
                    ]
                    page_token = "page-two"
                    has_more = True
                else:
                    entries = [
                        {
                            "item": {
                                "id": "202",
                                "publishedAtTs": int(
                                    (NOW - timedelta(hours=2)).timestamp()
                                ),
                            }
                        }
                    ]
                    page_token = "page-three"
                    has_more = True
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "list": entries,
                            "pageToken": page_token,
                            "hasMore": has_more,
                        },
                    },
                )
            self.assertEqual(request.url.path, "/web/v1/item")
            parameters = dict(request.url.params)
            self.assertEqual(
                request.headers["sign"], expected_sign(request.url.path, parameters)
            )
            post_id = request.url.params["id"]
            detail_ids.append(post_id)
            return httpx.Response(200, json=detail(post_id))

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            batch = await SklandTimelineClient(
                http_client,
                clock=lambda: NOW,
                device_uuid_factory=lambda: DEVICE_UUID,
                list_id_factory=lambda: LIST_ID,
            ).fetch_account(
                account_id="3737967211133",
                account_name="明日方舟终末地",
                cursor=SklandCursor(latest_post_id="202"),
                first_seen_at=NOW,
                published_since=NOW - timedelta(days=3),
            )

        self.assertEqual(timeline_tokens, [None, "page-two"])
        self.assertEqual(detail_ids, ["203", "202"])
        self.assertEqual(batch.cursor, SklandCursor(latest_post_id="203"))
        self.assertEqual(
            fingerprint_payload["organization"], "UWXspnCCJN4sfYlNfqps"
        )
        self.assertEqual(fingerprint_payload["encode"], 5)
        self.assertTrue(str(fingerprint_payload["ep"]))
        self.assertRegex(str(fingerprint_payload["data"]), r"^[0-9a-f]+$")

        first = batch.items[0].observation
        self.assertEqual(first.source.kind, SourceKind.SKLAND)
        self.assertEqual(str(first.source.url), "https://www.skland.com/article?id=203")
        self.assertEqual(first.text, "完整标题 203\n联动完整正文:amiya-smile:")
        self.assertEqual(
            [str(url) for url in first.media_urls],
            [
                "https://bbs.hycdn.cn/image/poster.webp?x-oss-process=style/item_style"
            ],
        )
        self.assertEqual(
            [str(url) for url in first.outbound_urls],
            ["https://example.com/reserve"],
        )
        # Pure-text posts are retained too.
        self.assertEqual(batch.items[1].observation.media_urls, [])

    async def test_old_page_stops_history_without_fetching_old_details(self) -> None:
        timeline_calls = 0
        detail_ids: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal timeline_calls
            if request.url.host == "fp-it.portal101.cn":
                return httpx.Response(200, json={"detail": {"deviceId": "public-device-id"}})
            if request.url.path == "/web/v1/auth/refresh":
                return httpx.Response(200, json={"code": 0, "data": {"token": "temporary-token"}})
            if request.url.path == "/web/v2/user/items":
                timeline_calls += 1
                recent = timeline_calls == 1
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "list": [
                                {
                                    "item": {
                                        "id": "new" if recent else "old",
                                        "publishedAtTs": int(
                                            (
                                                NOW
                                                if recent
                                                else NOW - timedelta(days=10)
                                            ).timestamp()
                                        ),
                                    }
                                }
                            ],
                            "pageToken": "next",
                            "hasMore": True,
                        },
                    },
                )
            post_id = request.url.params["id"]
            detail_ids.append(post_id)
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "item": {
                            "id": post_id,
                            "title": "完整公告",
                            "publishedAtTs": int(NOW.timestamp()),
                            "caption": "联动正文",
                        },
                        "user": {"id": "3737967211133"},
                        "tags": [],
                    },
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            batch = await SklandTimelineClient(
                http_client,
                clock=lambda: NOW,
                device_uuid_factory=lambda: DEVICE_UUID,
                list_id_factory=lambda: LIST_ID,
            ).fetch_account(
                account_id="3737967211133",
                account_name="明日方舟终末地",
                cursor=None,
                first_seen_at=NOW,
                published_since=NOW - timedelta(days=3),
                max_pages=5,
            )

        self.assertEqual(timeline_calls, 2)
        self.assertEqual(detail_ids, ["new"])
        self.assertEqual(batch.cursor.model_dump(), {"latest_post_id": "new"})
        self.assertEqual(batch.items[0].observation.text, "完整公告\n联动正文")

    async def test_service_errors_are_redacted(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "fp-it.portal101.cn":
                return httpx.Response(
                    200,
                    json={"message": "response containing private details"},
                )
            return httpx.Response(500)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            with self.assertRaisesRegex(
                SourceAccessError, "Skland device identity was not available"
            ) as context:
                await SklandTimelineClient(
                    http_client,
                    clock=lambda: NOW,
                    device_uuid_factory=lambda: DEVICE_UUID,
                    list_id_factory=lambda: LIST_ID,
                ).fetch_account(
                    account_id="3737967211133",
                    account_name="明日方舟终末地",
                    cursor=None,
                    first_seen_at=NOW,
                    published_since=NOW - timedelta(days=3),
                )

        self.assertNotIn("private details", str(context.exception))


if __name__ == "__main__":
    unittest.main()
