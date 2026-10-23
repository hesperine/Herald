from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import httpx

from herald.models import SourceKind
from herald.sources.base import SourceAccessError
from herald.sources.website import WebsiteCursor, WebsiteTimelineClient


NOW = datetime(2026, 10, 23, 13, tzinfo=timezone.utc)


def hoyo_item(identifier, day=22, **extra):
    return dict(iInfoId=identifier, sTitle=f"公告 {identifier}",
                dtStartTime=f"2026-10-{day:02} 12:00:00", **extra)


class WebsiteTimelineClientTests(unittest.IsolatedAsyncioTestCase):
    async def fetch(self, handler, account="honkai-star-rail-official", **kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await WebsiteTimelineClient(client).fetch_account(
                account_id=account, account_name="官方公告", cursor=None,
                first_seen_at=NOW, published_since=NOW-timedelta(days=21), **kwargs)

    async def test_hoyo_paginates_and_reads_full_details_before_filtering(self):
        requests = []

        def handler(request):
            requests.append(request)
            self.assertNotIn("cookie", request.headers)
            self.assertNotIn("authorization", request.headers)
            if request.url.path.endswith("/getContentList"):
                self.assertEqual(request.url.params["iChanId"], "255")
                self.assertEqual(request.url.params["sLangKey"], "zh-cn")
                page = request.url.params["iPage"]
                items = [hoyo_item(9, 1), hoyo_item(101)] if page == "1" else [hoyo_item(100, 21)]
                return httpx.Response(200, json={"retcode": 0, "data": {"iTotal": 21, "list": items}})
            identifier = int(request.url.params["iInfoId"])
            return httpx.Response(200, json={"retcode": 0, "data": hoyo_item(identifier,
                sContent='<p>《崩坏：星穹铁道》×《绝区零》联动</p><p>4.8版本上线</p>'
                         '<script>hidden</script><p><a href="https://sr.mihoyo.com/news/101">公告入口</a></p>'
                         '<img src="https://fastcdn.mihoyo.com/poster.png">')})

        batch = await self.fetch(handler, max_pages=2)
        self.assertEqual(len(batch.items), 2)
        item = batch.items[0].observation
        self.assertEqual(item.id, "website-honkai-star-rail-official-101")
        self.assertEqual(item.source.kind, SourceKind.WEBSITE)
        self.assertEqual(str(item.source.url), "https://sr.mihoyo.com/news/101")
        self.assertEqual(item.source.published_at.utcoffset(), timedelta(hours=8))
        self.assertIn("4.8版本上线", item.text)
        self.assertNotIn("hidden", item.text)
        self.assertEqual(list(map(str, item.media_urls)), ["https://fastcdn.mihoyo.com/poster.png"])
        self.assertEqual(list(map(str, item.outbound_urls)), ["https://sr.mihoyo.com/news/101"])
        self.assertEqual(batch.cursor.latest_post_id, "101")
        self.assertEqual(len(requests), 4)

    async def test_boundary_is_refetched_and_old_pins_do_not_stop_the_scan(self):
        requests = []

        def handler(request):
            requests.append(request)
            if request.url.path.endswith("/getContentList"):
                return httpx.Response(200, json={"retcode": 0, "data": {
                    "iTotal": 50, "list": [hoyo_item(9, 1), hoyo_item(102, 23), hoyo_item(101), hoyo_item(100, 21)]}})
            return httpx.Response(200, json={"retcode": 0, "data": hoyo_item(int(request.url.params["iInfoId"]), sContent="<p>更新后的正文</p>")})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            batch = await WebsiteTimelineClient(client).fetch_account(
                account_id="honkai-star-rail-official", account_name="官方公告",
                cursor=WebsiteCursor(latest_post_id="101"), first_seen_at=NOW,
                published_since=NOW-timedelta(days=2))
        self.assertEqual({i.observation.source.canonical_content_id for i in batch.items}, {"102", "101"})
        self.assertEqual(batch.cursor.latest_post_id, "102")
        self.assertEqual(len(requests), 3)
        self.assertEqual(set(batch.cursor.model_dump()), {"latest_post_id"})

    async def test_arknights_reads_article_paragraphs_and_bundle_dates(self):
        def handler(request):
            if request.url.path == "/api/news":
                self.assertEqual(request.url.params["category"], "LATEST")
                return httpx.Response(200, json={"code": 0, "data": {"list": [{
                    "cid": "1454", "title": "【明日方舟×国家图书馆】恒远津梁",
                    "displayTime": int(NOW.timestamp())}], "end": True, "total": 1}})
            self.assertEqual(request.url.path, "/news/1454.html")
            return httpx.Response(200, text='<header><p>导航</p></header><div>菜单</div>'
                '<div><p><strong>博览群书组合包</strong></p><p>10月16日 16:00 - 11月06日 03:59</p>'
                '<img src="https://web.hycdn.cn/upload/image/bundle.jpg"></div>'
                '<script>tracking</script><footer><p>版权</p></footer>')

        batch = await self.fetch(handler, account="arknights-official")
        item = batch.items[0].observation
        self.assertIn("博览群书组合包\n10月16日", item.text)
        self.assertNotIn("导航", item.text)
        self.assertNotIn("菜单", item.text)
        self.assertNotIn("tracking", item.text)
        self.assertNotIn("版权", item.text)
        self.assertEqual(list(map(str, item.media_urls)), ["https://web.hycdn.cn/upload/image/bundle.jpg"])

    async def test_all_three_hoyo_sites_use_their_own_app_and_channel(self):
        cases = (
            ("genshin-impact-official", "16471662a82d418a", "719", "ys.mihoyo.com/main/news/detail/10"),
            ("honkai-star-rail-official", "1963de8dc19e461c", "255", "sr.mihoyo.com/news/10"),
            ("zenless-zone-zero-official", "706fd13a87294881", "273", "zzz.mihoyo.com/news/10"),
        )
        for account, app, channel, url in cases:
            with self.subTest(account=account):
                def handler(request):
                    self.assertIn(app, request.url.path)
                    if request.url.path.endswith("/getContentList"):
                        self.assertEqual(request.url.params["iChanId"], channel)
                        data = {"iTotal": 1, "list": [hoyo_item(10)]}
                    else:
                        data = hoyo_item(10, sContent="<p>完整公告</p>")
                    return httpx.Response(200, json={"retcode": 0, "data": data})
                batch = await self.fetch(handler, account=account)
                self.assertEqual(str(batch.items[0].observation.source.url), "https://"+url)

    async def test_failures_are_redacted_and_do_not_return_partial_batches(self):
        for response in (httpx.Response(503, text="private-server-message"),
                         httpx.Response(200, json={"retcode": 1001, "message": "private-server-message"}),
                         httpx.Response(200, json={"retcode": 0, "data": {"iTotal": "private-server-message", "list": [hoyo_item(1)]}}),
                         httpx.Response(200, json={"retcode": 0, "data": {"iTotal": 1, "list": "broken"}})):
            with self.subTest(response=response):
                with self.assertRaises(SourceAccessError) as caught:
                    await self.fetch(lambda request: response)
                self.assertNotIn("private-server-message", str(caught.exception))

    async def test_date_window_skips_old_details_and_limits_history(self):
        details = []
        def handler(request):
            if request.url.path.endswith('/getContentList'):
                return httpx.Response(200, json={'retcode': 0, 'data': {
                    'iTotal': 3, 'list': [hoyo_item(3, 23), hoyo_item(2, 22), hoyo_item(1, 1)]}})
            details.append(request.url.params['iInfoId'])
            return httpx.Response(200, json={'retcode': 0, 'data': hoyo_item(2, sContent='<p>公告</p>')})
        batch = await self.fetch(handler, published_before=datetime(2026, 10, 23, tzinfo=timezone.utc))
        self.assertEqual(details, ['2'])
        self.assertEqual(len(batch.items), 1)

    async def test_invalid_pagination_is_a_source_failure_before_details(self):
        requests = []
        def handler(request):
            requests.append(request)
            if request.url.path.endswith('/getContentList'):
                return httpx.Response(200, json={'retcode': 0, 'data': {
                    'iTotal': 'invalid-total', 'list': [hoyo_item(1)]}})
            return httpx.Response(200, json={'retcode': 0, 'data': hoyo_item(1, sContent='<p>正文</p>')})
        with self.assertRaises(SourceAccessError):
            await self.fetch(handler)
        self.assertEqual(len(requests), 1)

    async def test_rejects_unknown_site_and_naive_date_without_requests(self):
        def unexpected(request):
            self.fail("invalid parameters must not request a source")
        async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
            adapter = WebsiteTimelineClient(client)
            for account, since in (("unknown", NOW), ("arknights-official", NOW.replace(tzinfo=None))):
                with self.subTest(account=account), self.assertRaises(ValueError):
                    await adapter.fetch_account(account_id=account, account_name="官方公告",
                        cursor=None, first_seen_at=NOW, published_since=since)
