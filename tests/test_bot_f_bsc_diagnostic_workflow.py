import unittest
from pathlib import Path


class BotFBscDiagnosticWorkflowTests(unittest.TestCase):
    def test_diagnostic_failure_is_not_marked_success(self):
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "bot-f-bsc-diagnostic.yml"
        ).read_text(encoding="utf-8")
        self.assertNotIn("continue-on-error: true", workflow)
        self.assertIn("if: always()", workflow)
        self.assertIn("BOT_F_RPC_URL_BSC: ${{ secrets.BOT_F_RPC_URL_BSC }}", workflow)


if __name__ == "__main__":
    unittest.main()
