import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
from unittest.mock import patch

from app.cooldown import CooldownStore, cooldown_key, is_within_cooldown
from app.portfolio_backtest import _make_events, BARS_PER_24H


class CooldownStoreTests(unittest.TestCase):
    def setUp(self):
        self.alert = {
            "agent": "Bot A", "symbol": "BTCUSDT",
            "signal": "movimento de alta",
        }
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def test_blocks_until_exactly_sixty_minutes(self):
        previous = self.now.isoformat()
        self.assertTrue(is_within_cooldown(previous, self.now + timedelta(minutes=59), 60))
        self.assertFalse(is_within_cooldown(previous, self.now + timedelta(minutes=60), 60))

    def test_state_persists_between_store_instances(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data" / "cooldown.json"
            first = CooldownStore(path, 60)
            self.assertTrue(first.allows(self.alert, "15m", self.now))
            first.record(self.alert, "15m", self.now)
            second = CooldownStore(path, 60)
            self.assertFalse(second.allows(self.alert, "15m", self.now + timedelta(minutes=30)))
            self.assertTrue(second.allows(self.alert, "15m", self.now + timedelta(minutes=60)))
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], 1)

    def test_keys_are_isolated_by_agent_symbol_timeframe_and_signal(self):
        base = cooldown_key(self.alert, "15m")
        self.assertNotEqual(base, cooldown_key({**self.alert, "symbol": "ETHUSDT"}, "15m"))
        self.assertNotEqual(base, cooldown_key({**self.alert, "agent": "Bot B"}, "15m"))
        self.assertNotEqual(base, cooldown_key({**self.alert, "signal": "movimento de baixa"}, "15m"))
        store = CooldownStore(Path("unused.json"), 60)
        store.record(self.alert, "15m", self.now)
        self.assertTrue(store.allows(self.alert, "1h", self.now))

    def test_zero_minutes_disables_cooldown(self):
        store = CooldownStore(Path("unused.json"), 0)
        store.record(self.alert, "15m", self.now)
        self.assertTrue(store.allows(self.alert, "15m", self.now))

    def test_negative_minutes_are_rejected(self):
        with self.assertRaises(ValueError):
            CooldownStore(Path("unused.json"), -1)


class BacktestCooldownTests(unittest.TestCase):
    def test_backtest_suppresses_repeated_signal_inside_cooldown(self):
        timestamps = pd.date_range("2026-01-01", periods=57, freq="15min", tz="UTC")
        frame = pd.DataFrame({
            "open_time": timestamps,
            "quote_volume": [10.0] * 57,
            "open": [100.0] * 57, "high": [101.0] * 57,
            "low": [99.0] * 57, "close": [100.0] * 57,
            "volume": [1.0] * 57, "atr14": [1.0] * 57,
        })
        alert = {"agent": "Bot A", "signal": "movimento de alta"}
        cfg = {"exchange": {"min_quote_volume_24h_usdt": 0},
               "alerts": {"cooldown_minutes": 60}}
        with patch("app.portfolio_backtest.enrich", side_effect=lambda data: data.copy()), \
             patch("app.portfolio_backtest.run_market_agents_enriched", return_value=[alert]), \
             patch.dict(BARS_PER_24H, {"15m": 1}):
            _, events = _make_events("BTCUSDT", "15m", frame, cfg)
        self.assertEqual(len(events["Bot A"]), 1)


if __name__ == "__main__":
    unittest.main()
