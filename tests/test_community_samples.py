import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock

import httpx

from herald.community_samples import collect
from herald.registry import IpRegistry
from herald.sources.base import FetchedObservation
from test_dedupe import make_observation
from test_merge import BASE


class CommunitySampleTests(unittest.IsolatedAsyncioTestCase):
    async def test_records_public_responses_and_adapter_output_not_auth(self):
        original_client = httpx.AsyncClient
        def handler(request):
            return httpx.Response(200, json={'public': '公告'} if 'getPostFull' in request.url.path else {'token': 'private-device-token'})
        def factory(**kwargs):
            return original_client(transport=httpx.MockTransport(handler), **kwargs)
        async def fetch(self, **kwargs):
            assert 'detail_predicate' not in kwargs
            await self.client.get('https://zonai.skland.com/web/v1/auth/refresh')
            await self.client.get('https://bbs-api.miyoushe.com/post/wapi/getPostFull?post_id=1')
            await self._fetch_detail(post_id='1')
        item = FetchedObservation(observation=make_observation('1', text='联动公告', account='official'))
        registry = IpRegistry([IpRegistry.load_builtin().entries[0]])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'capture'
            with patch('herald.community_samples.httpx.AsyncClient', side_effect=factory), patch('herald.community_samples.asyncio.sleep', new=AsyncMock()), patch('herald.community_samples.IpRegistry.load_builtin', return_value=registry), patch('herald.community_samples.MiyousheTimelineClient.fetch_account', new=fetch), patch('herald.community_samples.MiyousheTimelineClient._fetch_detail', new=AsyncMock(return_value=item)):
                await collect(root, BASE, 2)
            paths = list(root.rglob('raw-responses.json'))
            self.assertEqual(len(paths), 1)
            records = json.loads(paths[0].read_text(encoding='utf-8'))
            self.assertEqual(len(records), 1)
            self.assertNotIn('private-device-token', paths[0].read_text(encoding='utf-8'))
            output = json.loads(next(root.rglob('observations.json')).read_text(encoding='utf-8'))
            self.assertEqual(output['observations'][0]['text'], '联动公告')
