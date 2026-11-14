import unittest
from datetime import datetime, timezone

from app.historical_backtest import (net_event_return, signal_direction, _split_for_index,
                                      summarize_events, download_historical_klines, BASE_INTERVAL_MS)


class HistoricalEventStudyTests(unittest.TestCase):
    def test_long_and_short_returns_include_round_trip_costs(self):
        long_return = net_event_return(100, 102, 1, 0.10, 0.05)
        short_return = net_event_return(100, 98, -1, 0.10, 0.05)
        self.assertAlmostEqual(long_return, 1.45)
        self.assertAlmostEqual(short_return, 1.45)

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

    def test_spread_and_slippage_assumptions_must_be_non_negative(self):
        with self.assertRaises(ValueError):
            net_event_return(100, 102, 1, spread_per_side_pct=-0.01)

    def test_short_pages_do_not_truncate_historical_pagination(self):
        interval = BASE_INTERVAL_MS["15m"]
        def candle(ts):
            return [ts, "100", "101", "99", "100", "10", ts + interval - 1,
                    "1000", 5, "5", "500", "0"]
        class Response:
            def __init__(self, rows): self.rows = rows
            def raise_for_status(self): pass
            def json(self): return self.rows
        class Session:
            def __init__(self): self.calls = []
            def get(self, url, params, timeout):
                self.calls.append(params["startTime"])
                if len(self.calls) == 1:
                    return Response([candle(0), candle(interval)])
                return Response([candle(2 * interval)])
        session = Session()
        frame = download_historical_klines(
            "https://example.invalid", "BTCUSDT", "15m", 0, 3 * interval, session
        )
        self.assertEqual(len(frame), 3)
        self.assertEqual(session.calls, [0, 2 * interval])

    def test_invalid_prices_are_rejected(self):
        with self.assertRaises(ValueError):
            net_event_return(0, 100, 1)


if __name__ == "__main__":
    unittest.main()
