import unittest
from unittest.mock import patch

from app.run_bot_f import main


class BotFStandaloneRunnerTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "_root": "/tmp/dot-cripto-test",
            "_env": {"telegram_bot_token": "test-token", "telegram_chat_id": "123"},
            "launches": {"enabled": True},
        }

    @patch("app.run_bot_f.emit_alert")
    @patch("app.run_bot_f.scan_new_launches", return_value=[{"symbol": "TEST", "network": "base"}])
    @patch("app.run_bot_f.setup_logging")
    @patch("app.run_bot_f.load_config")
    def test_processes_alerts_without_trading(self, load_config, setup_logging, scan, emit):
        load_config.return_value = self.cfg
        self.assertEqual(main(), 0)
        setup_logging.assert_called_once_with(self.cfg["_root"])
        scan.assert_called_once_with(self.cfg)
        emit.assert_called_once()

    @patch("app.run_bot_f.release_seen_token")
    @patch("app.run_bot_f.emit_alert", side_effect=RuntimeError("Telegram unavailable"))
    @patch("app.run_bot_f.scan_new_launches", return_value=[{"symbol": "TEST", "token_address": "0x1"}])
    @patch("app.run_bot_f.setup_logging")
    @patch("app.run_bot_f.load_config")
    def test_failed_delivery_releases_deduplication_for_retry(
        self, load_config, setup_logging, scan, emit, release
    ):
        load_config.return_value = self.cfg
        self.assertEqual(main(), 1)
        release.assert_called_once_with(self.cfg, {"symbol": "TEST", "token_address": "0x1"})

    @patch("app.run_bot_f.scan_new_launches", side_effect=RuntimeError("provider unavailable"))
    @patch("app.run_bot_f.setup_logging")
    @patch("app.run_bot_f.load_config")
    def test_critical_scan_error_returns_failure(self, load_config, setup_logging, scan):
        load_config.return_value = self.cfg
        self.assertEqual(main(), 1)

    @patch("app.run_bot_f.scan_new_launches")
    @patch("app.run_bot_f.setup_logging")
    @patch("app.run_bot_f.load_config")
    def test_disabled_module_does_not_start_scan(self, load_config, setup_logging, scan):
        cfg = dict(self.cfg, launches={"enabled": False})
        load_config.return_value = cfg
        self.assertEqual(main(), 2)
        scan.assert_not_called()


if __name__ == "__main__":
    unittest.main()
