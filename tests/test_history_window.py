import unittest
from datetime import timedelta
from unittest.mock import AsyncMock

from herald.sources.skland import SklandTimelineClient
from test_skland_source import NOW


class HistoryWindowTests(unittest.IsolatedAsyncioTestCase):
    async def test_skland_upper_bound_and_exhaustion(self):
        adapter = SklandTimelineClient(None)
        adapter._signed_get = AsyncMock(return_value={'data': {'list': [
            {'id': 'new', 'publishedAtTs': int(NOW.timestamp())}], 'hasMore': False}})
        adapter._fetch_detail = AsyncMock()
        result = await adapter.fetch_account(account_id='official', account_name='官方',
            cursor=None, first_seen_at=NOW, published_since=NOW-timedelta(days=61),
            published_before=NOW)
        adapter._fetch_detail.assert_not_awaited()
        self.assertEqual(result.items, ())
        self.assertTrue(adapter.last_fetch_complete)

    async def test_page_cap_is_not_complete(self):
        adapter = SklandTimelineClient(None)
        adapter._signed_get = AsyncMock(return_value={'data': {'list': [
            {'id': 'new', 'publishedAtTs': int(NOW.timestamp())}], 'hasMore': True, 'pageToken': 'next'}})
        await adapter.fetch_account(account_id='official', account_name='官方', cursor=None,
            first_seen_at=NOW, published_since=NOW-timedelta(days=61), published_before=NOW, max_pages=1)
        self.assertFalse(adapter.last_fetch_complete)
