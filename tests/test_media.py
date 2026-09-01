from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import httpx

from herald.media import PublicMediaCache


class PublicMediaCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_downloads_public_image_with_weibo_referer_and_writes_index(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                headers={"Content-Type": "image/jpeg"},
                content=b"public-image-bytes",
            )

        with tempfile.TemporaryDirectory() as temporary:
            page = Path(temporary)
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            ) as client:
                result = await PublicMediaCache(client).cache(
                    ["https://wx1.sinaimg.cn/mw2000/poster.jpg"],
                    page,
                )

            self.assertEqual(result.failed_count, 0)
            asset = result.assets["https://wx1.sinaimg.cn/mw2000/poster.jpg"]
            self.assertEqual((page / asset.asset_path).read_bytes(), b"public-image-bytes")
            self.assertEqual(requests[0].headers["Referer"], "https://m.weibo.cn/")
            index = json.loads(
                (page / "data/media-index.json").read_text(encoding="utf-8")
            )
            self.assertEqual(index["assets"][0]["asset_path"], asset.asset_path)

    async def test_reuses_indexed_asset_without_another_request(self) -> None:
        calls = 0

        def first_handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                200,
                headers={"Content-Type": "image/png"},
                content=b"cached-image",
            )

        def unexpected_handler(request: httpx.Request) -> httpx.Response:
            self.fail("cached public media should not be downloaded twice")

        with tempfile.TemporaryDirectory() as temporary:
            page = Path(temporary)
            url = "https://wx1.sinaimg.cn/mw2000/poster.png"
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(first_handler)
            ) as client:
                await PublicMediaCache(client).cache([url], page)
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(unexpected_handler)
            ) as client:
                result = await PublicMediaCache(client).cache([url], page)

            self.assertEqual(calls, 1)
            self.assertEqual(result.failed_count, 0)
            self.assertIn(url, result.assets)

    async def test_failure_is_counted_without_response_or_url_in_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text="private upstream response")

        with tempfile.TemporaryDirectory() as temporary:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            ) as client:
                result = await PublicMediaCache(client).cache(
                    ["https://wx1.sinaimg.cn/mw2000/poster.jpg"],
                    Path(temporary),
                )

        self.assertEqual(result.assets, {})
        self.assertEqual(result.failed_count, 1)


if __name__ == "__main__":
    unittest.main()
