from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkflowContractTests(unittest.TestCase):
    def test_local_development_uses_a_rebuildable_virtual_environment(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn(".venv/", gitignore.splitlines())
        self.assertIn("python -m venv .venv", readme)
        self.assertIn(r".\.venv\Scripts\Activate.ps1", readme)
        self.assertIn('python -m pip install -e ".[dev]"', readme)
        self.assertIn("[project.optional-dependencies]", pyproject)
        self.assertIn("dev = [", pyproject)

    def test_local_debugging_instructions_cover_outputs_and_reproduction(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn(".herald-work/local-state", readme)
        self.assertIn(".herald-work/local-page", readme)
        self.assertIn("python -m http.server 8000", readme)
        self.assertIn("--now", readme)
        self.assertIn("--state-dir", readme)
        self.assertIn("--page-dir", readme)
        self.assertIn("Ctrl+C", readme)

    def test_local_launcher_injects_env_without_changing_runtime_contract(self) -> None:
        launcher = (ROOT / "scripts/run-local.py").read_text(encoding="utf-8")
        example = (ROOT / "local.env.example").read_text(encoding="utf-8")
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/daily.yml").read_text(encoding="utf-8")
        cli = (ROOT / "src/herald/cli.py").read_text(encoding="utf-8")

        self.assertFalse((ROOT / ".env.example").exists())
        self.assertFalse((ROOT / "scripts/run-local.ps1").exists())
        self.assertIn("local.env", gitignore.splitlines())
        self.assertIn("WATCH_IPS=原神,明日方舟", example)
        self.assertIn('"-m", "herald"', launcher)
        self.assertIn("env=child_environment", launcher)
        self.assertNotIn("local.env", workflow)
        self.assertNotIn("local.env", cli)

    def test_daily_workflow_keeps_configuration_out_of_main(self) -> None:
        workflow = (ROOT / ".github/workflows/daily.yml").read_text(encoding="utf-8")

        self.assertIn('cron: "15 0 * * *"', workflow)
        self.assertIn("prepare_branch state _state", workflow)
        self.assertIn("prepare_branch page _page", workflow)
        self.assertIn("group: herald-daily-", workflow)
        self.assertIn(
            "python -m herald --state-dir _state --page-dir _page", workflow
        )
        self.assertIn("--state-dir _state --page-dir _page", workflow)
        self.assertIn("commit_branch state _state", workflow)
        self.assertIn("commit_branch page _page", workflow)
        self.assertNotIn("commit_branch main", workflow)

    def test_private_configuration_uses_secrets(self) -> None:
        workflow = (ROOT / ".github/workflows/daily.yml").read_text(encoding="utf-8")

        for name in (
            "ORIGIN_CITY",
            "REACHABLE_CITIES",
            "NOTIFY_EMAIL",
            "AI_API_KEY",
            "WEIBO_COOKIE",
            "SMTP_USERNAME",
            "SMTP_PASSWORD",
        ):
            self.assertIn(f"{name}: ${{{{ secrets.{name} }}}}", workflow)


if __name__ == "__main__":
    unittest.main()
