from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_PATH = ROOT / "scripts/run-local.py"


def _load_launcher():
    spec = importlib.util.spec_from_file_location("herald_local_launcher", LAUNCHER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("local launcher could not be imported")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LocalLauncherTests(unittest.TestCase):
    def test_parses_simple_utf8_environment_without_execution(self) -> None:
        launcher = _load_launcher()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "local.env"
            path.write_text(
                "# local debug values\n"
                "WATCH_IPS=原神,明日方舟\n"
                'AI_MODEL="free model"\n'
                "EMPTY=\n",
                encoding="utf-8",
            )

            values = launcher.load_local_env(path)

        self.assertEqual(values["WATCH_IPS"], "原神,明日方舟")
        self.assertEqual(values["AI_MODEL"], "free model")
        self.assertEqual(values["EMPTY"], "")

    def test_invalid_line_does_not_echo_its_contents(self) -> None:
        launcher = _load_launcher()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "local.env"
            path.write_text("WATCH_IPS=原神\ninvalid-local-value\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, r"local\.env line 2") as raised:
                launcher.load_local_env(path)

        self.assertNotIn("invalid-local-value", str(raised.exception))

    def test_loads_selected_private_ai_profile(self) -> None:
        launcher = _load_launcher()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "local-ai-providers.toml"
            path.write_text(
                "[openrouter-minimax-minimax-m3]\n"
                'AI_PROVIDER = "openai_compatible"\n'
                'AI_BASE_URL = "https://openrouter.ai/api/v1"\n'
                'AI_MODEL = "minimax/minimax-m3:free"\n'
                "AI_VISION = true\n"
                "AI_JSON_MODE = true\n"
                'AI_API_KEY = "private-test-key"\n',
                encoding="utf-8",
            )

            values = launcher.load_ai_profile(
                path, "openrouter-minimax-minimax-m3"
            )

        self.assertEqual(values["AI_PROVIDER"], "openai_compatible")
        self.assertEqual(values["AI_MODEL"], "minimax/minimax-m3:free")
        self.assertEqual(values["AI_VISION"], "true")
        self.assertEqual(values["AI_JSON_MODE"], "true")
        self.assertEqual(values["AI_API_KEY"], "private-test-key")

    def test_profile_errors_do_not_echo_private_values(self) -> None:
        launcher = _load_launcher()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "local-ai-providers.toml"
            path.write_text(
                "[broken-profile]\n"
                'AI_PROVIDER = "openai_compatible"\n'
                'AI_BASE_URL = "https://example.invalid/v1"\n'
                'AI_MODEL = "example/model"\n'
                'AI_VISION = "not-a-boolean"\n'
                "AI_JSON_MODE = true\n"
                'AI_API_KEY = "must-not-appear-in-errors"\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "AI_VISION") as raised:
                launcher.load_ai_profile(path, "broken-profile")

        self.assertNotIn("must-not-appear-in-errors", str(raised.exception))

    def test_launches_same_module_with_a_child_only_environment(self) -> None:
        launcher = _load_launcher()
        with tempfile.TemporaryDirectory() as temporary:
            env_file = Path(temporary) / "local.env"
            state_dir = Path(temporary) / "state"
            page_dir = Path(temporary) / "page"
            env_file.write_text("WATCH_IPS=原神\n", encoding="utf-8")
            completed = SimpleNamespace(returncode=0)

            with patch.dict(os.environ, {"EXISTING_VALUE": "kept"}, clear=True):
                with patch.object(launcher.subprocess, "run", return_value=completed) as run:
                    exit_code = launcher.main(
                        [
                            "--env-file",
                            str(env_file),
                            "--state-dir",
                            str(state_dir),
                            "--page-dir",
                            str(page_dir),
                        ]
                    )
                self.assertNotIn("WATCH_IPS", os.environ)

        self.assertEqual(exit_code, 0)
        command = run.call_args.args[0]
        child_environment = run.call_args.kwargs["env"]
        self.assertEqual(command[1:3], ["-m", "herald"])
        self.assertEqual(child_environment["WATCH_IPS"], "原神")
        self.assertEqual(child_environment["EXISTING_VALUE"], "kept")

    def test_selected_profile_overrides_local_ai_values_for_child(self) -> None:
        launcher = _load_launcher()
        with tempfile.TemporaryDirectory() as temporary:
            env_file = Path(temporary) / "local.env"
            provider_file = Path(temporary) / "local-ai-providers.toml"
            state_dir = Path(temporary) / "state"
            page_dir = Path(temporary) / "page"
            env_file.write_text(
                "WATCH_IPS=原神\n"
                "AI_PROFILE=siliconflow-qwen3.5-4b\n"
                "AI_MODEL=old-direct-model\n",
                encoding="utf-8",
            )
            provider_file.write_text(
                '["siliconflow-qwen3.5-4b"]\n'
                'AI_PROVIDER = "openai_compatible"\n'
                'AI_BASE_URL = "https://api.siliconflow.cn/v1"\n'
                'AI_MODEL = "Qwen/Qwen3.5-4B"\n'
                "AI_VISION = true\n"
                "AI_JSON_MODE = true\n"
                'AI_API_KEY = "child-only-key"\n',
                encoding="utf-8",
            )
            completed = SimpleNamespace(returncode=0)

            with patch.dict(os.environ, {}, clear=True):
                with patch.object(launcher.subprocess, "run", return_value=completed) as run:
                    exit_code = launcher.main(
                        [
                            "--env-file",
                            str(env_file),
                            "--provider-file",
                            str(provider_file),
                            "--state-dir",
                            str(state_dir),
                            "--page-dir",
                            str(page_dir),
                        ]
                    )
                self.assertNotIn("AI_API_KEY", os.environ)

        self.assertEqual(exit_code, 0)
        child_environment = run.call_args.kwargs["env"]
        self.assertNotIn("AI_PROFILE", child_environment)
        self.assertEqual(child_environment["AI_MODEL"], "Qwen/Qwen3.5-4B")
        self.assertEqual(child_environment["AI_API_KEY"], "child-only-key")

    def test_ai_smoke_flag_launches_only_extraction_module(self) -> None:
        launcher = _load_launcher()
        with tempfile.TemporaryDirectory() as temporary:
            env_file = Path(temporary) / "local.env"
            env_file.write_text("WATCH_IPS=原神\n", encoding="utf-8")
            completed = SimpleNamespace(returncode=0)

            with patch.object(
                launcher.subprocess, "run", return_value=completed
            ) as run:
                exit_code = launcher.main(
                    ["--env-file", str(env_file), "--ai-smoke-test"]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            run.call_args.args[0],
            [launcher.sys.executable, "-m", "herald.ai_smoke"],
        )

    def test_history_collection_flag_launches_only_public_collector(self) -> None:
        launcher = _load_launcher()
        with tempfile.TemporaryDirectory() as temporary:
            env_file = Path(temporary) / "local.env"
            output = Path(temporary) / "samples.json"
            env_file.write_text("WATCH_IPS=原神\n", encoding="utf-8")
            completed = SimpleNamespace(returncode=0)

            with patch.object(
                launcher.subprocess, "run", return_value=completed
            ) as run:
                exit_code = launcher.main(
                    [
                        "--env-file",
                        str(env_file),
                        "--collect-weibo-samples",
                        "--sample-start-date",
                        "2026-08-01",
                        "--sample-end-date",
                        "2026-08-31",
                        "--sample-max-pages",
                        "12",
                        "--sample-output",
                        str(output),
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            run.call_args.args[0],
            [
                launcher.sys.executable,
                "-m",
                "herald.weibo_sample_collector",
                "--start-date",
                "2026-08-01",
                "--end-date",
                "2026-08-31",
                "--max-pages",
                "12",
                "--output",
                str(output),
            ],
        )

    def test_history_collection_requires_both_date_bounds(self) -> None:
        launcher = _load_launcher()
        with tempfile.TemporaryDirectory() as temporary:
            env_file = Path(temporary) / "local.env"
            env_file.write_text("WATCH_IPS=原神\n", encoding="utf-8")

            with patch.object(launcher.subprocess, "run") as run:
                exit_code = launcher.main(
                    [
                        "--env-file",
                        str(env_file),
                        "--collect-weibo-samples",
                        "--sample-start-date",
                        "2026-08-01",
                    ]
                )

        self.assertEqual(exit_code, 2)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
