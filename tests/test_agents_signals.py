import unittest
from datetime import datetime, timezone

import pandas as pd

from app.agents import run_market_agents_enriched


CFG = {
    "thresholds": {
        "move_15m_pct": 1.8,
        "move_1h_pct": 3.5,
        "move_4h_pct": 6.0,
        "relative_volume": 1.8,
        "breakout_lookback": 20,
        "rsi_oversold": 30,
        "rsi_overbought": 70,
        "atr_stop_multiple": 1.5,
        "min_risk_reward": 1.5,
    }
}


def frame(**overrides):
    rows = []
    for index in range(55):
        rows.append({
            "close": 100.0,
            "high": 110.0,
            "low": 90.0,
            "volume": 100.0,
            "close_time": datetime(2026, 11, 5, tzinfo=timezone.utc),
            "change_pct": 0.0,
            "rel_volume": 1.0,
            "rsi14": 50.0,
            "atr14": 2.0,
            "sma20": 100.0,
            "sma50": 100.0,
        })
    result = pd.DataFrame(rows)
    for key, value in overrides.items():
        if isinstance(value, tuple):
            result.loc[value[0], key] = value[1]
        else:
            result.loc[54, key] = value
    return result


class ActiveAgentSignalTests(unittest.TestCase):
    def agents(self, data, timeframe="15m"):
        return [x["agent"] for x in run_market_agents_enriched("BTCUSDT", timeframe, data, CFG)]

    def test_bot_a_triggers_at_configured_positive_move_and_relative_volume(self):
        data = frame(change_pct=1.8, rel_volume=1.8, rsi14=75.0)
        self.assertIn("Bot A — Altas expressivas", self.agents(data))

    def test_bot_b_triggers_at_configured_negative_move_and_relative_volume(self):
        data = frame(change_pct=-1.8, rel_volume=1.8, rsi14=75.0)
        self.assertIn("Bot B — Quedas expressivas", self.agents(data))

    def test_bot_c_oversold_reversal_requires_positive_candle_and_higher_volume(self):
        data = frame(
            rsi14=29.9, change_pct=0.2,
            **{"close": (53, 100.0), "volume": (53, 100.0)}
        )
        data.loc[54, "close"] = 100.2
        data.loc[54, "volume"] = 101.0
        self.assertIn("Bot C — Reversões", self.agents(data))

    def test_bot_c_does_not_trigger_without_positive_candle_or_volume_confirmation(self):
        data = frame(rsi14=29.9, change_pct=0.2)
        self.assertNotIn("Bot C — Reversões", self.agents(data))
        data.loc[53, "close"] = 99.0
        data.loc[54, "close"] = 100.0
        data.loc[53, "volume"] = 101.0
        data.loc[54, "volume"] = 100.0
        self.assertNotIn("Bot C — Reversões", self.agents(data))

    def test_bot_c_overbought_reversal_requires_negative_candle_and_higher_volume(self):
        data = frame(rsi14=70.1, change_pct=-0.2)
        data.loc[53, "close"] = 100.0
        data.loc[54, "close"] = 99.8
        data.loc[53, "volume"] = 100.0
        data.loc[54, "volume"] = 101.0
        self.assertIn("Bot C — Reversões", self.agents(data))

    def test_bot_a_uses_the_configured_threshold_for_each_timeframe(self):
        expected = {"15m": 1.8, "1h": 3.5, "4h": 6.0}
        for timeframe, threshold in expected.items():
            with self.subTest(timeframe=timeframe):
                data = frame(change_pct=threshold, rel_volume=1.8, rsi14=75.0)
                self.assertIn("Bot A — Altas expressivas", self.agents(data, timeframe))

    def test_bot_d_upside_breakout_uses_prior_lookback_and_relative_volume(self):
        data = frame(change_pct=0.2, rel_volume=1.8, rsi14=75.0)
        data.loc[54, "close"] = 111.0
        self.assertIn("Bot D — Rompimentos", self.agents(data))

    def test_bot_d_downside_breakout_uses_prior_lookback_and_relative_volume(self):
        data = frame(change_pct=-0.2, rel_volume=1.8, rsi14=25.0)
        data.loc[54, "close"] = 89.0
        self.assertIn("Bot D — Rompimentos", self.agents(data))

    def test_bot_d_does_not_trigger_without_relative_volume_confirmation(self):
        data = frame(change_pct=0.2, rel_volume=1.79, rsi14=75.0)
        data.loc[54, "close"] = 111.0
        self.assertNotIn("Bot D — Rompimentos", self.agents(data))

    def test_bot_e_uptrend_emits_stop_and_target_from_atr(self):
        data = frame(sma20=99.0, sma50=98.0, rsi14=50.0, rel_volume=1.0, atr14=2.0)
        alerts = run_market_agents_enriched("BTCUSDT", "4h", data, CFG)
        alert = next(x for x in alerts if x["agent"] == "Bot E — Swing trade")
        self.assertIn("Invalidação indicativa: 97", alert["reason"])
        self.assertIn("objetivo ilustrativo: 104", alert["reason"])

    def test_bot_e_requires_minimum_relative_volume(self):
        data = frame(sma20=99.0, sma50=98.0, rsi14=50.0, rel_volume=0.99)
        self.assertNotIn("Bot E — Swing trade", self.agents(data))

    def test_bot_e_downtrend_emits_bearish_setup(self):
        data = frame(sma20=101.0, sma50=102.0, rsi14=50.0, rel_volume=1.0)
        alerts = run_market_agents_enriched("BTCUSDT", "4h", data, CFG)
        self.assertTrue(any(
            x["agent"] == "Bot E — Swing trade" and x["signal"] == "configuração potencial de baixa"
            for x in alerts
        ))

    def test_no_agent_emits_signal_when_all_conditions_are_neutral(self):
        self.assertEqual(self.agents(frame()), [])

    def test_agents_require_at_least_55_candles(self):
        self.assertEqual(self.agents(frame().iloc[:54].copy()), [])

    def test_bot_e_long_target_uses_configured_risk_reward(self):
        cfg = {"thresholds": {**CFG["thresholds"], "min_risk_reward": 2.0}}
        data = frame(sma20=99.0, sma50=98.0, rsi14=50.0, rel_volume=1.0, atr14=2.0)
        alerts = run_market_agents_enriched("BTCUSDT", "4h", data, cfg)
        alert = next(x for x in alerts if x["agent"] == "Bot E — Swing trade")
        self.assertIn("objetivo ilustrativo: 106", alert["reason"])

    def test_bot_e_short_target_uses_configured_risk_reward(self):
        cfg = {"thresholds": {**CFG["thresholds"], "min_risk_reward": 2.0}}
        data = frame(sma20=101.0, sma50=102.0, rsi14=50.0, rel_volume=1.0, atr14=2.0)
        alerts = run_market_agents_enriched("BTCUSDT", "4h", data, cfg)
        alert = next(x for x in alerts if x["agent"] == "Bot E — Swing trade")
        self.assertIn("objetivo ilustrativo: 94", alert["reason"])

    def test_bot_e_rejects_non_positive_risk_reward_configuration(self):
        cfg = {"thresholds": {**CFG["thresholds"], "min_risk_reward": 0}}
        data = frame(sma20=99.0, sma50=98.0, rsi14=50.0, rel_volume=1.0)
        with self.assertRaisesRegex(ValueError, "greater than zero"):
            run_market_agents_enriched("BTCUSDT", "4h", data, cfg)


if __name__ == "__main__":
    unittest.main()
