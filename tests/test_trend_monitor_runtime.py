import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from app.main import process_continuous_trend_monitor
from app.trend_monitor import TrendStateStore


def rising_candles():
    values = [100 + i * 0.5 for i in range(80)]
    return pd.DataFrame({
        "open": [v - 0.1 for v in values],
        "high": [v + 0.5 for v in values],
        "low": [v - 0.5 for v in values],
        "close": values,
        "volume": [100.0] * len(values),
        "close_time": [f"2026-11-15T{i // 4:02d}:{(i % 4) * 15:02d}:00Z" for i in range(len(values))],
    })


class ContinuousTrendRuntimeTests(unittest.TestCase):
    def test_monitors_all_symbols_and_timeframes_without_candidate_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {
                "_root": tmp,
                "exchange": {
                    "symbols": ["BTCUSDT", "ETHUSDT"],
                    "timeframes": ["15m", "1h", "4h"],
                    "candle_limit": 120,
                },
                "trend_monitor": {"slope_floor_pct": 0.02},
            }
            store = TrendStateStore(Path(tmp) / "trend.json", confirmations=1)
            delivered = []
            with patch("app.main.get_klines", return_value=rising_candles()) as fetch:
                with patch("app.main.emit_alert", side_effect=delivered.append):
                    sent, failures = process_continuous_trend_monitor(cfg, "https://example.invalid", store)
            self.assertEqual(fetch.call_count, 6)
            self.assertEqual(sent, 6)
            self.assertEqual(failures, [])
            self.assertEqual(len(delivered), 6)
            self.assertTrue(all(item["execution_eligible"] is False for item in delivered))
            self.assertTrue(Path(tmp, "trend.json").exists())

    def test_one_collection_failure_does_not_suppress_other_markets(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {
                "_root": tmp,
                "exchange": {
                    "symbols": ["BTCUSDT", "ETHUSDT"],
                    "timeframes": ["15m"],
                    "candle_limit": 120,
                },
                "trend_monitor": {"slope_floor_pct": 0.02},
            }
            store = TrendStateStore(Path(tmp) / "trend.json", confirmations=1)
            def fetch(_base, symbol, _timeframe, _limit):
                if symbol == "BTCUSDT":
                    raise TimeoutError("simulated timeout")
                return rising_candles()
            delivered = []
            with patch("app.main.get_klines", side_effect=fetch):
                with patch("app.main.emit_alert", side_effect=delivered.append):
                    sent, failures = process_continuous_trend_monitor(cfg, "unused", store)
            self.assertEqual(sent, 1)
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0]["symbol"], "BTCUSDT")
            self.assertEqual(len(delivered), 1)


if __name__ == "__main__":
    unittest.main()
