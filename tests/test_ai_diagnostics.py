import unittest
import httpx
from herald.ai import OpenAICompatibleProvider, AIProviderError, ExtractionResult


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_is_logged_without_exception_text(self):
        def handler(request):
            self.assertEqual(request.extensions['timeout']['read'], 180)
            self.assertEqual(request.extensions['timeout']['connect'], 15)
            self.assertEqual(request.extensions['timeout']['write'], 60)
            raise httpx.ReadTimeout('SECRET https://private', request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAICompatibleProvider(client=client, base_url='https://example.test', model='test', api_key='SECRET', max_attempts=1)
            with self.assertRaises(AIProviderError):
                await provider._complete({}, ExtractionResult)
            entry = provider.diagnostics[0]
            self.assertEqual(entry['exception_type'], 'ReadTimeout')
            self.assertEqual(entry['stage'], 'request')
            self.assertNotIn('SECRET', str(entry))

    async def test_http_status_and_schema_failure_are_distinct(self):
        for status, body, expected_stage in [(429, {}, 'http_status'), (200, {'choices':[{'message':{'content':'{}'}}]}, 'schema')]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status, json=body))) as client:
                provider = OpenAICompatibleProvider(client=client, base_url='https://example.test', model='test', api_key='SECRET', max_attempts=1)
                with self.assertRaises(AIProviderError):
                    await provider._complete({}, ExtractionResult)
                self.assertEqual(provider.diagnostics[0]['stage'], expected_stage)
                self.assertEqual(provider.diagnostics[0]['http_status'], status)
