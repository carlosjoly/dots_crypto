import tempfile
import unittest
from pathlib import Path

import pandas as pd

from app.trend_monitor import TrendStateStore, classify_trend


def candles(values, start=0):
    return pd.DataFrame({
        "open": [v - 0.1 for v in values],
        "high": [v + 0.5 for v in values],
        "low": [v - 0.5 for v in values],
        "close": values,
        "volume": [100.0] * len(values),
        "close_time": [f"2026-11-15T{(start + i) // 60:02d}:{(start + i) % 60:02d}:00Z" for i in range(len(values))],
    })


class TrendClassificationTests(unittest.TestCase):
    def test_classifies_confirmed_rising_structure(self):
        values = [100 + i * 0.5 for i in range(80)]
        self.assertEqual(classify_trend(candles(values))["regime"], "bullish")

    def test_classifies_confirmed_falling_structure(self):
        values = [140 - i * 0.5 for i in range(80)]
        self.assertEqual(classify_trend(candles(values))["regime"], "bearish")

    def test_classifies_flat_market_as_sideways(self):
        self.assertEqual(classify_trend(candles([100.0] * 80))["regime"], "sideways")

    def test_rejects_insufficient_history(self):
        result = classify_trend(candles([100.0] * 40))
        self.assertIsNone(result["regime"])
        self.assertEqual(result["reason"], "historico_insuficiente")


class TrendStateTests(unittest.TestCase):
    def test_requires_two_distinct_closed_candles_for_transition(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TrendStateStore(Path(tmp) / "state.json", confirmations=2)
            base = {"regime": "bullish", "candle_time_utc": "2026-11-15T10:00:00Z",
                    "price": 100, "sma20": 99, "sma50": 98,
                    "sma20_slope_pct": 0.1, "sma50_slope_pct": 0.1, "rsi": 60}
            self.assertIsNone(store.observe("BTCUSDT", "15m", base))
            self.assertIsNone(store.observe("BTCUSDT", "15m", base))
            second = dict(base, candle_time_utc="2026-11-15T10:15:00Z")
            alert = store.observe("BTCUSDT", "15m", second)
            self.assertIn("estado inicial", alert["signal"])
            self.assertFalse(alert["execution_eligible"])

    def test_detects_up_down_sideways_up_sequence_without_duplicate_alerts(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TrendStateStore(Path(tmp) / "state.json", confirmations=1)
            template = {"price": 100, "sma20": 99, "sma50": 98,
                        "sma20_slope_pct": 0.1, "sma50_slope_pct": 0.1, "rsi": 60}
            regimes = ["bullish", "bearish", "sideways", "bullish"]
            alerts = []
            for index, regime in enumerate(regimes):
                snapshot = dict(template, regime=regime,
                                candle_time_utc=f"2026-11-15T{10 + index:02d}:00:00Z")
                alert = store.observe("BTCUSDT", "15m", snapshot)
                if alert:
                    alerts.append(alert)
                self.assertIsNone(store.observe("BTCUSDT", "15m", snapshot))
            self.assertEqual(len(alerts), 4)
            self.assertIn("tendência de alta", alerts[0]["signal"])
            self.assertIn("tendência de baixa", alerts[1]["signal"])
            self.assertIn("lateral/estável", alerts[2]["signal"])
            self.assertIn("tendência de alta", alerts[3]["signal"])

    def test_persists_state_across_process_restarts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            first = TrendStateStore(path, confirmations=1)
            snapshot = {"regime": "bullish", "candle_time_utc": "2026-11-15T10:00:00Z",
                        "price": 100, "sma20": 99, "sma50": 98,
                        "sma20_slope_pct": 0.1, "sma50_slope_pct": 0.1, "rsi": 60}
            self.assertIsNotNone(first.observe("BTCUSDT", "15m", snapshot))
            first.save()
            restarted = TrendStateStore(path, confirmations=1)
            self.assertIsNone(restarted.observe("BTCUSDT", "15m", snapshot))
            self.assertEqual(restarted.state["markets"]["BTCUSDT|15m"]["regime"], "bullish")

    def test_state_is_independent_per_symbol_and_timeframe(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TrendStateStore(Path(tmp) / "state.json", confirmations=1)
            snapshot = {"regime": "bullish", "candle_time_utc": "2026-11-15T10:00:00Z",
                        "price": 100, "sma20": 99, "sma50": 98,
                        "sma20_slope_pct": 0.1, "sma50_slope_pct": 0.1, "rsi": 60}
            store.observe("BTCUSDT", "15m", snapshot)
            self.assertIsNone(store.state["markets"].get("ETHUSDT|15m"))
            self.assertIsNone(store.state["markets"].get("BTCUSDT|1h"))


if __name__ == "__main__":
    unittest.main()
