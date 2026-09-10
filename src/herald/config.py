"""Runtime configuration loaded from GitHub Variables and Secrets."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Literal
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


AI_BASE_URLS = {
    "openai_compatible": "https://api.openai.com/v1",
    "zhipu_openai": "https://open.bigmodel.cn/api/paas/v4",
}


def _split_list(value: str | None) -> list[str]:
    if not value:
        return []
    normalized = value.replace("\r", "\n").replace(",", "\n")
    return [item.strip() for item in normalized.split("\n") if item.strip()]


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean value: {value!r}")


def _parse_initial_lookback_days(value: str | None) -> int:
    raw = "21" if value is None or not value.strip() else value.strip()
    try:
        days = int(raw)
    except ValueError as exc:
        raise ValueError(
            "INITIAL_LOOKBACK_DAYS must be an integer from 1 to 90"
        ) from exc
    if not 1 <= days <= 90:
        raise ValueError("INITIAL_LOOKBACK_DAYS must be an integer from 1 to 90")
    return days


def _parse_extra_uids(value: str | None) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    if not value:
        return result
    for line in value.replace("\r", "").split("\n"):
        line = line.strip()
        if not line:
            continue
        if ":" not in line:
            raise ValueError("EXTRA_WEIBO_UIDS entries must use 'IP:uid,uid' format")
        ip_name, raw_uids = line.split(":", 1)
        uids = [uid.strip() for uid in raw_uids.split(",") if uid.strip()]
        if not ip_name.strip() or not uids:
            raise ValueError("EXTRA_WEIBO_UIDS entries require an IP and at least one UID")
        if any(not uid.isdigit() for uid in uids):
            raise ValueError("EXTRA_WEIBO_UIDS values must be numeric Weibo UIDs")
        result.setdefault(ip_name.strip(), []).extend(uids)
    return result


class PublicSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    watched_ips: list[str]
    country: str = "CN"
    extra_weibo_uids: dict[str, list[str]] = Field(default_factory=dict)
    include_in_game: bool = False
    remind_day_before: bool = True
    publish_reachability: bool = False
    timezone: str = "Asia/Shanghai"
    initial_lookback_days: int = Field(default=21, ge=1, le=90)
    ai_provider: str = "openai_compatible"
    ai_base_url: str = "https://api.openai.com/v1"
    ai_model: str | None = None
    ai_vision: bool = False
    ai_json_mode: bool = True
    ai_thinking: Literal['enabled', 'disabled'] | None = None
    ai_max_tokens: int | None = Field(default=None, ge=1, le=65536)
    smtp_host: str | None = None
    smtp_port: int = Field(default=465, ge=1, le=65535)
    smtp_use_ssl: bool = True

    @field_validator("country")
    @classmethod
    def normalize_country(cls, value: str) -> str:
        normalized = value.strip().upper() or "CN"
        if normalized != "CN":
            raise ValueError("the first release only supports COUNTRY=CN")
        return normalized

    @field_validator("ai_provider")
    @classmethod
    def normalize_ai_provider(cls, value: str) -> str:
        normalized = value.strip().lower().replace("-", "_")
        if normalized not in AI_BASE_URLS:
            raise ValueError(
                "AI_PROVIDER must be 'openai_compatible' or 'zhipu_openai'"
            )
        return normalized


class PrivateSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin_city: SecretStr | None = None
    reachable_cities: SecretStr | None = None
    notify_email: SecretStr | None = None
    ai_api_key: SecretStr | None = None
    weibo_cookie: SecretStr | None = None
    smtp_username: SecretStr | None = None
    smtp_password: SecretStr | None = None


class RuntimeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    public: PublicSettings
    private: PrivateSettings

    @property
    def ai_enabled(self) -> bool:
        return self.public.ai_model is not None and self.private.ai_api_key is not None

    def profile_fingerprint(self) -> str:
        """Return a non-reversible hash without persisting private values."""

        private_presence = {
            name: value is not None
            for name, value in self.private.__dict__.items()
        }
        payload = {
            "public": self.public.model_dump(mode="json"),
            "private_presence": private_presence,
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


def load_settings(environ: Mapping[str, str] | None = None) -> RuntimeSettings:
    env = os.environ if environ is None else environ
    watched_ips = _split_list(env.get("WATCH_IPS"))
    if not watched_ips:
        raise ValueError("WATCH_IPS must contain at least one built-in IP name")

    ai_provider = env.get("AI_PROVIDER", "openai_compatible").strip().lower()
    ai_provider = ai_provider.replace("-", "_")
    ai_base_url = env.get("AI_BASE_URL") or AI_BASE_URLS.get(
        ai_provider, AI_BASE_URLS["openai_compatible"]
    )

    public = PublicSettings(
        watched_ips=watched_ips,
        country=env.get("COUNTRY", "CN"),
        extra_weibo_uids=_parse_extra_uids(env.get("EXTRA_WEIBO_UIDS")),
        include_in_game=_parse_bool(env.get("INCLUDE_IN_GAME"), False),
        remind_day_before=_parse_bool(env.get("REMIND_DAY_BEFORE"), True),
        publish_reachability=_parse_bool(
            env.get("PUBLISH_REACHABILITY"), False
        ),
        timezone=env.get("TIMEZONE", "Asia/Shanghai"),
        initial_lookback_days=_parse_initial_lookback_days(
            env.get("INITIAL_LOOKBACK_DAYS")
        ),
        ai_provider=ai_provider,
        ai_base_url=ai_base_url,
        ai_model=env.get("AI_MODEL") or None,
        ai_vision=_parse_bool(env.get("AI_VISION"), False),
        ai_json_mode=_parse_bool(env.get("AI_JSON_MODE"), True),
        ai_thinking=env.get('AI_THINKING') or None,
        ai_max_tokens=int(env['AI_MAX_TOKENS']) if env.get('AI_MAX_TOKENS') else None,
        smtp_host=env.get("SMTP_HOST") or None,
        smtp_port=int(env.get("SMTP_PORT", "465")),
        smtp_use_ssl=_parse_bool(env.get("SMTP_USE_SSL"), True),
    )
    private = PrivateSettings(
        origin_city=env.get("ORIGIN_CITY") or None,
        reachable_cities=env.get("REACHABLE_CITIES") or None,
        notify_email=env.get("NOTIFY_EMAIL") or None,
        ai_api_key=env.get("AI_API_KEY") or None,
        weibo_cookie=env.get("WEIBO_COOKIE") or None,
        smtp_username=env.get("SMTP_USERNAME") or None,
        smtp_password=env.get("SMTP_PASSWORD") or None,
    )
    return RuntimeSettings(public=public, private=private)
