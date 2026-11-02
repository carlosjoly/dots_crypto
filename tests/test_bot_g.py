import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.bot_g import run_bot_g_observer
from app.alerts import format_message


class BotGObserverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cfg = {
            "_root": self.temp.name,
            "_env": {
                "telegram_bot_token": "telegram-test-token",
                "telegram_chat_id": "telegram-test-chat",
            },
            "security": {"goplus_enabled": True},
            "alerts": {"telegram_enabled": True, "log_file": "data/alerts.jsonl"},
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
    @patch("app.bot_g.emit_alert")
    @patch("app.bot_g.fetch_social_snapshots")
    @patch("app.bot_g.goplus_token_security")
    def test_collects_hourly_and_persists_all_social_assets(self, goplus, fetch, emit):
        fetch.return_value = self.snapshots
        goplus.return_value = {
            "is_honeypot": "0", "cannot_buy": "0", "cannot_sell_all": "0",
            "is_blacklisted": "0", "is_open_source": "1", "is_in_dex": "1",
            "buy_tax": "0.01", "sell_tax": "0.01",
        }
        result = run_bot_g_observer(self.cfg, self.now)
        self.assertEqual(result, {"status": "observed", "collected": 4, "alerts": 0, "pending_telegram": 0, "delivery_failed": 0})
        fetch.assert_called_once()
        goplus.assert_called_once()
        emit.assert_not_called()
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
            "assets": {}, "goplus": {}, "alerts": {},
        }))
        result = run_bot_g_observer(self.cfg, self.now)
        self.assertEqual(result["status"], "throttled")
        fetch.assert_not_called()

    @patch.dict(os.environ, {"SOCIAL_API_KEY": "test-key"})
    @patch("app.bot_g.emit_alert")
    @patch("app.bot_g.fetch_social_snapshots")
    @patch("app.bot_g.goplus_token_security", side_effect=RuntimeError("provider failure"))
    def test_first_extreme_sentiment_emits_preliminary_telegram_alert(self, goplus, fetch, emit):
        snapshots = {k: dict(v, sentiment=82) for k, v in self.snapshots.items()}
        fetch.return_value = snapshots
        result = run_bot_g_observer(self.cfg, self.now)
        self.assertEqual(result["alerts"], 4)
        self.assertEqual(emit.call_count, 4)
        alert = emit.call_args_list[0].args[1]
        self.assertEqual(alert["agent"], "BOT G — VALIDAÇÃO SOCIAL")
        self.assertIsNone(alert["social_volume_acceleration_pct"])
        message = format_message(alert)
        self.assertIn("BOT G — VALIDAÇÃO SOCIAL", message)
        self.assertIn("SENTIMENTO SOCIAL POSITIVO", message)
        self.assertIn("atualização temporal das métricas sociais", message)
        state = json.loads((Path(self.temp.name) / "data" / "bot_g_social_history.json").read_text())
        self.assertIn("last_sent_at", state["alerts"]["BTCUSDT"])

    @patch.dict(os.environ, {"SOCIAL_API_KEY": "test-key"})
    @patch("app.bot_g.emit_alert")
    @patch("app.bot_g.fetch_social_snapshots")
    @patch("app.bot_g.goplus_token_security", side_effect=RuntimeError("provider failure"))
    def test_next_signal_requires_extreme_sentiment_and_30pct_volume_acceleration(self, goplus, fetch, emit):
        state_dir = Path(self.temp.name) / "data"
        state_dir.mkdir()
        previous_time = self.now - timedelta(hours=1)
        prior = {
            "last_collected_at": previous_time.isoformat(),
            "assets": {
                symbol: [{
                    "symbol": symbol, "social_volume_24h": 100,
                    "sentiment": 70, "collected_at": previous_time.isoformat(),
                }]
                for symbol in self.snapshots
            },
            "goplus": {}, "alerts": {},
        }
        (state_dir / "bot_g_social_history.json").write_text(json.dumps(prior))
        current = {
            symbol: dict(snapshot, social_volume_24h=135, sentiment=80)
            for symbol, snapshot in self.snapshots.items()
        }
        fetch.return_value = current
        result = run_bot_g_observer(self.cfg, self.now)
        self.assertEqual(result["alerts"], 4)
        alert = emit.call_args_list[0].args[1]
        self.assertEqual(alert["social_volume_acceleration_pct"], 35.0)

    @patch.dict(os.environ, {"SOCIAL_API_KEY": "test-key"})
    @patch("app.bot_g.emit_alert")
    @patch("app.bot_g.fetch_social_snapshots")
    @patch("app.bot_g.goplus_token_security", side_effect=RuntimeError("provider failure"))
    def test_six_hour_cooldown_is_per_asset(self, goplus, fetch, emit):
        state_dir = Path(self.temp.name) / "data"
        state_dir.mkdir()
        prior_time = self.now - timedelta(hours=2)
        prior = {
            "last_collected_at": prior_time.isoformat(),
            "assets": {
                symbol: [{
                    "symbol": symbol, "social_volume_24h": 100,
                    "sentiment": 70, "collected_at": prior_time.isoformat(),
                }]
                for symbol in self.snapshots
            },
            "goplus": {},
            "alerts": {"BTCUSDT": {"last_sent_at": prior_time.isoformat()}},
        }
        (state_dir / "bot_g_social_history.json").write_text(json.dumps(prior))
        current = {
            symbol: dict(snapshot, social_volume_24h=140, sentiment=80)
            for symbol, snapshot in self.snapshots.items()
        }
        fetch.return_value = current
        result = run_bot_g_observer(self.cfg, self.now)
        self.assertEqual(result["alerts"], 3)
        self.assertEqual(emit.call_count, 3)
        self.assertNotIn("BTCUSDT", [call.args[1]["symbol"] for call in emit.call_args_list])

    @patch.dict(os.environ, {"SOCIAL_API_KEY": "test-key"})
    @patch("app.bot_g.emit_alert")
    @patch("app.bot_g.fetch_social_snapshots")
    def test_missing_telegram_configuration_keeps_signal_pending_and_does_not_consume_cooldown(self, fetch, emit):
        self.cfg["_env"] = {}
        fetch.return_value = {
            symbol: dict(snapshot, sentiment=80)
            for symbol, snapshot in self.snapshots.items()
        }
        with patch("app.bot_g.goplus_token_security", side_effect=RuntimeError("provider failure")):
            result = run_bot_g_observer(self.cfg, self.now)
        self.assertEqual(result["alerts"], 0)
        self.assertEqual(result["pending_telegram"], 4)
        emit.assert_not_called()

    @patch.dict(os.environ, {"SOCIAL_API_KEY": "test-key"})
    @patch("app.bot_g.fetch_social_snapshots")
    @patch("app.bot_g.goplus_token_security", side_effect=RuntimeError("provider failure"))
    def test_empty_provider_response_does_not_write_history(self, goplus, fetch):
        fetch.return_value = {}
        with self.assertRaises(RuntimeError):
            run_bot_g_observer(self.cfg, self.now)
        self.assertFalse((Path(self.temp.name) / "data" / "bot_g_social_history.json").exists())

    @patch.dict(os.environ, {"LUNARCRUSH_API_KEY": "canonical-test-key"}, clear=True)
    @patch("app.bot_g.fetch_social_snapshots")
    @patch("app.bot_g.goplus_token_security", side_effect=RuntimeError("provider failure"))
    def test_canonical_lunarcrush_secret_name_is_supported(self, goplus, fetch):
        fetch.return_value = self.snapshots
        result = run_bot_g_observer(self.cfg, self.now)
        self.assertEqual(result["status"], "observed")
        self.assertEqual(fetch.call_args.args[1], "canonical-test-key")

    @patch.dict(os.environ, {"SOCIAL_API_KEY": "test-key"})
    @patch("app.bot_g.emit_alert", side_effect=RuntimeError("telegram transport failure"))
    @patch("app.bot_g.fetch_social_snapshots")
    @patch("app.bot_g.goplus_token_security", side_effect=RuntimeError("provider failure"))
    def test_failed_telegram_delivery_does_not_consume_cooldown(self, goplus, fetch, emit):
        fetch.return_value = {
            symbol: dict(snapshot, sentiment=82)
            for symbol, snapshot in self.snapshots.items()
        }
        result = run_bot_g_observer(self.cfg, self.now)
        self.assertEqual(result["alerts"], 0)
        self.assertEqual(result["delivery_failed"], 4)
        state = json.loads((Path(self.temp.name) / "data" / "bot_g_social_history.json").read_text())
        self.assertNotIn("BTCUSDT", state["alerts"])

    @patch.dict(os.environ, {}, clear=True)
    @patch("app.bot_g.fetch_social_snapshots")
    def test_missing_social_secret_disables_bot_without_network_call(self, fetch):
        result = run_bot_g_observer(self.cfg, self.now)
        self.assertEqual(result["status"], "disabled")
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
