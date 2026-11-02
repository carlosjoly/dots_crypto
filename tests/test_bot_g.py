import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.bot_g import run_bot_g_observer


class BotGObserverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cfg = {
            "_root": self.temp.name,
            "_env": {},
            "security": {"goplus_enabled": True},
        }
        self.now = datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc)
        self.snapshots = {
            symbol: {
                "symbol": symbol,
                "social_volume_24h": 100,
                "sentiment": 70,
                "collected_at": self.now.isoformat(),
                "source_updated_at": None,
                "source_freshness_verified": False,
                "social_contributors": None,
                "spam": None,
                "bot_ratio_pct": None,
            }
            for symbol in ("BTCUSDT", "ETHUSDT", "BNBUSDT", "LINKUSDT")
        }

    def tearDown(self):
        self.temp.cleanup()

    @patch.dict(os.environ, {"SOCIAL_API_KEY": "test-key"})
    @patch("app.bot_g.fetch_social_snapshots")
    @patch("app.bot_g.goplus_token_security")
    def test_collects_hourly_and_persists_all_social_assets(self, goplus, fetch):
        fetch.return_value = self.snapshots
        goplus.return_value = {
            "is_honeypot": "0", "cannot_buy": "0", "cannot_sell_all": "0",
            "is_blacklisted": "0", "is_open_source": "1", "is_in_dex": "1",
            "buy_tax": "0.01", "sell_tax": "0.01",
        }
        result = run_bot_g_observer(self.cfg, self.now)
        self.assertEqual(result, {"status": "observed", "collected": 4})
        fetch.assert_called_once()
        goplus.assert_called_once()
        state = json.loads((Path(self.temp.name) / "data" / "bot_g_social_history.json").read_text())
        self.assertEqual(set(state["assets"]), set(self.snapshots))
        self.assertFalse(state["assets"]["BTCUSDT"][0]["source_freshness_verified"])
        self.assertEqual(state["goplus"]["ethereum_link"]["status"], "eligible_for_watchlist")

    @patch.dict(os.environ, {"SOCIAL_API_KEY": "test-key"})
    @patch("app.bot_g.fetch_social_snapshots")
    def test_hourly_throttle_prevents_excess_api_calls(self, fetch):
        state_dir = Path(self.temp.name) / "data"
        state_dir.mkdir()
        (state_dir / "bot_g_social_history.json").write_text(json.dumps({
            "last_collected_at": (self.now - timedelta(minutes=15)).isoformat(),
            "assets": {}, "goplus": {},
        }))
        result = run_bot_g_observer(self.cfg, self.now)
        self.assertEqual(result["status"], "throttled")
        fetch.assert_not_called()

    @patch.dict(os.environ, {"SOCIAL_API_KEY": "test-key"})
    @patch("app.bot_g.fetch_social_snapshots")
    @patch("app.bot_g.goplus_token_security", side_effect=RuntimeError("provider failure"))
    def test_goplus_failure_is_fail_closed_without_blocking_observation(self, goplus, fetch):
        fetch.return_value = self.snapshots
        result = run_bot_g_observer(self.cfg, self.now)
        self.assertEqual(result["status"], "observed")
        state = json.loads((Path(self.temp.name) / "data" / "bot_g_social_history.json").read_text())
        self.assertEqual(state["goplus"]["ethereum_link"]["status"], "review_required")
        self.assertIsNone(state["goplus"]["ethereum_link"]["checked_at"])

    @patch.dict(os.environ, {}, clear=True)
    @patch("app.bot_g.fetch_social_snapshots")
    def test_missing_secret_disables_bot_without_network_call(self, fetch):
        result = run_bot_g_observer(self.cfg, self.now)
        self.assertEqual(result["status"], "disabled")
        fetch.assert_not_called()

    @patch.dict(os.environ, {"SOCIAL_API_KEY": "test-key"})
    @patch("app.bot_g.fetch_social_snapshots")
    def test_empty_provider_response_does_not_write_history(self, fetch):
        fetch.return_value = {}
        with self.assertRaises(RuntimeError):
            run_bot_g_observer(self.cfg, self.now)
        self.assertFalse((Path(self.temp.name) / "data" / "bot_g_social_history.json").exists())


if __name__ == "__main__":
    unittest.main()
