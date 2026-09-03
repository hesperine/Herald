from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import httpx

from herald.cli import _make_email_sender, _make_provider, _parse_now, _parser, main
from herald.runner import RunPhase
from herald.config import load_settings


class CliTests(unittest.TestCase):
    def test_provider_is_optional_and_uses_openai_compatible_configuration(self) -> None:
        without_key = load_settings({"WATCH_IPS": "原神"})
        client = object()
        self.assertIsNone(_make_provider(without_key, client))

        configured = load_settings(
            {
                "WATCH_IPS": "原神",
                "AI_MODEL": "free-model",
                "AI_BASE_URL": "https://free.example/v1",
                "AI_API_KEY": "user-owned-key",
                "AI_VISION": "true",
            }
        )
        provider = _make_provider(configured, client)
        self.assertEqual(provider.model_name, "free-model")
        self.assertEqual(provider.base_url, "https://free.example/v1")
        self.assertFalse(hasattr(provider, "supports_vision"))

    def test_zhipu_openai_alias_selects_the_zhipu_dialect_and_default_url(self) -> None:
        configured = load_settings(
            {
                "WATCH_IPS": "原神",
                "AI_PROVIDER": "zhipu-openai",
                "AI_MODEL": "glm-4.6v-flash",
                "AI_API_KEY": "user-owned-key",
            }
        )

        provider = _make_provider(configured, object())

        self.assertEqual(configured.public.ai_provider, "zhipu_openai")
        self.assertEqual(provider.provider_name, "zhipu_openai")
        self.assertEqual(
            provider.base_url, "https://open.bigmodel.cn/api/paas/v4"
        )

    def test_email_sender_requires_a_complete_user_configuration(self) -> None:
        incomplete = load_settings(
            {"WATCH_IPS": "原神", "NOTIFY_EMAIL": "player@example.com"}
        )
        self.assertIsNone(_make_email_sender(incomplete))

        complete = load_settings(
            {
                "WATCH_IPS": "原神",
                "SMTP_HOST": "smtp.example.com",
                "SMTP_USERNAME": "player@example.com",
                "SMTP_PASSWORD": "password-value",
            }
        )
        sender = _make_email_sender(complete)
        self.assertEqual(sender.host, "smtp.example.com")

    def test_parse_now_requires_an_explicit_timezone(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone"):
            _parse_now("2026-08-30T20:00:00")
        self.assertEqual(_parse_now("2026-08-30T20:00:00+08:00").utcoffset().seconds, 28800)

    def test_phase_argument_defaults_to_full_and_accepts_individual_stages(self) -> None:
        self.assertEqual(_parser().parse_args([]).phase, RunPhase.FULL.value)
        self.assertEqual(
            _parser().parse_args(["--phase", "fetch"]).phase,
            RunPhase.FETCH.value,
        )
        self.assertEqual(
            _parser().parse_args(["--phase", "extract"]).phase,
            RunPhase.EXTRACT.value,
        )

    def test_main_builds_a_minimal_page_when_official_source_is_offline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            page = root / "page"
            with patch.dict(os.environ, {"WATCH_IPS": "原神"}, clear=True):
                class NoopAsyncClient:
                    def __init__(self, **kwargs):
                        pass

                    async def __aenter__(self):
                        return self

                    async def __aexit__(self, *args):
                        return None

                    async def get(self, *args, **kwargs):
                        raise httpx.ConnectError("offline fixture")

                with patch("herald.cli.httpx.AsyncClient", NoopAsyncClient):
                    with redirect_stdout(StringIO()):
                        exit_code = main(
                            [
                                "--state-dir",
                                str(state),
                                "--page-dir",
                                str(page),
                                "--now",
                                "2026-08-30T20:00:00+08:00",
                            ]
                        )

            self.assertEqual(exit_code, 0)
            payload = json.loads((page / "data/active.json").read_text("utf-8"))
            self.assertEqual(payload["campaigns"], [])
            reports = list((state / "runs").rglob("*.json"))
            self.assertEqual(len(reports), 1)


if __name__ == "__main__":
    unittest.main()
