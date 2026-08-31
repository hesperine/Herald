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


if __name__ == "__main__":
    unittest.main()
