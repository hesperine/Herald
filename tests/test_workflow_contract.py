from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkflowContractTests(unittest.TestCase):
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
