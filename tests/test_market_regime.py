import unittest
from datetime import datetime, timezone
import pandas as pd
from app.agents import diagnose_market_agents, run_market_agents_enriched

CFG = {"thresholds": {
    "move_15m_pct": 1.8, "move_1h_pct": 3.5, "move_4h_pct": 6.0,
    "relative_volume": 1.8, "reversal_relative_volume": 1.5,
    "breakout_relative_volume": 2.0, "breakout_lookback": 20,
    "breakout_buffer_atr": 0.1, "breakout_rsi_long_min": 50,
    "breakout_rsi_long_max": 75, "breakout_rsi_short_min": 25,
    "breakout_rsi_short_max": 50, "rsi_oversold": 30,
    "rsi_overbought": 70, "atr_stop_multiple": 1.5,
    "min_risk_reward": 1.5, "swing_pullback_relative_volume": 1.2,
    "regime_warning_rsi_max": 48, "regime_warning_relative_volume": 1.0,
}}

def frame():
    rows=[]
    for i in range(55):
        rows.append({"open":100.0,"high":101.0,"low":99.0,"close":100.0,
            "volume":100.0,"close_time":datetime(2026,11,5,tzinfo=timezone.utc),
            "change_pct":0.0,"rel_volume":1.0,"rsi14":50.0,"atr14":2.0,
            "sma20":100.0,"sma50":98.0})
    return pd.DataFrame(rows)

class MarketRegimeTests(unittest.TestCase):
    def test_early_bearish_transition_warns_before_sma50_cross(self):
        d=frame()
        d.loc[53,["close","sma20","sma50","high","low","rsi14"]]=[102,101,98,103,100,55]
        d.loc[54,["open","close","sma20","sma50","rsi14","rel_volume"]]=[100,99,100.5,98,46,1.05]
        alerts=run_market_agents_enriched("BTCUSDT","15m",d,CFG)
        alert=next(x for x in alerts if x["agent"]=="Bot E — Mudança de regime")
        self.assertEqual(alert["validation_status"],"experimental")
        self.assertIs(alert["execution_eligible"],False)
        self.assertLess(99,100.5)
        self.assertGreater(100.5,98)

    def test_bot_c_warns_on_bearish_rsi_midline_shift_before_extreme_oversold(self):
        d=frame()
        d.loc[53,["rsi14","close","sma20","sma50"]]=[52,101,100,98]
        d.loc[54,["rsi14","open","close","sma20","sma50","rel_volume"]]=[48,100,99,100,98,1.3]
        alerts=run_market_agents_enriched("BTCUSDT","15m",d,CFG)
        alert=next(x for x in alerts if x["agent"]=="Bot C — Reversões" and x["signal"]=="atenção: perda inicial do momentum")
        self.assertFalse(alert["execution_eligible"])

    def test_bot_c_does_not_warn_on_midline_cross_without_volume(self):
        d=frame()
        d.loc[53,["rsi14","close","sma20"]]=[52,101,100]
        d.loc[54,["rsi14","open","close","sma20","rel_volume"]]=[48,100,99,100,0.8]
        alerts=run_market_agents_enriched("BTCUSDT","15m",d,CFG)
        self.assertFalse(any(x.get("signal")=="atenção: perda inicial do momentum" for x in alerts))

    def test_early_warning_requires_multiple_confirmations(self):
        d=frame()
        d.loc[53,["close","sma20","sma50"]]=[102,101,98]
        d.loc[54,["close","sma20","sma50","rsi14","rel_volume"]]=[99,100.5,98,55,1.1]
        alerts=run_market_agents_enriched("BTCUSDT","15m",d,CFG)
        self.assertFalse(any(x["agent"]=="Bot E — Mudança de regime" for x in alerts))

    def test_diagnostics_explain_each_agent_even_when_no_signal(self):
        d=frame()
        rows=diagnose_market_agents("BTCUSDT","15m",d,CFG,[])
        self.assertEqual(len(rows),6)
        by_name={x["agent"]:x for x in rows}
        self.assertEqual(by_name["Bot A — Altas expressivas"]["decision"],"criteria_not_met")
        self.assertIn("bullish_breakout",by_name["Bot D — Rompimentos"]["criteria"])
        self.assertIn("bearish_breakdown",by_name["Bot D — Rompimentos"]["criteria"])
        self.assertFalse(by_name["Bot D — Rompimentos"]["criteria"]["bullish_breakout"]["crossed_resistance"])
        self.assertIn("previous_bullish_structure",by_name["Bot E — Mudança de regime"]["criteria"])

    def test_diagnostics_mark_insufficient_history(self):
        rows=diagnose_market_agents("BTCUSDT","15m",frame().iloc[:40],CFG,[])
        self.assertTrue(all(x["decision"]=="not_evaluated" for x in rows))

if __name__=="__main__": unittest.main()
