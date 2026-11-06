import unittest

import pandas as pd

from app.agents import run_market_agents_enriched


def make_frame():
    rows = []
    for i in range(60):
        rows.append({
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
            "volume": 100.0, "close_time": f"2026-01-01T{i:02d}:00:00Z",
            "change_pct": 0.0, "rel_volume": 0.5, "rsi14": 50.0,
            "atr14": 1.0, "sma20": 101.0, "sma50": 102.0,
        })
    return pd.DataFrame(rows)


def config():
    return {"thresholds": {
        "move_15m_pct": 99.0, "move_1h_pct": 99.0, "move_4h_pct": 99.0,
        "relative_volume": 1.0, "breakout_lookback": 20,
        "rsi_oversold": 30, "rsi_overbought": 70,
        "atr_stop_multiple": 1.5, "min_risk_reward": 1.5,
    }}


class EdgeTriggeredSignalTests(unittest.TestCase):
    def test_breakout_is_emitted_only_on_crossing_candle(self):
        frame = make_frame()
        frame.loc[58, ["close", "rel_volume"]] = [100.0, 1.2]
        frame.loc[59, ["close", "rel_volume"]] = [103.0, 1.2]
        first = run_market_agents_enriched("BTCUSDT", "15m", frame.iloc[:60], config())
        self.assertEqual(sum(x["agent"] == "Bot D — Rompimentos" for x in first), 1)

        frame.loc[59, ["close", "rel_volume"]] = [104.0, 1.2]
        frame.loc[58, ["close", "rel_volume"]] = [103.0, 1.2]
        second = run_market_agents_enriched("BTCUSDT", "15m", frame, config())
        self.assertFalse(any(x["agent"] == "Bot D — Rompimentos" for x in second))

    def test_swing_setup_is_emitted_once_per_setup_transition(self):
        frame = make_frame()
        frame.loc[58, ["close", "sma20", "sma50", "rsi14", "rel_volume"]] = [110, 105, 100, 50, 1.2]
        frame.loc[59, ["close", "sma20", "sma50", "rsi14", "rel_volume"]] = [111, 105, 100, 51, 1.2]
        repeated = run_market_agents_enriched("BTCUSDT", "15m", frame, config())
        self.assertFalse(any(x["agent"] == "Bot E — Swing trade" for x in repeated))

        frame.loc[58, ["close", "sma20", "sma50", "rsi14", "rel_volume"]] = [100, 105, 102, 35, 0.5]
        entered = run_market_agents_enriched("BTCUSDT", "15m", frame, config())
        self.assertEqual(sum(x["agent"] == "Bot E — Swing trade" for x in entered), 1)


if __name__ == "__main__":
    unittest.main()
