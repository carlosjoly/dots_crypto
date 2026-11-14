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
            "open": 100.0,
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

    def test_bot_c_oversold_reversal_requires_rsi_cross_price_break_and_relative_volume(self):
        data = frame(rsi14=30.1, rel_volume=1.5)
        data.loc[53, ["rsi14", "high", "close"]] = [29.9, 100.0, 99.8]
        data.loc[54, ["open", "close", "rel_volume"]] = [100.0, 100.2, 1.5]
        self.assertIn("Bot C — Reversões", self.agents(data))

    def test_bot_c_rejects_reversal_without_rsi_cross_or_break_confirmation(self):
        data = frame(rsi14=29.9, rel_volume=2.0)
        self.assertNotIn("Bot C — Reversões", self.agents(data))
        data.loc[53, ["rsi14", "high", "close"]] = [29.0, 100.0, 99.0]
        data.loc[54, ["rsi14", "open", "close", "rel_volume"]] = [30.1, 100.0, 99.9, 2.0]
        self.assertNotIn("Bot C — Reversões", self.agents(data))

    def test_bot_c_overbought_reversal_requires_rsi_cross_and_support_break(self):
        data = frame(rsi14=69.9, rel_volume=1.5)
        data.loc[53, ["rsi14", "low", "close"]] = [70.1, 100.0, 100.2]
        data.loc[54, ["open", "close", "rel_volume"]] = [100.0, 99.8, 1.5]
        self.assertIn("Bot C — Reversões", self.agents(data))

    def test_bot_a_uses_the_configured_threshold_for_each_timeframe(self):
        expected = {"15m": 1.8, "1h": 3.5, "4h": 6.0}
        for timeframe, threshold in expected.items():
            with self.subTest(timeframe=timeframe):
                data = frame(change_pct=threshold, rel_volume=1.8, rsi14=75.0)
                self.assertIn("Bot A — Altas expressivas", self.agents(data, timeframe))

    def test_bot_d_upside_breakout_uses_prior_lookback_and_relative_volume(self):
        data = frame(change_pct=0.2, rel_volume=2.0, rsi14=60.0, sma20=101.0, sma50=99.0)
        data.loc[54, ["open", "close"]] = [110.0, 111.0]
        self.assertIn("Bot D — Rompimentos", self.agents(data))

    def test_bot_d_downside_breakout_uses_prior_lookback_and_relative_volume(self):
        data = frame(change_pct=-0.2, rel_volume=2.0, rsi14=40.0, sma20=99.0, sma50=101.0)
        data.loc[54, ["open", "close"]] = [90.0, 89.0]
        self.assertIn("Bot D — Rompimentos", self.agents(data))

    def test_bot_d_rejects_shallow_breakout_without_atr_clearance(self):
        data = frame(change_pct=0.2, rel_volume=2.0, rsi14=60.0, sma20=101.0, sma50=99.0)
        data.loc[54, ["open", "close"]] = [110.0, 110.1]
        self.assertNotIn("Bot D — Rompimentos", self.agents(data))

    def test_bot_d_rejects_breakout_with_exhausted_rsi(self):
        data = frame(change_pct=0.2, rel_volume=2.0, rsi14=80.0, sma20=101.0, sma50=99.0)
        data.loc[54, ["open", "close"]] = [110.0, 112.0]
        self.assertNotIn("Bot D — Rompimentos", self.agents(data))

    def test_bot_d_does_not_trigger_without_relative_volume_confirmation(self):
        data = frame(change_pct=0.2, rel_volume=1.99, rsi14=60.0, sma20=101.0, sma50=99.0)
        data.loc[54, ["open", "close"]] = [110.0, 111.0]
        self.assertNotIn("Bot D — Rompimentos", self.agents(data))

    def test_bot_e_uptrend_emits_stop_and_target_from_atr(self):
        data = frame(sma20=99.0, sma50=98.0, rsi14=50.0, rel_volume=1.2, atr14=2.0)
        data.loc[53, ["close", "high", "low", "sma20", "sma50"]] = [100.0, 99.0, 98.0, 99.0, 98.0]
        data.loc[54, ["open", "close", "high", "low"]] = [99.0, 100.0, 101.0, 98.0]
        alerts = run_market_agents_enriched("BTCUSDT", "4h", data, CFG)
        alert = next(x for x in alerts if x["agent"] == "Bot E — Swing trade")
        self.assertIn("Invalidação indicativa: 97", alert["reason"])
        self.assertIn("objetivo ilustrativo: 104", alert["reason"])

    def test_bot_e_requires_minimum_relative_volume(self):
        data = frame(sma20=99.0, sma50=98.0, rsi14=50.0, rel_volume=0.99)
        self.assertNotIn("Bot E — Swing trade", self.agents(data))

    def test_bot_e_downtrend_emits_bearish_setup(self):
        data = frame(sma20=101.0, sma50=102.0, rsi14=50.0, rel_volume=1.2)
        data.loc[53, ["close", "high", "low", "sma20", "sma50"]] = [100.0, 102.0, 101.0, 102.0, 103.0]
        data.loc[54, ["open", "close", "high", "low"]] = [101.0, 100.0, 102.0, 99.0]
        alerts = run_market_agents_enriched("BTCUSDT", "4h", data, CFG)
        self.assertTrue(any(
            x["agent"] == "Bot E — Swing trade" and x["signal"] == "configuração potencial de baixa"
            for x in alerts
        ))

    def test_all_market_agent_alerts_are_explicitly_experimental(self):
        data = frame(change_pct=1.8, rel_volume=1.8, rsi14=60.0)
        alert = next(
            x for x in run_market_agents_enriched("BTCUSDT", "15m", data, CFG)
            if x["agent"] == "Bot A — Altas expressivas"
        )
        self.assertEqual(alert["validation_status"], "experimental")
        self.assertIs(alert["execution_eligible"], False)

    def test_no_agent_emits_signal_when_all_conditions_are_neutral(self):
        self.assertEqual(self.agents(frame()), [])

    def test_agents_require_at_least_55_candles(self):
        self.assertEqual(self.agents(frame().iloc[:54].copy()), [])

    def test_bot_e_long_target_uses_configured_risk_reward(self):
        cfg = {"thresholds": {**CFG["thresholds"], "min_risk_reward": 2.0}}
        data = frame(sma20=99.0, sma50=98.0, rsi14=50.0, rel_volume=1.2, atr14=2.0)
        data.loc[53, ["close", "high", "low", "sma20", "sma50"]] = [100.0, 99.0, 98.0, 99.0, 98.0]
        data.loc[54, ["open", "close", "high", "low"]] = [99.0, 100.0, 101.0, 98.0]
        alerts = run_market_agents_enriched("BTCUSDT", "4h", data, cfg)
        alert = next(x for x in alerts if x["agent"] == "Bot E — Swing trade")
        self.assertIn("objetivo ilustrativo: 106", alert["reason"])

    def test_bot_e_short_target_uses_configured_risk_reward(self):
        cfg = {"thresholds": {**CFG["thresholds"], "min_risk_reward": 2.0}}
        data = frame(sma20=101.0, sma50=102.0, rsi14=50.0, rel_volume=1.2, atr14=2.0)
        data.loc[53, ["close", "high", "low", "sma20", "sma50"]] = [100.0, 102.0, 101.0, 102.0, 103.0]
        data.loc[54, ["open", "close", "high", "low"]] = [101.0, 100.0, 102.0, 99.0]
        alerts = run_market_agents_enriched("BTCUSDT", "4h", data, cfg)
        alert = next(x for x in alerts if x["agent"] == "Bot E — Swing trade")
        self.assertIn("objetivo ilustrativo: 94", alert["reason"])

    def test_bot_e_rejects_non_positive_risk_reward_configuration(self):
        cfg = {"thresholds": {**CFG["thresholds"], "min_risk_reward": 0}}
        data = frame(sma20=99.0, sma50=98.0, rsi14=50.0, rel_volume=1.2)
        with self.assertRaisesRegex(ValueError, "greater than zero"):
            run_market_agents_enriched("BTCUSDT", "4h", data, cfg)


if __name__ == "__main__":
    unittest.main()
