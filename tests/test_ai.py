from __future__ import annotations

import base64
import json
import unittest
from datetime import datetime, timezone

import httpx

from herald.ai import (
    AIProviderError,
    ExtractedAction,
    ExtractedActivity,
    ExtractedClaim,
    ExtractionInput,
    ExtractionResult,
    MockAIProvider,
    OpenAICompatibleProvider,
    ZhipuOpenAIProvider,
)
from herald.models import ActionKind, ActivityKind


UTC = timezone.utc
NOW = datetime(2026, 8, 30, 12, tzinfo=UTC)


def packet() -> ExtractionInput:
    return ExtractionInput(
        observation_id="weibo-a",
        platform="weibo",
        account_name="原神官方微博",
        published_at=NOW,
        text="原神与示例品牌联动，9月8日10:00开售",
        ocr_text=["品牌小程序"],
        media_urls=["https://img.example/poster.jpg"],
        source_url="https://weibo.com/1/a",
        ip_slug_hint="genshin-impact",
        ip_name_hint="原神",
    )


def result() -> ExtractionResult:
    return ExtractionResult(
        relevant=True,
        campaign_title="原神 × 示例品牌",
        partner="示例品牌",
        activities=[
            ExtractedActivity(
                kind=ActivityKind.PRODUCT,
                title="全国产品联动",
                actions=[
                    ExtractedAction(
                        kind=ActionKind.SALE_OPEN,
                        title="联动商品开售",
                        at="2026-09-08T10:00:00+08:00",
                        requires_rush=True,
                    )
                ],
            )
        ],
        claims=[
            ExtractedClaim(
                field_path="activities[0].actions[0].at",
                quote="9月8日10:00开售",
                confidence=1,
            )
        ],
    )


def historical_collaboration_packet() -> ExtractionInput:
    return ExtractionInput(
        observation_id="weibo-historical-collaboration",
        platform="weibo",
        account_name="原神",
        published_at="2026-08-16T12:00:00+08:00",
        text=(
            "原神 × 美团丨大众点评联名活动正式开启："
            "2026年8月16日美团快闪预约开启，"
            "8月19日大众点评打卡活动开启，"
            "8月21日美团原神专属会场开启。"
        ),
        source_url="https://weibo.com/6593199887/RdDIG4Sea",
        ip_slug_hint="genshin-impact",
        ip_name_hint="原神",
    )


def historical_collaboration_result() -> ExtractionResult:
    return ExtractionResult(
        relevant=True,
        campaign_title="原神 × 美团丨大众点评",
        partner="美团丨大众点评",
        activities=[
            ExtractedActivity(
                kind=ActivityKind.POPUP,
                title="美团快闪预约",
                actions=[
                    ExtractedAction(
                        kind=ActionKind.RESERVATION_OPEN,
                        title="美团快闪预约开启",
                    )
                ],
            ),
            ExtractedActivity(
                kind=ActivityKind.ONLINE,
                title="大众点评打卡活动",
                actions=[
                    ExtractedAction(
                        kind=ActionKind.EVENT_START,
                        title="大众点评打卡活动开启",
                    )
                ],
            ),
            ExtractedActivity(
                kind=ActivityKind.ONLINE,
                title="美团原神专属会场",
                actions=[
                    ExtractedAction(
                        kind=ActionKind.EVENT_START,
                        title="美团原神专属会场开启",
                    )
                ],
            ),
        ],
        claims=[
            ExtractedClaim(
                field_path="partner",
                quote="原神 × 美团丨大众点评联名活动",
                confidence=1,
            )
        ],
        uncertainties=["公告只给出日期，未给出三个节点的具体时刻。"],
    )


class MockAIProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_stable_fixture_copy(self) -> None:
        provider = MockAIProvider({"weibo-a": result()})

        first = await provider.extract(packet())
        second = await provider.extract(packet())

        self.assertEqual(first, second)
        self.assertIsNot(first, second)

    async def test_historical_collaboration_fixture_keeps_unknown_times_null(self) -> None:
        packet = historical_collaboration_packet()
        provider = MockAIProvider(
            {packet.observation_id: historical_collaboration_result()}
        )

        extracted = await provider.extract(packet)

        self.assertTrue(extracted.relevant)
        self.assertEqual(extracted.partner, "美团丨大众点评")
        self.assertEqual(len(extracted.activities), 3)
        self.assertTrue(
            all(
                action.at is None
                for activity in extracted.activities
                for action in activity.actions
            )
        )
        self.assertIn("未给出", extracted.uncertainties[0])


class OpenAICompatibleProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_contains_only_public_packet_fields(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["authorization"] = request.headers.get("Authorization")
            captured["body"] = request.content.decode("utf-8")
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": result().model_dump_json()}}
                    ]
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            provider = OpenAICompatibleProvider(
                client=http_client,
                base_url="https://free-api.example/v1",
                model="free-model",
                api_key="private-api-key",
            )
            extracted = await provider.extract(packet())

        body = str(captured["body"])
        self.assertEqual(extracted.partner, "示例品牌")
        self.assertEqual(captured["authorization"], "Bearer private-api-key")
        self.assertNotIn("private-api-key", body)
        for forbidden in (
            "ORIGIN_CITY",
            "REACHABLE_CITIES",
            "NOTIFY_EMAIL",
            "WEIBO_COOKIE",
            "SMTP_PASSWORD",
        ):
            self.assertNotIn(forbidden, body)

    async def test_json_code_fence_is_accepted(self) -> None:
        fenced = "```json\n" + result().model_dump_json() + "\n```"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"choices": [{"message": {"content": fenced}}]}
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            provider = OpenAICompatibleProvider(
                client=http_client,
                base_url="https://api.example/v1",
                model="model",
                api_key="key",
            )
            extracted = await provider.extract(packet())

        self.assertTrue(extracted.relevant)

    async def test_vision_request_fetches_public_poster_into_a_data_url(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                captured["image_referer"] = request.headers.get("Referer")
                captured["image_user_agent"] = request.headers.get("User-Agent")
                return httpx.Response(
                    200,
                    headers={"Content-Type": "image/jpeg"},
                    content=b"public-poster-bytes",
                )
            captured.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": result().model_dump_json()}}]},
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            provider = OpenAICompatibleProvider(
                client=http_client,
                base_url="https://vision.example/v1",
                model="vision-model",
                api_key="private-key",
                supports_vision=True,
            )
            await provider.extract(packet())

        content = captured["messages"][1]["content"]
        image_parts = [part for part in content if part["type"] == "image_url"]
        data_url = image_parts[0]["image_url"]["url"]
        prefix, encoded = data_url.split(",", 1)
        self.assertEqual(prefix, "data:image/jpeg;base64")
        self.assertEqual(base64.b64decode(encoded), b"public-poster-bytes")
        self.assertEqual(captured["image_referer"], "https://m.weibo.cn/")
        self.assertIn(
            "HERALD",
            str(captured["image_user_agent"]),
        )

    async def test_vision_image_fetch_failure_is_redacted(self) -> None:
        provider_called = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal provider_called
            if request.method == "GET":
                return httpx.Response(403, text="private upstream response")
            provider_called = True
            return httpx.Response(500)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            provider = OpenAICompatibleProvider(
                client=http_client,
                base_url="https://vision.example/v1",
                model="vision-model",
                api_key="private-key",
                supports_vision=True,
            )
            with self.assertRaisesRegex(
                AIProviderError, "AI image preparation failed"
            ) as raised:
                await provider.extract(packet())

        self.assertFalse(provider_called)
        self.assertNotIn("private upstream response", str(raised.exception))
        self.assertNotIn("private-key", str(raised.exception))

    async def test_zhipu_dialect_uses_a_supported_nonzero_temperature(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": result().model_dump_json()}}]},
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            provider = ZhipuOpenAIProvider(
                client=http_client,
                base_url="https://open.bigmodel.cn/api/paas/v4",
                model="glm-4.6v-flash",
                api_key="private-key",
            )
            extracted = await provider.extract(packet())

        self.assertTrue(extracted.relevant)
        self.assertEqual(provider.provider_name, "zhipu_openai")
        self.assertGreater(captured["temperature"], 0)
        self.assertLess(captured["temperature"], 1)
        self.assertEqual(captured["response_format"], {"type": "json_object"})

    async def test_zhipu_rate_limit_is_deferred_instead_of_retried_immediately(self) -> None:
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(
                429,
                json={"error": {"code": "1305", "message": "provider overloaded"}},
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            provider = ZhipuOpenAIProvider(
                client=http_client,
                base_url="https://open.bigmodel.cn/api/paas/v4",
                model="glm-4.6v-flash",
                api_key="private-key",
                max_attempts=3,
            )
            with self.assertRaisesRegex(AIProviderError, "redacted retries"):
                await provider.extract(packet())

        self.assertEqual(attempts, 1)

    async def test_invalid_output_is_retried_then_redacted(self) -> None:
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": "not-json with private response"}}
                    ]
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            provider = OpenAICompatibleProvider(
                client=http_client,
                base_url="https://api.example/v1",
                model="model",
                api_key="private-key",
                max_attempts=2,
            )
            with self.assertRaisesRegex(AIProviderError, "redacted retries") as context:
                await provider.extract(packet())

        self.assertEqual(attempts, 2)
        self.assertNotIn("private-key", str(context.exception))
        self.assertNotIn("private response", str(context.exception))

    def test_extraction_input_cannot_accept_private_profile_fields(self) -> None:
        payload = packet().model_dump(mode="json")
        payload["notify_email"] = "private@example.com"

        with self.assertRaises(Exception):
            ExtractionInput.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
