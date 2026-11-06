import unittest
from datetime import datetime, timezone

from app.historical_backtest import net_event_return, signal_direction, _split_for_index, summarize_events


class HistoricalEventStudyTests(unittest.TestCase):
    def test_long_and_short_returns_include_round_trip_costs(self):
        long_return = net_event_return(100, 102, 1, 0.10, 0.05)
        short_return = net_event_return(100, 98, -1, 0.10, 0.05)
        self.assertAlmostEqual(long_return, 1.7)
        self.assertAlmostEqual(short_return, 1.7)

    def test_direction_is_derived_from_signal_semantics(self):
        self.assertEqual(signal_direction({"signal": "movimento de baixa"}), -1)
        self.assertEqual(signal_direction({"signal": "possível perda de força após sobrecompra"}), -1)
        self.assertEqual(signal_direction({"signal": "configuração potencial de alta"}), 1)

    def test_chronological_splits_are_ordered(self):
        self.assertEqual(_split_for_index(10, 100)[0], "train")
        self.assertEqual(_split_for_index(65, 100)[0], "validation")
        self.assertEqual(_split_for_index(85, 100)[0], "test")


    def test_event_summary_keeps_spot_long_and_short_research_separate(self):
        common = {
            "symbol": "BTCUSDT", "timeframe": "15m", "agent": "Bot A",
            "horizon_bars": 4, "split": "test",
        }
        rows = [
            {**common, "direction": "bullish", "net_return_pct": 1.0},
            {**common, "direction": "bearish", "net_return_pct": -1.0},
        ]
        summary = summarize_events(rows)
        self.assertEqual({row["market_mode"] for row in summary}, {"spot_long", "short_research"})
        self.assertEqual(len(summary), 2)

    def test_invalid_prices_are_rejected(self):
        with self.assertRaises(ValueError):
            net_event_return(0, 100, 1)


if __name__ == "__main__":
    unittest.main()
