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
    out = []
    d = enrich(df)
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
    if len(prior) == lookback and r["close"] > prior["high"].max() and rv >= t["relative_volume"]:
        out.append(_alert("Bot D — Rompimentos", symbol, tf, r, "possível rompimento de resistência",
                          f"Fecho acima da máxima das {lookback} velas anteriores com volume relativo {rv:.2f}x; confirmar risco de falso rompimento."))
    if len(prior) == lookback and r["close"] < prior["low"].min() and rv >= t["relative_volume"]:
        out.append(_alert("Bot D — Rompimentos", symbol, tf, r, "possível rompimento de suporte",
                          f"Fecho abaixo da mínima das {lookback} velas anteriores com volume relativo {rv:.2f}x; confirmar risco de falso rompimento."))
    trend_up = r["close"] > r["sma20"] > r["sma50"]
    trend_down = r["close"] < r["sma20"] < r["sma50"]
    if trend_up and 40 <= r["rsi14"] <= 68 and rv >= 1.0:
        stop = float(r["close"] - t["atr_stop_multiple"] * r["atr14"])
        target = float(r["close"] + 1.5 * (r["close"] - stop))
        out.append(_alert("Bot E — Swing trade", symbol, tf, r, "configuração potencial de alta",
                          f"Tendência SMA20>SMA50, RSI={r['rsi14']:.1f}; referência técnica, não recomendação. Invalidação indicativa: {stop:.8g}; objetivo ilustrativo: {target:.8g}."))
    elif trend_down and 32 <= r["rsi14"] <= 60 and rv >= 1.0:
        stop = float(r["close"] + t["atr_stop_multiple"] * r["atr14"])
        target = float(r["close"] - 1.5 * (stop - r["close"]))
        out.append(_alert("Bot E — Swing trade", symbol, tf, r, "configuração potencial de baixa",
                          f"Tendência SMA20<SMA50, RSI={r['rsi14']:.1f}; referência técnica, não recomendação. Invalidação indicativa: {stop:.8g}; objetivo ilustrativo: {target:.8g}."))
    return out

def disabled_agent_status():
    return [
        {"agent": "Bot F — Novos lançamentos", "status": "disabled",
         "reason": "Requer fonte de lançamentos/listagens e validação de rede; não emitir sinal sem integração."},
        {"agent": "Bot G — Inteligência social", "status": "disabled",
         "reason": "Requer API social e verificação contra spam/bots; sem fonte configurada."},
        {"agent": "Bot H — Fundamentalista", "status": "disabled",
         "reason": "Requer coleta de fontes primárias, atividade de desenvolvimento e evidências datadas."},
        {"agent": "Bot J — Tokenomics", "status": "disabled",
         "reason": "Requer dados confiáveis de oferta, distribuição e desbloqueios."},
    ]
