import unittest
import json
import traceback
import httpx
from herald.ai import OpenAICompatibleProvider, AIProviderError, ExtractionResult
from herald.ai_diagnostics import safe_diagnostic


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def test_unknown_metadata_is_replaced_and_numeric_fields_are_bounded(self):
        entry = safe_diagnostic({'operation': 'PRIVATE', 'stage': 'PRIVATE', 'code': 'PRIVATE',
            'exception_type': 'PRIVATE', 'finish_reason': 'PRIVATE', 'request': 'PRIVATE',
            'usage': {'prompt_tokens': 10**1000, 'completion_tokens': float('inf')},
            'validation_errors': [{'type': 'PRIVATE', 'field': 'PRIVATE', 'input': 'PRIVATE'}]})
        self.assertNotIn('PRIVATE', json.dumps(entry))
        self.assertEqual(entry['usage'], {})

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

    async def test_failure_keeps_safe_attempts_without_chained_private_error(self):
        def handler(request):
            raise httpx.ReadTimeout('PRIVATE-KEY PRIVATE-CITY https://private.test', request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAICompatibleProvider(client=client, base_url='https://private.test',
                model='test', api_key='PRIVATE-KEY', max_attempts=2)
            with self.assertRaises(AIProviderError) as caught:
                await provider._complete({}, ExtractionResult)
        error = caught.exception
        self.assertEqual(len(error.diagnostics), 2)
        self.assertEqual(error.diagnostics[-1]['operation'], 'extract')
        self.assertEqual(error.diagnostics[-1]['code'], 'transport_error')
        visible = ''.join(traceback.format_exception(error)) + json.dumps(error.diagnostics)
        for private in ('PRIVATE-KEY', 'PRIVATE-CITY', 'private.test'):
            self.assertNotIn(private, visible)

    async def test_truncation_invalid_json_and_schema_are_distinguishable(self):
        cases = [
            ({'choices': [{'finish_reason': 'length', 'message': {'content': '{}'}}]},
             'completion', 'incomplete_response', 'length'),
            ({'choices': [{'finish_reason': 'stop', 'message': {'content': '{'}}]},
             'schema', 'invalid_json', 'stop'),
            ({'choices': [{'message': {'content': '{}'}}]},
             'schema', 'schema_validation', None),
            ({'choices': []}, 'response_shape', 'invalid_response_shape', None),
        ]
        for body, stage, code, finish in cases:
            with self.subTest(code=code):
                async with httpx.AsyncClient(transport=httpx.MockTransport(
                        lambda r: httpx.Response(200, json=body))) as client:
                    provider = OpenAICompatibleProvider(client=client, base_url='https://example.test',
                        model='test', api_key='fixture', max_attempts=1)
                    with self.assertRaises(AIProviderError) as caught:
                        await provider._complete({}, ExtractionResult)
                entry = caught.exception.diagnostics[-1]
                self.assertEqual((entry['stage'], entry['code']), (stage, code))
                self.assertEqual(entry['finish_reason'], finish)

    async def test_untrusted_metadata_and_validation_values_never_enter_diagnostics(self):
        contents = [json.dumps({'relevant': True, 'PRIVATE-CITY': 'PRIVATE-KEY',
                                 'activities': [{'kind': 'PRIVATE-KEY'}]}),
                    json.dumps({'relevant': False})]
        for content in contents:
            body = {'choices': [{'message': {'content': content}}],
                    'usage': {'prompt_tokens': 123, 'completion_tokens': 7,
                              'total_tokens': 'PRIVATE-KEY', 'PRIVATE-CITY': 'PRIVATE-KEY'}}
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                    lambda r: httpx.Response(200, json=body))) as client:
                provider = OpenAICompatibleProvider(client=client, base_url='https://example.test',
                    model='test', api_key='fixture', max_attempts=1)
                try:
                    await provider._complete({}, ExtractionResult)
                except AIProviderError as error:
                    self.assertIn('validation_errors', error.diagnostics[-1])
                    self.assertIn({'type': 'extra_forbidden', 'field': 'other'},
                                  error.diagnostics[-1]['validation_errors'])
            output = json.dumps(provider.diagnostics)
            self.assertNotIn('PRIVATE-KEY', output)
            self.assertNotIn('PRIVATE-CITY', output)
            self.assertEqual(provider.diagnostics[-1]['usage'],
                             {'prompt_tokens': 123, 'completion_tokens': 7})

    async def test_http_status_and_schema_failure_are_distinct(self):
        for status, body, expected_stage in [(429, {}, 'http_status'), (200, {'choices':[{'message':{'content':'{}'}}]}, 'schema')]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status, json=body))) as client:
                provider = OpenAICompatibleProvider(client=client, base_url='https://example.test', model='test', api_key='SECRET', max_attempts=1)
                with self.assertRaises(AIProviderError):
                    await provider._complete({}, ExtractionResult)
                self.assertEqual(provider.diagnostics[0]['stage'], expected_stage)
                self.assertEqual(provider.diagnostics[0]['http_status'], status)
