"""AI extraction contracts and an OpenAI-compatible implementation.

The provider sees only public source material. It never receives RuntimeSettings
or any notification, location, cookie, or SMTP configuration.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Protocol

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, ValidationError

from .models import ActionKind, ActivityKind


CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class ExtractionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: str
    platform: str
    account_name: str
    published_at: AwareDatetime
    text: str
    ocr_text: list[str] = Field(default_factory=list)
    media_urls: list[HttpUrl] = Field(default_factory=list)
    external_links: list[HttpUrl] = Field(default_factory=list)
    source_url: HttpUrl
    ip_slug_hint: str
    ip_name_hint: str


class ExtractedVenue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    country: str = "CN"
    province: str | None = None
    city: str | None = None
    address: str | None = None
    online_platform: str | None = None
    business_hours: str | None = None
    nationwide: bool = False


class ExtractedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ActionKind
    title: str
    at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None
    platform: str | None = None
    url: HttpUrl | None = None
    requires_reservation: bool | None = None
    requires_rush: bool | None = None


class ExtractedActivity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ActivityKind
    title: str
    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None
    venues: list[ExtractedVenue] = Field(default_factory=list)
    actions: list[ExtractedAction] = Field(default_factory=list)


class ExtractedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_path: str
    quote: str = Field(min_length=1, max_length=1000)
    confidence: float = Field(ge=0, le=1)


class ExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relevant: bool
    campaign_title: str | None = None
    partner: str | None = None
    activities: list[ExtractedActivity] = Field(default_factory=list)
    claims: list[ExtractedClaim] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)


class AIProviderError(RuntimeError):
    """A redacted failure safe for run reports."""


class AIProvider(Protocol):
    provider_name: str
    model_name: str

    async def extract(self, packet: ExtractionInput) -> ExtractionResult: ...


class MockAIProvider:
    provider_name = "mock"
    model_name = "fixture"

    def __init__(self, results: dict[str, ExtractionResult]) -> None:
        self.results = results

    async def extract(self, packet: ExtractionInput) -> ExtractionResult:
        try:
            return self.results[packet.observation_id].model_copy(deep=True)
        except KeyError as exc:
            raise AIProviderError("no mock extraction fixture for source item") from exc


class OpenAICompatibleProvider:
    provider_name = "openai_compatible"

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str,
        model: str,
        api_key: str,
        supports_vision: bool = False,
        supports_json_object: bool = True,
        temperature: float = 0,
        max_attempts: int = 3,
    ) -> None:
        self.client = client
        self.base_url = base_url.rstrip("/")
        self.model_name = model
        self.api_key = api_key
        self.supports_vision = supports_vision
        self.supports_json_object = supports_json_object
        self.temperature = temperature
        self.max_attempts = max(1, max_attempts)

    async def extract(self, packet: ExtractionInput) -> ExtractionResult:
        request_payload = self._request_payload(packet)
        last_error: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                response = await self.client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=request_payload,
                    timeout=60,
                )
                response.raise_for_status()
                content = self._response_content(response.json())
                return ExtractionResult.model_validate_json(self._strip_fence(content))
            except (httpx.HTTPError, ValueError, KeyError, TypeError, ValidationError) as exc:
                last_error = exc
                if attempt + 1 < self.max_attempts:
                    retry_delay = self._retry_delay(exc, attempt)
                    if retry_delay is None:
                        break
                    await asyncio.sleep(retry_delay)
        raise AIProviderError("AI extraction failed after redacted retries") from last_error

    def _retry_delay(self, exc: Exception, attempt: int) -> float | None:
        return 0.25 * (2**attempt)

    def _request_payload(self, packet: ExtractionInput) -> dict[str, Any]:
        public_packet = packet.model_dump(mode="json")
        user_payload = json.dumps(
            {
                "source": public_packet,
                "output_schema": ExtractionResult.model_json_schema(),
            },
            ensure_ascii=False,
        )
        user_content: str | list[dict[str, Any]] = user_payload
        if self.supports_vision and packet.media_urls:
            user_content = [{"type": "text", "text": user_payload}]
            user_content.extend(
                {
                    "type": "image_url",
                    "image_url": {"url": str(url)},
                }
                for url in packet.media_urls
            )

        payload: dict[str, Any] = {
            "model": self.model_name,
            "temperature": self.temperature,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是游戏联动公告结构化提取器。只依据用户提供的公开公告材料输出 JSON。"
                        "不得猜测；不确定信息写入 uncertainties。每个确定事实必须在 claims 中提供原文 quote。"
                        "日期时间使用带时区的 ISO 8601。输出必须符合给定结构，不要输出解释或 Markdown。"
                    ),
                },
                {
                    "role": "user",
                    "content": user_content,
                },
            ],
        }
        if self.supports_json_object:
            payload["response_format"] = {"type": "json_object"}
        return payload

    @staticmethod
    def _response_content(payload: dict[str, Any]) -> str:
        choices = payload["choices"]
        content = choices[0]["message"]["content"]
        if not isinstance(content, str):
            raise TypeError("AI response content was not text")
        return content

    @staticmethod
    def _strip_fence(content: str) -> str:
        stripped = content.strip()
        if stripped.startswith("```"):
            stripped = CODE_FENCE.sub("", stripped).strip()
        return stripped


class ZhipuOpenAIProvider(OpenAICompatibleProvider):
    """Zhipu's OpenAI-compatible dialect with provider-specific parameters."""

    provider_name = "zhipu_openai"

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str,
        model: str,
        api_key: str,
        supports_vision: bool = False,
        supports_json_object: bool = True,
        max_attempts: int = 3,
    ) -> None:
        super().__init__(
            client=client,
            base_url=base_url,
            model=model,
            api_key=api_key,
            supports_vision=supports_vision,
            supports_json_object=supports_json_object,
            temperature=0.1,
            max_attempts=max_attempts,
        )

    def _retry_delay(self, exc: Exception, attempt: int) -> float | None:
        if (
            isinstance(exc, httpx.HTTPStatusError)
            and exc.response.status_code == 429
        ):
            return None
        return super()._retry_delay(exc, attempt)
