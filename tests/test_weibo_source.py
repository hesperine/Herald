from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import httpx

from herald.sources.base import SourceAccessError
from herald.sources.weibo import WeiboCursor, WeiboTimelineClient


UTC = timezone.utc
NOW = datetime(2026, 8, 30, 12, tzinfo=UTC)


class WeiboTimelineClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_long_weibo_is_expanded_before_observation_is_built(self) -> None:
        requested_paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested_paths.append(request.url.path)
            if request.url.path.endswith("/statuses/extend"):
                self.assertEqual(request.url.params.get("id"), "long-post")
                return httpx.Response(
                    200,
                    json={
                        "ok": 1,
                        "data": {
                            "longTextContent": (
                                "<p>完整联动正文，9月10日通过微博抽奖平台"
                                "抽取10位用户。</p>"
                            ),
                            "url_struct": [
                                {"long_url": "https://example.com/lottery"}
                            ],
                        },
                    },
                )
            if request.url.params.get("page") == "1":
                cards = [
                    {
                        "card_type": 9,
                        "mblog": {
                            "id": "long-post",
                            "bid": "LongBid",
                            "created_at": "Sun Aug 30 10:00:00 +0800 2026",
                            "isLongText": True,
                            "text": "完整联动正文，9月10日通过 ...全文",
                        },
                    }
                ]
            else:
                cards = []
            return httpx.Response(200, json={"ok": 1, "data": {"cards": cards}})

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

        self.assertEqual(
            batch.items[0].observation.text,
            "完整联动正文，9月10日通过微博抽奖平台抽取10位用户。",
        )
        self.assertNotIn("...全文", batch.items[0].observation.text)
        self.assertEqual(
            [str(url) for url in batch.items[0].observation.outbound_urls],
            ["https://example.com/lottery"],
        )
        self.assertIn("/statuses/extend", requested_paths)

    async def test_long_reposted_weibo_is_expanded_too(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/statuses/extend"):
                self.assertEqual(request.url.params.get("id"), "original-long")
                return httpx.Response(
                    200,
                    json={
                        "ok": 1,
                        "data": {
                            "longTextContent": (
                                "<p>原始活动完整正文，活动地点上海、成都。</p>"
                            )
                        },
                    },
                )
            if request.url.params.get("page") == "1":
                cards = [
                    {
                        "card_type": 9,
                        "mblog": {
                            "id": "lottery-result",
                            "bid": "ResultBid",
                            "created_at": "Sun Aug 30 10:00:00 +0800 2026",
                            "text": "开奖结果",
                            "retweeted_status": {
                                "id": "original-long",
                                "isLongText": True,
                                "text": "原始活动正文 ...全文",
                            },
                        },
                    }
                ]
            else:
                cards = []
            return httpx.Response(200, json={"ok": 1, "data": {"cards": cards}})

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

        self.assertEqual(
            batch.items[0].observation.text,
            "开奖结果\n转发内容：原始活动完整正文，活动地点上海、成都。",
        )

    async def test_history_read_filters_date_range_and_pages_until_older(self) -> None:
        requests: list[httpx.Request] = []

        def post(post_id: str, created_at: str) -> dict[str, object]:
            return {
                "card_type": 9,
                "mblog": {
                    "id": post_id,
                    "bid": f"bid-{post_id}",
                    "created_at": created_at,
                    "text": f"联动公告 {post_id}",
                },
            }

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
            page = request.url.params.get("page")
            if page == "1":
                cards = [
                    post("too-new", "Mon Aug 31 11:00:00 +0800 2026"),
                    post("in-range", "Sun Aug 30 10:00:00 +0800 2026"),
                ]
                since_id = "page-2"
            elif page == "2":
                cards = [
                    post("lower-bound", "Sat Aug 15 10:00:00 +0800 2026"),
                    post("too-old", "Fri Aug 14 10:00:00 +0800 2026"),
                ]
                since_id = "page-3"
            else:
                self.fail("history fetch should stop after crossing the lower bound")
            return httpx.Response(
                200,
                json={
                    "ok": 1,
                    "data": {
                        "cards": cards,
                        "cardlistInfo": {"since_id": since_id},
                    },
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            items = await WeiboTimelineClient(http_client).fetch_history(
                account_id="1001",
                account_name="原神",
                first_seen_at=NOW,
                published_from=datetime(2026, 8, 15, 2, tzinfo=UTC),
                published_before=datetime(2026, 8, 31, 3, tzinfo=UTC),
                max_pages=5,
            )

        self.assertEqual(
            [item.observation.id for item in items],
            ["weibo-in-range", "weibo-lower-bound"],
        )
        self.assertEqual(
            [request.url.params.get("page") for request in requests[1:]],
            ["1", "2"],
        )

    async def test_history_read_deduplicates_a_repeated_pinned_post(self) -> None:
        requested_pages: list[str | None] = []

        def post(post_id: str, day: int) -> dict[str, object]:
            return {
                "card_type": 9,
                "mblog": {
                    "id": post_id,
                    "bid": f"bid-{post_id}",
                    "created_at": f"Sun Aug {day:02d} 10:00:00 +0800 2026",
                    "text": f"联动公告 {post_id}",
                },
            }

        def handler(request: httpx.Request) -> httpx.Response:
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
            page = request.url.params.get("page")
            requested_pages.append(page)
            if page == "1":
                cards = [post("pinned", 30), post("first", 29)]
                info = {"since_id": "page-2"}
            elif page == "2":
                cards = [post("pinned", 30), post("second", 28)]
                info = {"since_id": "page-3"}
            else:
                cards = []
                info = {}
            return httpx.Response(
                200,
                json={"ok": 1, "data": {"cards": cards, "cardlistInfo": info}},
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            items = await WeiboTimelineClient(http_client).fetch_history(
                account_id="1001",
                account_name="原神",
                first_seen_at=NOW,
                published_from=datetime(2026, 8, 1, tzinfo=UTC),
                published_before=datetime(2026, 9, 1, tzinfo=UTC),
                max_pages=5,
            )

        self.assertEqual(
            [item.observation.id for item in items],
            ["weibo-pinned", "weibo-first", "weibo-second"],
        )
        self.assertEqual(requested_pages, ["1", "2", "3"])

    async def test_history_read_rejects_an_empty_or_reversed_range(self) -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(500))
        ) as http_client:
            client = WeiboTimelineClient(http_client)
            with self.assertRaisesRegex(ValueError, "published_from"):
                await client.fetch_history(
                    account_id="1001",
                    account_name="原神",
                    first_seen_at=NOW,
                    published_from=NOW,
                    published_before=NOW,
                )

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
                        "cardlistInfo": {},
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

        self.assertEqual(len(requests), 3)
        self.assertEqual(
            batch.cursor,
            WeiboCursor(container_id="1076031001", latest_post_id="501"),
        )
        self.assertEqual(len(batch.items), 1)
        item = batch.items[0]
        self.assertEqual(item.observation.text, "联动正式公布，9月8日开始")
        self.assertEqual(
            str(item.observation.source.url), "https://weibo.com/1001/AbCd"
        )
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

    async def test_incremental_read_pages_until_previous_latest_post(self) -> None:
        requested_pages: list[str | None] = []

        def post(post_id: str, day: int) -> dict[str, object]:
            return {
                "card_type": 9,
                "mblog": {
                    "id": post_id,
                    "bid": f"bid-{post_id}",
                    "created_at": f"Sun Aug {day:02d} 10:00:00 +0800 2026",
                    "text": f"原神联动公告 {post_id}",
                },
            }

        def handler(request: httpx.Request) -> httpx.Response:
            page = request.url.params.get("page")
            requested_pages.append(page)
            if page == "1":
                cards = [post("new-a", 30), post("new-b", 29)]
                info = {"since_id": "page-2"}
            elif page == "2":
                cards = [post("boundary", 28), post("old", 27)]
                info = {"since_id": "page-3"}
            else:
                self.fail("incremental fetch should stop at the saved post id")
            return httpx.Response(
                200,
                json={"ok": 1, "data": {"cards": cards, "cardlistInfo": info}},
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
                published_since=NOW - timedelta(days=7),
            )

        self.assertEqual(
            [item.observation.id for item in batch.items],
            ["weibo-new-a", "weibo-new-b", "weibo-boundary"],
        )
        self.assertEqual(batch.cursor.latest_post_id, "new-a")
        self.assertEqual(requested_pages, ["1", "2"])

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
