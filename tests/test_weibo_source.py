from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import httpx

from herald.sources.base import SourceAccessError
from herald.sources.weibo import WeiboCursor, WeiboTimelineClient


UTC = timezone.utc
NOW = datetime(2026, 8, 30, 12, tzinfo=UTC)


class WeiboTimelineClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolves_container_parses_latest_posts_and_saves_cursor(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.params.get("type") == "uid":
                return httpx.Response(
                    200,
                    json={
                        "ok": 1,
                        "data": {
                            "tabsInfo": {
                                "tabs": [
                                    {
                                        "tab_type": "weibo",
                                        "containerid": "1076031001",
                                    }
                                ]
                            }
                        },
                    },
                )
            return httpx.Response(
                200,
                json={
                    "ok": 1,
                    "data": {
                        "cards": [
                            {
                                "card_type": 9,
                                "mblog": {
                                    "id": "501",
                                    "bid": "AbCd",
                                    "created_at": "Sun Aug 30 10:00:00 +0800 2026",
                                    "text": "<b>联动正式公布</b>，9月8日开始",
                                    "pics": [
                                        {"large": {"url": "https://img.example/poster.jpg"}}
                                    ],
                                    "url_struct": [
                                        {"long_url": "https://example.com/event?from=weibo"}
                                    ],
                                },
                            },
                            {"card_type": 11, "card_group": []},
                        ],
                        "cardlistInfo": {"since_id": "next-2"},
                    },
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            client = WeiboTimelineClient(http_client, cookie="private-cookie")
            batch = await client.fetch_account(
                account_id="1001",
                account_name="原神",
                cursor=None,
                first_seen_at=NOW,
                published_since=NOW - timedelta(hours=72),
            )

        self.assertEqual(len(requests), 2)
        self.assertEqual(
            batch.cursor,
            WeiboCursor(container_id="1076031001", latest_post_id="501"),
        )
        self.assertEqual(len(batch.items), 1)
        item = batch.items[0]
        self.assertEqual(item.observation.text, "联动正式公布，9月8日开始")
        self.assertEqual(item.observation.source.canonical_content_id, "501")
        self.assertEqual(item.media_urls, ("https://img.example/poster.jpg",))
        self.assertEqual(
            [str(url) for url in item.observation.media_urls],
            ["https://img.example/poster.jpg"],
        )
        self.assertEqual(len(item.observation.media_hashes), 1)

    async def test_reposted_text_and_poster_are_kept_as_public_material(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "ok": 1,
                    "data": {
                        "cards": [
                            {
                                "card_type": 9,
                                "mblog": {
                                    "id": "outer",
                                    "created_at": "Sun Aug 30 10:00:00 +0800 2026",
                                    "text": "转发微博",
                                    "retweeted_status": {
                                        "id": "original",
                                        "text": "<b>原神联动快闪，9月8日开放预约</b>",
                                        "pics": [
                                            {
                                                "pid": "poster-original",
                                                "large": {
                                                    "url": "https://img.example/repost.jpg"
                                                },
                                            }
                                        ],
                                    },
                                },
                            }
                        ],
                        "cardlistInfo": {},
                    },
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            batch = await WeiboTimelineClient(http_client).fetch_account(
                account_id="1001",
                account_name="原神",
                cursor=WeiboCursor(container_id="1076031001"),
                first_seen_at=NOW,
                published_since=NOW - timedelta(hours=72),
            )

        item = batch.items[0]
        self.assertIn("转发内容：原神联动快闪", item.observation.text)
        self.assertEqual(item.observation.source.canonical_content_id, "original")
        self.assertEqual(len(item.observation.media_hashes), 1)
        self.assertEqual(
            str(item.observation.media_urls[0]), "https://img.example/repost.jpg"
        )

    async def test_existing_cursor_reads_latest_page_without_using_pagination_token(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "ok": 1,
                    "data": {"cards": [], "cardlistInfo": {"since_id": "older-page"}},
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            client = WeiboTimelineClient(http_client)
            await client.fetch_account(
                account_id="1001",
                account_name="原神",
                cursor=WeiboCursor(container_id="1076031001", latest_post_id="old"),
                first_seen_at=NOW,
                published_since=NOW - timedelta(hours=72),
            )

        self.assertEqual(len(requests), 1)
        self.assertIsNone(requests[0].url.params.get("since_id"))

    async def test_incremental_read_stops_at_previous_latest_post(self) -> None:
        def post(post_id: str, day: int) -> dict[str, object]:
            return {
                "card_type": 9,
                "mblog": {
                    "id": post_id,
                    "created_at": f"Sun Aug {day:02d} 10:00:00 +0800 2026",
                    "text": f"原神联动公告 {post_id}",
                },
            }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "ok": 1,
                    "data": {
                        "cards": [post("new", 30), post("boundary", 29), post("old", 28)],
                        "cardlistInfo": {"since_id": "older-page"},
                    },
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            batch = await WeiboTimelineClient(http_client).fetch_account(
                account_id="1001",
                account_name="原神",
                cursor=WeiboCursor(
                    container_id="1076031001", latest_post_id="boundary"
                ),
                first_seen_at=NOW,
                published_since=NOW - timedelta(hours=72),
            )

        self.assertEqual(
            [item.observation.id for item in batch.items],
            ["weibo-new", "weibo-boundary"],
        )
        self.assertEqual(batch.cursor.latest_post_id, "new")

    async def test_old_posts_are_filtered_by_publication_time(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "ok": 1,
                    "data": {
                        "cards": [
                            {
                                "card_type": 9,
                                "mblog": {
                                    "id": "old",
                                    "created_at": "Mon Aug 10 10:00:00 +0800 2026",
                                    "text": "旧公告",
                                },
                            }
                        ],
                        "cardlistInfo": {},
                    },
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            client = WeiboTimelineClient(http_client)
            batch = await client.fetch_account(
                account_id="1001",
                account_name="原神",
                cursor=WeiboCursor(container_id="1076031001"),
                first_seen_at=NOW,
                published_since=NOW - timedelta(hours=72),
            )

        self.assertEqual(batch.items, ())

    async def test_http_failure_is_redacted(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(432, text="response containing private details")

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            client = WeiboTimelineClient(http_client, cookie="secret-cookie")
            with self.assertRaisesRegex(SourceAccessError, "Weibo request failed") as context:
                await client.fetch_account(
                    account_id="1001",
                    account_name="原神",
                    cursor=WeiboCursor(container_id="1076031001"),
                    first_seen_at=NOW,
                    published_since=NOW - timedelta(hours=72),
                )

        self.assertNotIn("secret-cookie", str(context.exception))
        self.assertNotIn("private details", str(context.exception))


if __name__ == "__main__":
    unittest.main()
