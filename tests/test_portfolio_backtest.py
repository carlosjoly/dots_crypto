import unittest

import pandas as pd

from app.historical_backtest import signal_direction
from app.indicators import rsi
from app.portfolio_backtest import (chronological_bounds, simulate_portfolio,
                                    filter_events_by_market_mode, failed_spot_long_test_rows, benchmark_for_market_mode)


class SafeSignalDirectionTests(unittest.TestCase):
    def test_known_signals_are_mapped_explicitly(self):
        expected = {
            "movimento de alta": 1,
            "movimento de baixa": -1,
            "possível estabilização após sobrevenda": 1,
            "possível perda de força após sobrecompra": -1,
            "possível rompimento de resistência": 1,
            "possível rompimento de suporte": -1,
            "configuração potencial de alta": 1,
            "configuração potencial de baixa": -1,
        }
        for signal, direction in expected.items():
            with self.subTest(signal=signal):
                self.assertEqual(signal_direction({"signal": signal}), direction)

    def test_unknown_or_missing_signal_fails_closed(self):
        for alert in ({}, {"signal": ""}, {"signal": "bullish maybe"}, {"signal": "alta e baixa"}):
            with self.subTest(alert=alert):
                with self.assertRaises(ValueError):
                    signal_direction(alert)


class MarketModeSeparationTests(unittest.TestCase):
    def test_spot_long_and_short_research_events_are_disjoint(self):
        events = [
            {"symbol": "BTCUSDT", "direction": 1, "signal": "bullish"},
            {"symbol": "ETHUSDT", "direction": -1, "signal": "bearish"},
        ]
        longs = filter_events_by_market_mode(events, "spot_long")
        shorts = filter_events_by_market_mode(events, "short_research")
        self.assertEqual([event["direction"] for event in longs], [1])
        self.assertEqual([event["direction"] for event in shorts], [-1])
        self.assertFalse(set(map(id, longs)) & set(map(id, shorts)))

    def test_unknown_market_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            filter_events_by_market_mode([], "spot_margin")

    def test_short_research_and_bot_b_do_not_enter_spot_long_gate(self):
        rows = [
            {"split": "test", "market_mode": "spot_long", "agent": "Bot A — Altas expressivas",
             "timeframe": "15m", "trades": 3, "total_return_pct": 1.0},
            {"split": "test", "market_mode": "short_research", "agent": "Bot A — Altas expressivas",
             "timeframe": "15m", "trades": 3, "total_return_pct": -1.0},
            {"split": "test", "market_mode": "spot_long", "agent": "Bot B — Quedas expressivas",
             "timeframe": "15m", "trades": 0, "total_return_pct": 0.0},
            {"split": "test", "market_mode": "spot_long", "agent": "Bot C — Reversões",
             "timeframe": "15m", "trades": 0, "total_return_pct": 0.0},
        ]
        failed = failed_spot_long_test_rows(rows)
        self.assertEqual(len(failed), 2)
        self.assertEqual(
            {row["agent"] for row in failed},
            {"Bot A — Altas expressivas", "Bot C — Reversões"},
        )

    def test_bot_a_requires_at_least_50_out_of_sample_trades(self):
        rows = [{
            "split": "test", "market_mode": "spot_long",
            "agent": "Bot A — Altas expressivas", "timeframe": "1h",
            "trades": 49, "total_return_pct": 2.0,
        }]
        self.assertEqual(len(failed_spot_long_test_rows(rows)), 1)
        rows[0]["trades"] = 50
        self.assertEqual(failed_spot_long_test_rows(rows), [])


    def test_spot_long_uses_buy_hold_and_short_research_uses_cash(self):
        frame = pd.DataFrame([
            {"open_time": "2026-01-01T00:00:00Z", "open": 100.0, "close": 100.0},
            {"open_time": "2026-01-01T00:15:00Z", "open": 110.0, "close": 110.0},
        ])
        frames = {"BTCUSDT": frame}
        starts, ends = {"BTCUSDT": 0}, {"BTCUSDT": 2}
        long_benchmark, long_name = benchmark_for_market_mode(
            "spot_long", frames, starts, ends, 10000, 0, 0
        )
        short_benchmark, short_name = benchmark_for_market_mode(
            "short_research", frames, starts, ends, 10000, 0, 0
        )
        self.assertEqual(long_name, "equal_weight_buy_and_hold")
        self.assertEqual(long_benchmark["total_return_pct"], 10.0)
        self.assertEqual(short_name, "cash_zero_return")
        self.assertEqual(short_benchmark["total_return_pct"], 0.0)
        self.assertEqual(short_benchmark["by_symbol"], {})

    def test_unknown_benchmark_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            benchmark_for_market_mode("spot_margin", {}, {}, {}, 10000, 0, 0)


class RsiEdgeCaseTests(unittest.TestCase):
    def test_flat_series_is_neutral_after_warmup(self):
        self.assertEqual(float(rsi(pd.Series([100.0] * 40)).iloc[-1]), 50.0)

    def test_monotonic_rise_is_100_after_warmup(self):
        self.assertEqual(float(rsi(pd.Series(range(1, 41), dtype=float)).iloc[-1]), 100.0)

    def test_monotonic_fall_is_zero_after_warmup(self):
        self.assertEqual(float(rsi(pd.Series(range(40, 0, -1), dtype=float)).iloc[-1]), 0.0)

    def test_invalid_period_is_rejected(self):
        with self.assertRaises(ValueError):
            rsi(pd.Series([1.0, 2.0]), period=0)


class PortfolioSimulationTests(unittest.TestCase):
    @staticmethod
    def frame(rows):
        result = pd.DataFrame(rows)
        result["open_time"] = pd.to_datetime(result["open_time"], utc=True)
        result["close_time"] = result["open_time"] + pd.Timedelta(minutes=15)
        return result

    def test_stop_wins_when_stop_and_target_touch_same_candle(self):
        frame = self.frame([{
            "open_time": "2026-01-01T00:00:00Z", "open": 100.0,
            "high": 110.0, "low": 95.0, "close": 103.0,
        }])
        result = simulate_portfolio(
            {"BTCUSDT": frame},
            [{"symbol": "BTCUSDT", "entry_index": 0, "direction": 1, "atr": 2.0}],
            {"BTCUSDT": 0}, {"BTCUSDT": 1}, initial_cash=10000,
            fee_per_side_pct=0, slippage_per_side_pct=0,
        )
        self.assertEqual(result["trades"][0]["exit_reason"], "stop")
        self.assertLess(result["trades"][0]["net_pnl"], 0)

    def test_multiple_symbols_can_be_opened_simultaneously(self):
        rows = [{
            "open_time": "2026-01-01T00:00:00Z", "open": 100.0,
            "high": 101.0, "low": 99.0, "close": 100.5,
        }]
        frames = {"BTCUSDT": self.frame(rows), "ETHUSDT": self.frame(rows)}
        events = [{"symbol": s, "entry_index": 0, "direction": 1, "atr": 2.0} for s in frames]
        result = simulate_portfolio(
            frames, events, {"BTCUSDT": 0, "ETHUSDT": 0},
            {"BTCUSDT": 1, "ETHUSDT": 1}, initial_cash=10000,
            fee_per_side_pct=0, slippage_per_side_pct=0, max_positions=2,
        )
        self.assertEqual(len(result["trades"]), 2)

    def test_short_trade_uses_inverse_stop_and_target(self):
        frame = self.frame([{
            "open_time": "2026-01-01T00:00:00Z", "open": 100.0,
            "high": 101.0, "low": 95.0, "close": 96.0,
        }])
        result = simulate_portfolio(
            {"BTCUSDT": frame},
            [{"symbol": "BTCUSDT", "entry_index": 0, "direction": -1, "atr": 2.0}],
            {"BTCUSDT": 0}, {"BTCUSDT": 1}, initial_cash=10000,
            fee_per_side_pct=0, slippage_per_side_pct=0,
        )
        self.assertEqual(result["trades"][0]["direction"], "short")
        self.assertEqual(result["trades"][0]["exit_reason"], "target")
        self.assertGreater(result["trades"][0]["net_pnl"], 0)

    def test_splits_are_chronological_and_non_overlapping(self):
        bounds = chronological_bounds(100)
        self.assertEqual(bounds["train"], (0, 60))
        self.assertEqual(bounds["validation"], (60, 80))
        self.assertEqual(bounds["test"], (80, 100))


if __name__ == "__main__":
    unittest.main()
