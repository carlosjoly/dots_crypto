import unittest
from unittest.mock import patch

from app.main import process_active_market_agents


class ActiveCycleHealthTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "exchange": {"timeframes": ["15m", "1h"], "candle_limit": 120,
                         "max_spread_pct": 0.25}
        }
        self.snapshot = {
            "spread_pct": 0.01, "bid": 99.9, "ask": 100.1,
            "collected_at_utc": "2026-11-05T20:00:00+00:00",
        }

    @patch("app.main.emit_alert")
    @patch("app.main.run_market_agents", return_value=[])
    @patch("app.main.get_klines")
    def test_cycle_continues_but_reports_partial_data_failure(self, get_klines, agents, emit):
        get_klines.side_effect = [ValueError("missing candle interval"), object()]
        sent, failures = process_active_market_agents(
            self.cfg, "https://example.test", {"BTCUSDT": self.snapshot}
        )
        self.assertEqual(sent, 0)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["symbol"], "BTCUSDT")
        self.assertEqual(failures[0]["timeframe"], "15m")
        self.assertEqual(get_klines.call_count, 2)
        agents.assert_called_once()

    @patch("app.main.emit_alert")
    @patch("app.main.run_market_agents", return_value=[])
    @patch("app.main.get_klines", return_value=object())
    def test_complete_cycle_has_no_failures(self, get_klines, agents, emit):
        sent, failures = process_active_market_agents(
            self.cfg, "https://example.test", {"BTCUSDT": self.snapshot}
        )
        self.assertEqual(sent, 0)
        self.assertEqual(failures, [])
        self.assertEqual(get_klines.call_count, 2)
        self.assertEqual(agents.call_count, 2)


if __name__ == "__main__":
    unittest.main()
