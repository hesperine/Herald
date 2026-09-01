from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone

import httpx

from herald.models import SourceKind
from herald.sources.base import SourceAccessError
from herald.sources.miyoushe import MiyousheCursor, MiyousheTimelineClient


UTC = timezone.utc
NOW = datetime(2026, 9, 2, 2, tzinfo=UTC)


class MiyousheTimelineClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_pages_author_timeline_and_expands_every_candidate_post(self) -> None:
        requests: list[httpx.Request] = []

        def list_item(post_id: str, published_at: datetime) -> dict[str, object]:
            return {
                "post": {
                    "post_id": post_id,
                    "created_at": int(published_at.timestamp()),
                    "subject": f"列表标题 {post_id}",
                }
            }

        def detail(post_id: str) -> dict[str, object]:
            image_list = (
                [{"url": "https://upload-bbs.miyoushe.com/poster.jpg"}]
                if post_id == "103"
                else []
            )
            return {
                "retcode": 0,
                "data": {
                    "post": {
                        "post": {
                            "post_id": post_id,
                            "subject": f"完整标题 {post_id}",
                            "created_at": int(NOW.timestamp()),
                            "structured_content": json.dumps(
                                [
                                    {"insert": "联动完整正文"},
                                    {
                                        "insert": "预约入口",
                                        "attributes": {
                                            "link": "https://example.com/reserve"
                                        },
                                    },
                                    {"insert": "\n"},
                                ],
                                ensure_ascii=False,
                            ),
                        },
                        "user": {"uid": "75276539", "nickname": "原神"},
                        "image_list": image_list,
                    }
                },
            }

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path.endswith("/userPostList"):
                self.assertEqual(request.url.params.get("uid"), "75276539")
                self.assertIsNone(request.url.params.get("offset"))
                return httpx.Response(
                    200,
                    json={
                        "retcode": 0,
                        "data": {
                            "list": [
                                list_item("103", NOW),
                                list_item("102", NOW - timedelta(hours=2)),
                            ],
                            "next_offset": "private-pagination-token",
                            "is_last": False,
                        },
                    },
                )
            return httpx.Response(200, json=detail(request.url.params["post_id"]))

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            batch = await MiyousheTimelineClient(http_client).fetch_account(
                account_id="75276539",
                account_name="原神",
                account_url=(
                    "https://www.miyoushe.com/ys/accountCenter/postList?id=75276539"
                ),
                cursor=MiyousheCursor(latest_post_id="102"),
                first_seen_at=NOW,
                published_since=NOW - timedelta(days=3),
            )

        self.assertEqual([item.observation.id for item in batch.items], ["miyoushe-103", "miyoushe-102"])
        self.assertEqual(batch.cursor, MiyousheCursor(latest_post_id="103"))
        self.assertEqual(len(requests), 3)
        first = batch.items[0].observation
        self.assertEqual(first.source.kind, SourceKind.MIYOUSHE)
        self.assertEqual(str(first.source.url), "https://www.miyoushe.com/ys/article/103")
        self.assertEqual(first.text, "完整标题 103\n联动完整正文预约入口")
        self.assertEqual(
            [str(url) for url in first.outbound_urls],
            ["https://example.com/reserve"],
        )
        self.assertEqual(
            [str(url) for url in first.media_urls],
            ["https://upload-bbs.miyoushe.com/poster.jpg"],
        )
        # Pure-text posts must not be discarded.
        self.assertEqual(batch.items[1].observation.media_urls, [])

    async def test_uses_next_offset_but_does_not_save_it_as_incremental_cursor(self) -> None:
        requested_offsets: list[str | None] = []
        detail_ids: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/userPostList"):
                offset = request.url.params.get("offset")
                requested_offsets.append(offset)
                if offset is None:
                    post_id = "new"
                    created_at = NOW
                    next_offset = "page-two"
                    is_last = False
                else:
                    post_id = "old"
                    created_at = NOW - timedelta(days=10)
                    next_offset = "page-three"
                    is_last = False
                return httpx.Response(
                    200,
                    json={
                        "retcode": 0,
                        "data": {
                            "list": [
                                {
                                    "post": {
                                        "post_id": post_id,
                                        "created_at": int(created_at.timestamp()),
                                    }
                                }
                            ],
                            "next_offset": next_offset,
                            "is_last": is_last,
                        },
                    },
                )
            post_id = request.url.params["post_id"]
            detail_ids.append(post_id)
            return httpx.Response(
                200,
                json={
                    "retcode": 0,
                    "data": {
                        "post": {
                            "post": {
                                "post_id": post_id,
                                "subject": "完整公告",
                                "created_at": int(NOW.timestamp()),
                                "content": json.dumps({"describe": "联动正文"}),
                            },
                            "image_list": [],
                        }
                    },
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            batch = await MiyousheTimelineClient(http_client).fetch_account(
                account_id="75276539",
                account_name="原神",
                account_url=(
                    "https://www.miyoushe.com/ys/accountCenter/postList?id=75276539"
                ),
                cursor=None,
                first_seen_at=NOW,
                published_since=NOW - timedelta(days=3),
                max_pages=5,
            )

        self.assertEqual(requested_offsets, [None, "page-two"])
        self.assertEqual(detail_ids, ["new"])
        self.assertEqual(batch.cursor.model_dump(), {"latest_post_id": "new"})
        self.assertEqual(batch.items[0].observation.text, "完整公告\n联动正文")

    async def test_http_and_service_errors_are_redacted(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"retcode": -1, "message": "response containing private data"},
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            with self.assertRaisesRegex(
                SourceAccessError, "Miyoushe returned an unavailable response"
            ) as context:
                await MiyousheTimelineClient(http_client).fetch_account(
                    account_id="75276539",
                    account_name="原神",
                    account_url=(
                        "https://www.miyoushe.com/ys/accountCenter/postList?id=75276539"
                    ),
                    cursor=None,
                    first_seen_at=NOW,
                    published_since=NOW - timedelta(days=3),
                )

        self.assertNotIn("private data", str(context.exception))


if __name__ == "__main__":
    unittest.main()
