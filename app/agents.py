from datetime import timezone
from .indicators import enrich

def _alert(agent, symbol, tf, row, signal, reason, confidence="não calibrada"):
    return {
        "agent": agent, "symbol": symbol, "timeframe": tf,
        "price": round(float(row["close"]), 8),
        "signal": signal, "reason": reason,
        "change_pct": round(float(row["change_pct"]), 3) if row.get("change_pct") == row.get("change_pct") else None,
        "relative_volume": round(float(row["rel_volume"]), 2) if row.get("rel_volume") == row.get("rel_volume") else None,
        "rsi": round(float(row["rsi14"]), 1) if row.get("rsi14") == row.get("rsi14") else None,
        "atr": round(float(row["atr14"]), 8) if row.get("atr14") == row.get("atr14") else None,
        "candle_time_utc": str(row["close_time"]),
        "confidence": confidence,
        "risk_note": "Heurística inicial, ainda não validada por backtest; confirme spread, custos, liquidez e contexto."
    }

def run_market_agents(symbol, tf, df, cfg):
    return run_market_agents_enriched(symbol, tf, enrich(df), cfg)


def run_market_agents_enriched(symbol, tf, d, cfg):
    """Evaluate agents against an already enriched, point-in-time candle frame."""
    out = []
    if len(d) < 55:
        return out
    r = d.iloc[-1]
    prev = d.iloc[-2]
    t = cfg["thresholds"]
    move_threshold = {"15m": t["move_15m_pct"], "1h": t["move_1h_pct"], "4h": t["move_4h_pct"]}.get(tf, t["move_15m_pct"])
    rv = r["rel_volume"]
    change = r["change_pct"]
    if change >= move_threshold and rv >= t["relative_volume"]:
        out.append(_alert("Bot A — Altas expressivas", symbol, tf, r, "movimento de alta",
                          f"Variação positiva de {change:.2f}% com volume relativo {rv:.2f}x. Não implica compra."))
    if change <= -move_threshold and rv >= t["relative_volume"]:
        out.append(_alert("Bot B — Quedas expressivas", symbol, tf, r, "movimento de baixa",
                          f"Variação negativa de {change:.2f}% com volume relativo {rv:.2f}x. Não implica venda."))
    if r["rsi14"] < t["rsi_oversold"] and r["close"] > prev["close"] and r["volume"] > prev["volume"]:
        out.append(_alert("Bot C — Reversões", symbol, tf, r, "possível estabilização após sobrevenda",
                          "RSI baixo e candle recente positivo com volume superior ao candle anterior; reversão não confirmada."))
    if r["rsi14"] > t["rsi_overbought"] and r["close"] < prev["close"] and r["volume"] > prev["volume"]:
        out.append(_alert("Bot C — Reversões", symbol, tf, r, "possível perda de força após sobrecompra",
                          "RSI alto e candle recente negativo com volume superior ao candle anterior; reversão não confirmada."))
    lookback = int(t["breakout_lookback"])
    prior = d.iloc[-lookback-1:-1]
    # Emit a breakout only on the crossing candle, not on every candle that
    # remains beyond the rolling boundary after the initial breakout.
    prior_before = d.iloc[-lookback-2:-2]
    crossed_resistance = (
        len(prior) == lookback and len(prior_before) == lookback
        and r["close"] > prior["high"].max()
        and prev["close"] <= prior_before["high"].max()
    )
    crossed_support = (
        len(prior) == lookback and len(prior_before) == lookback
        and r["close"] < prior["low"].min()
        and prev["close"] >= prior_before["low"].min()
    )
    if crossed_resistance and rv >= t["relative_volume"]:
        out.append(_alert("Bot D — Rompimentos", symbol, tf, r, "possível rompimento de resistência",
                          f"Fecho cruzou acima da máxima das {lookback} velas anteriores com volume relativo {rv:.2f}x; confirmar risco de falso rompimento."))
    if crossed_support and rv >= t["relative_volume"]:
        out.append(_alert("Bot D — Rompimentos", symbol, tf, r, "possível rompimento de suporte",
                          f"Fecho cruzou abaixo da mínima das {lookback} velas anteriores com volume relativo {rv:.2f}x; confirmar risco de falso rompimento."))
    trend_up = r["close"] > r["sma20"] > r["sma50"]
    trend_down = r["close"] < r["sma20"] < r["sma50"]
    previous_trend_up = prev["close"] > prev["sma50"] and prev["sma20"] > prev["sma50"]
    previous_trend_down = prev["close"] < prev["sma50"] and prev["sma20"] < prev["sma50"]
    min_risk_reward = float(t.get("min_risk_reward", 1.5))
    if min_risk_reward <= 0:
        raise ValueError("thresholds.min_risk_reward must be greater than zero")

    if tf == "15m":
        # A 15m swing entry now requires an established uptrend, an actual
        # pullback to SMA20, and a bullish reclaim that closes above the
        # previous candle high. This is deliberately a one-candle trigger.
        pullback_long = (
            previous_trend_up
            and float(prev["low"]) <= float(prev["sma20"])
            and float(prev["close"]) > float(prev["sma50"])
        )
        confirmed_reclaim_long = (
            trend_up
            and float(r["close"]) > float(prev["high"])
            and float(r["close"]) > float(r["sma20"])
            and float(r["rsi14"]) >= float(t.get("swing_pullback_rsi_min", 45))
            and float(r["rsi14"]) <= float(t.get("swing_pullback_rsi_max", 65))
            and float(rv) >= float(t.get("swing_pullback_relative_volume", 1.2))
        )
        if pullback_long and confirmed_reclaim_long:
            stop = float(r["close"] - t["atr_stop_multiple"] * r["atr14"])
            target = float(r["close"] + min_risk_reward * (r["close"] - stop))
            out.append(_alert("Bot E — Swing trade", symbol, tf, r, "configuração potencial de alta",
                              f"Recuo à SMA20 seguido de recuperação acima da máxima anterior; RSI={r['rsi14']:.1f}. Invalidação indicativa: {stop:.8g}; objetivo ilustrativo: {target:.8g}."))
    else:
        # Preserve the previous edge-triggered swing rules on 1h/4h so the
        # experiment isolates the requested 15m change.
        swing_long = trend_up and 40 <= r["rsi14"] <= 68 and rv >= 1.0
        previous_swing_long = previous_trend_up and 40 <= prev["rsi14"] <= 68 and prev["rel_volume"] >= 1.0
        swing_short = trend_down and 32 <= r["rsi14"] <= 60 and rv >= 1.0
        previous_swing_short = previous_trend_down and 32 <= prev["rsi14"] <= 60 and prev["rel_volume"] >= 1.0
        if swing_long and not previous_swing_long:
            stop = float(r["close"] - t["atr_stop_multiple"] * r["atr14"])
            target = float(r["close"] + min_risk_reward * (r["close"] - stop))
            out.append(_alert("Bot E — Swing trade", symbol, tf, r, "configuração potencial de alta",
                              f"Tendência SMA20>SMA50, RSI={r['rsi14']:.1f}; referência técnica, não recomendação. Invalidação indicativa: {stop:.8g}; objetivo ilustrativo: {target:.8g}."))
        elif swing_short and not previous_swing_short:
            stop = float(r["close"] + t["atr_stop_multiple"] * r["atr14"])
            target = float(r["close"] - min_risk_reward * (stop - r["close"]))
            out.append(_alert("Bot E — Swing trade", symbol, tf, r, "configuração potencial de baixa",
                              f"Tendência SMA20<SMA50, RSI={r['rsi14']:.1f}; referência técnica, não recomendação. Invalidação indicativa: {stop:.8g}; objetivo ilustrativo: {target:.8g}."))
    return out

def disabled_agent_status(cfg=None):
    env = (cfg or {}).get("_env", {})
    social_enabled = bool(
        env.get("lunarcrush_enabled")
        or env.get("lunarcrush_api_key")
        or env.get("social_api_key")
    )
    statuses = []
    if not social_enabled:
        statuses.append({
            "agent": "Bot G — Inteligência social", "status": "disabled",
            "reason": "Chave LunarCrush não configurada; coleta e geração de sinais indisponíveis.",
        })
    statuses.extend([
        {"agent": "Bot H — Fundamentalista", "status": "disabled",
         "reason": "Requer coleta de fontes primárias, atividade de desenvolvimento e evidências datadas."},
        {"agent": "Bot J — Tokenomics", "status": "disabled",
         "reason": "Requer dados confiáveis de oferta, distribuição e desbloqueios."},
    ])
    if not cfg or not cfg.get("launches", {}).get("enabled", False) or not cfg.get("_env", {}).get("goplus_enabled", False):
        statuses.insert(0, {
            "agent": "Bot F — Novos lançamentos", "status": "disabled",
            "reason": "Requer descoberta de pools habilitada e GoPlus ativo; sem aprovação não emite alertas.",
        })
    return statuses
