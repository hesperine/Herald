import json
import unittest
import httpx
from herald.ai import OpenAICompatibleProvider, AIProviderError
from herald.contextual_ai import route_campaigns, resolve_campaign
from tests.test_ai import packet


class ContextualAITests(unittest.IsolatedAsyncioTestCase):
    async def test_unchanged_existing_dates_need_not_be_reextracted(self):
        from herald.contextual_ai import CampaignRoute
        from herald.models import Campaign
        from tests.test_pipeline import NOW
        answer = {'extraction': {'relevant': True, 'activities': [{'kind':'popup','title':'已知快闪','start_date':'2026-10-26'}]},
                  'merge': {'decision': 'update'}}
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200,
            json={'choices':[{'message':{'content':json.dumps(answer)}}]}))) as client:
            provider = OpenAICompatibleProvider(client=client, base_url='https://example.invalid', model='test', api_key='private')
            existing = Campaign(id='existing', ip_slug='genshin-impact', ip_name='原神', title='已知企划', first_seen_at=NOW, updated_at=NOW)
            result = await resolve_campaign(provider, CampaignRoute(phrase='已知企划', campaign_id='existing', observation_ids=['weibo-a']), [packet()], existing)
            self.assertEqual(result.merge.updates, [])
            self.assertEqual(result.extraction.activities, [])
    def test_contextual_dates_require_field_evidence(self):
        from herald.contextual_ai import validate_dates
        from herald.ai import ExtractionResult, ExtractedActivity
        extraction = ExtractionResult(relevant=True, activities=[ExtractedActivity(
            kind='popup', title='快闪', start_date='2026-10-26')])
        with self.assertRaises(ValueError):
            validate_dates(extraction)

    def test_explicit_utc_deadline_in_cited_rules_becomes_action_end(self):
        from herald.contextual_ai import complete_explicit_deadlines
        from herald.ai import ExtractionResult, ExtractedActivity, ExtractedAction, ExtractedClaim
        from tests.test_pipeline import observation
        quote = '2026/11/15 00:00（UTC+8）前登录游戏'
        item = observation('a', '原神', digest='a'*64)
        item.text = quote
        result = ExtractionResult(relevant=True, activities=[ExtractedActivity(kind='in_game', title='奖励',
            actions=[ExtractedAction(kind='gift', title='领取', rules=quote)])],
            claims=[ExtractedClaim(field_path='activities[0].actions[0].rules', quote=quote, confidence=1)])
        complete_explicit_deadlines(result, [item])
        self.assertEqual(result.activities[0].actions[0].end_at.isoformat(), '2026-11-15T00:00:00+08:00')
        self.assertEqual(result.claims[-1].field_path, 'activities[0].actions[0].end_at')

    def test_uncited_or_ambiguous_deadline_is_not_inferred(self):
        from herald.contextual_ai import complete_explicit_deadlines
        from herald.ai import ExtractionResult, ExtractedActivity, ExtractedAction
        result = ExtractionResult(relevant=True, activities=[ExtractedActivity(kind='in_game', title='奖励',
            actions=[ExtractedAction(kind='gift', title='领取', rules='11月15日前登录游戏')])])
        complete_explicit_deadlines(result, [])
        self.assertIsNone(result.activities[0].actions[0].end_at)
    async def test_route_requires_complete_exclusive_coverage(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200,
            json={'choices': [{'message': {'content': '{"groups":[],"unconfirmed":[]}'}}]}))) as client:
            provider = OpenAICompatibleProvider(client=client, base_url='https://example.invalid', model='test', api_key='private')
            with self.assertRaises(AIProviderError):
                await route_campaigns(provider, [packet()], [])

    async def test_route_multi_campaign_post_without_images(self):
        def handler(request):
            body = json.loads(request.content)
            self.assertNotIn('https://img.example', json.dumps(body))
            answer = {'groups': [{'phrase': s, 'observation_ids': ['weibo-a']} for s in ('原神品牌甲', '原神品牌乙')], 'unconfirmed': []}
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(answer)}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAICompatibleProvider(client=client, base_url='https://example.invalid', model='test', api_key='private')
            result = await route_campaigns(provider, [packet()], [])
            self.assertEqual(len(result.groups), 2)

    async def test_truncated_completion_never_accepted(self):
        from herald.contextual_ai import RoutingResult
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200,
            json={'choices': [{'finish_reason': 'length', 'message': {'content': '{"groups":[],"unconfirmed":[]}'}}]}))) as client:
            provider = OpenAICompatibleProvider(client=client, base_url='https://example.invalid', model='test', api_key='private', max_attempts=1)
            with self.assertRaises(AIProviderError):
                await provider._complete({}, RoutingResult)
