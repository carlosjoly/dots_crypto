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
        "risk_note": "Heurística experimental; não aprovada para execução. Confirme spread, custos, liquidez e contexto.",
        "validation_status": "experimental",
        "execution_eligible": False,
    }


def trend_slope_aligned(row, direction):
    """Require both short- and medium-term averages to slope with the signal."""
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or 1")
    short_slope = float(row.get("sma20_slope_3_pct", 0.0))
    medium_slope = float(row.get("sma50_slope_5_pct", 0.0))
    return short_slope * direction > 0 and medium_slope * direction > 0


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
    reversal_volume = float(t.get("reversal_relative_volume", 1.5))
    # Require an actual RSI threshold cross plus price-structure confirmation.
    # A single green/red candle inside an extreme RSI zone is not a reversal.
    if (float(prev["rsi14"]) <= float(t["rsi_oversold"])
            and float(r["rsi14"]) > float(t["rsi_oversold"])
            and float(r["close"]) > float(prev["high"])
            and float(r["close"]) > float(r["open"])
            and float(rv) >= reversal_volume):
        out.append(_alert("Bot C — Reversões", symbol, tf, r, "possível estabilização após sobrevenda",
                          "RSI cruzou acima da sobrevenda e o preço confirmou a máxima anterior com volume relativo suficiente; ainda é sinal experimental."))
    if (float(prev["rsi14"]) >= float(t["rsi_overbought"])
            and float(r["rsi14"]) < float(t["rsi_overbought"])
            and float(r["close"]) < float(prev["low"])
            and float(r["close"]) < float(r["open"])
            and float(rv) >= reversal_volume):
        out.append(_alert("Bot C — Reversões", symbol, tf, r, "possível perda de força após sobrecompra",
                          "RSI cruzou abaixo da sobrecompra e o preço confirmou a mínima anterior com volume relativo suficiente; ainda é sinal experimental."))
    # Earlier momentum-shift warnings complement the stricter extreme-RSI
    # reversal confirmations above. They are informational and require a
    # midline RSI cross, price/SMA20 agreement, candle direction and volume.
    momentum_reversal_volume = float(t.get("reversal_momentum_relative_volume", 1.2))
    bullish_momentum_shift = (
        float(prev["rsi14"]) <= 50 < float(r["rsi14"])
        and float(r["close"]) > float(r["sma20"])
        and float(r["close"]) > float(r["open"])
        and float(rv) >= momentum_reversal_volume
    )
    bearish_momentum_shift = (
        float(prev["rsi14"]) >= 50 > float(r["rsi14"])
        and float(r["close"]) < float(r["sma20"])
        and float(r["close"]) < float(r["open"])
        and float(rv) >= momentum_reversal_volume
    )
    if bullish_momentum_shift:
        out.append(_alert(
            "Bot C — Reversões", symbol, tf, r,
            "atenção: recuperação inicial do momentum",
            f"RSI cruzou acima de 50, preço fechou acima da SMA20 com candle comprador e volume relativo {float(rv):.2f}x. Recuperação ainda não confirmada como reversão de tendência.",
        ))
    elif bearish_momentum_shift:
        out.append(_alert(
            "Bot C — Reversões", symbol, tf, r,
            "atenção: perda inicial do momentum",
            f"RSI cruzou abaixo de 50, preço fechou abaixo da SMA20 com candle vendedor e volume relativo {float(rv):.2f}x. Enfraquecimento ainda não confirma tendência de baixa.",
        ))
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
    breakout_buffer = float(t.get("breakout_buffer_atr", 0.10)) * float(r["atr14"])
    breakout_rsi_long = (
        float(t.get("breakout_rsi_long_min", 50))
        <= float(r["rsi14"]) <= float(t.get("breakout_rsi_long_max", 75))
    )
    breakout_rsi_short = (
        float(t.get("breakout_rsi_short_min", 25))
        <= float(r["rsi14"]) <= float(t.get("breakout_rsi_short_max", 50))
    )
    bullish_regime = (float(r["close"]) > float(r["sma20"]) > float(r["sma50"])
                      and trend_slope_aligned(r, 1))
    bearish_regime = (float(r["close"]) < float(r["sma20"]) < float(r["sma50"])
                      and trend_slope_aligned(r, -1))
    bullish_candle = float(r["close"]) > float(r["open"])
    bearish_candle = float(r["close"]) < float(r["open"])
    if (crossed_resistance and bullish_regime and bullish_candle
            and rv >= max(float(t["relative_volume"]), float(t.get("breakout_relative_volume", 2.0)))
            and breakout_rsi_long
            and float(r["close"]) > float(prior["high"].max()) + breakout_buffer):
        out.append(_alert("Bot D — Rompimentos", symbol, tf, r, "possível rompimento de resistência",
                          f"Rompimento acima da máxima das {lookback} velas com tendência alinhada, candle comprador, margem de {breakout_buffer:.8g} (0,10 ATR), volume relativo {rv:.2f}x e RSI={r['rsi14']:.1f}; experimental."))
    if (crossed_support and bearish_regime and bearish_candle
            and rv >= max(float(t["relative_volume"]), float(t.get("breakout_relative_volume", 2.0)))
            and breakout_rsi_short
            and float(r["close"]) < float(prior["low"].min()) - breakout_buffer):
        out.append(_alert("Bot D — Rompimentos", symbol, tf, r, "possível rompimento de suporte",
                          f"Rompimento abaixo da mínima das {lookback} velas com tendência alinhada, candle vendedor, margem de {breakout_buffer:.8g} (0,10 ATR), volume relativo {rv:.2f}x e RSI={r['rsi14']:.1f}; experimental."))
    trend_up = r["close"] > r["sma20"] > r["sma50"] and trend_slope_aligned(r, 1)
    trend_down = r["close"] < r["sma20"] < r["sma50"] and trend_slope_aligned(r, -1)
    previous_trend_up = prev["close"] > prev["sma20"] > prev["sma50"]
    previous_trend_down = prev["close"] < prev["sma20"] < prev["sma50"]
    min_risk_reward = float(t.get("min_risk_reward", 1.5))
    if min_risk_reward <= 0:
        raise ValueError("thresholds.min_risk_reward must be greater than zero")

    # Apply the same pullback/reclaim structure on every timeframe. Trend-only
    # transitions on 1h/4h were too permissive and were not supported by the
    # historical results. Short signals remain research-only on Spot.
    min_swing_volume = float(t.get("swing_pullback_relative_volume", 1.2))
    pullback_long = (
        float(prev["close"]) > float(prev["sma50"])
        and float(prev["sma20"]) > float(prev["sma50"])
        and float(prev["low"]) <= float(prev["sma20"])
    )
    confirmed_reclaim_long = (
        trend_up
        and float(r["close"]) > float(prev["high"])
        and float(r["close"]) > float(r["sma20"])
        and float(r["rsi14"]) >= float(t.get("swing_pullback_rsi_min", 45))
        and float(r["rsi14"]) <= float(t.get("swing_pullback_rsi_max", 65))
        and float(rv) >= min_swing_volume
    )
    pullback_short = (
        float(prev["close"]) < float(prev["sma50"])
        and float(prev["sma20"]) < float(prev["sma50"])
        and float(prev["high"]) >= float(prev["sma20"])
    )
    confirmed_breakdown_short = (
        trend_down
        and float(r["close"]) < float(prev["low"])
        and float(r["close"]) < float(r["sma20"])
        and float(r["rsi14"]) >= float(t.get("swing_pullback_short_rsi_min", 35))
        and float(r["rsi14"]) <= float(t.get("swing_pullback_short_rsi_max", 55))
        and float(rv) >= min_swing_volume
    )
    # Early regime warning: a loss of the 20-period mean while the prior
    # candle still had bullish structure. This is deliberately an ATTENTION
    # signal, not a confirmed short or a trading instruction.
    regime_rsi_max = float(t.get("regime_warning_rsi_max", 48))
    regime_min_volume = float(t.get("regime_warning_relative_volume", 1.0))
    prior_bullish_structure = float(prev["close"]) > float(prev["sma20"]) > float(prev["sma50"])
    lost_sma20 = float(r["close"]) < float(r["sma20"])
    sma20_weakening = float(r["sma20"]) < float(prev["sma20"])
    momentum_weak = float(r["rsi14"]) <= regime_rsi_max
    downside_confirmation = float(rv) >= regime_min_volume or float(r["close"]) < float(prev["low"])
    early_bearish_transition = (
        prior_bullish_structure and lost_sma20 and sma20_weakening
        and momentum_weak and downside_confirmation
    )
    if early_bearish_transition:
        out.append(_alert(
            "Bot E — Mudança de regime", symbol, tf, r,
            "atenção: possível perda da tendência de alta",
            f"Preço fechou abaixo da SMA20 após estrutura anterior de alta; SMA20 perdeu inclinação, RSI={float(r['rsi14']):.1f} e confirmação baixista observada. Não confirma tendência de baixa; aguardar novos candles.",
        ))

    if pullback_long and confirmed_reclaim_long:
        stop = float(r["close"] - t["atr_stop_multiple"] * r["atr14"])
        target = float(r["close"] + min_risk_reward * (r["close"] - stop))
        out.append(_alert("Bot E — Swing trade", symbol, tf, r, "configuração potencial de alta",
                          f"Recuo à SMA20 seguido de recuperação acima da máxima anterior; RSI={r['rsi14']:.1f}. Invalidação indicativa: {stop:.8g}; objetivo ilustrativo: {target:.8g}."))
    elif pullback_short and confirmed_breakdown_short:
        stop = float(r["close"] + t["atr_stop_multiple"] * r["atr14"])
        target = float(r["close"] - min_risk_reward * (stop - r["close"]))
        out.append(_alert("Bot E — Swing trade", symbol, tf, r, "configuração potencial de baixa",
                          f"Reteste à SMA20 seguido de continuação abaixo da mínima anterior; RSI={r['rsi14']:.1f}. Invalidação indicativa: {stop:.8g}; objetivo ilustrativo: {target:.8g}."))
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


def diagnose_market_agents(symbol, tf, df, cfg, alerts=None):
    """Return point-in-time decision diagnostics for every A-E agent, including no-signal cases."""
    d = enrich(df)
    if len(d) < 55:
        return [{
            "agent": name, "symbol": symbol, "timeframe": tf,
            "decision": "not_evaluated", "reason": "insufficient_candles",
            "candles_available": int(len(d)), "candles_required": 55,
        } for name in (
            "Bot A — Altas expressivas", "Bot B — Quedas expressivas",
            "Bot C — Reversões", "Bot D — Rompimentos",
            "Bot E — Swing trade", "Bot E — Mudança de regime",
        )]
    r, prev = d.iloc[-1], d.iloc[-2]
    t = cfg["thresholds"]
    move_threshold = {"15m": t["move_15m_pct"], "1h": t["move_1h_pct"], "4h": t["move_4h_pct"]}.get(tf, t["move_15m_pct"])
    rv, change, rsi = float(r["rel_volume"]), float(r["change_pct"]), float(r["rsi14"])
    emitted = {(x.get("agent"), x.get("signal")) for x in (alerts or [])}
    def record(agent, criteria, signal=None):
        groups = [value for value in criteria.values() if isinstance(value, dict)]
        passed = (
            any(all(bool(item) for item in group.values()) for group in groups)
            if groups else all(bool(value) for value in criteria.values())
        )
        matching = any(name == agent for name, _ in emitted)
        return {
            "agent": agent, "symbol": symbol, "timeframe": tf,
            "decision": "signal_emitted" if matching else ("criteria_not_met" if not passed else "no_signal"),
            "criteria": criteria,
            "observed": {
                "close": float(r["close"]), "change_pct": change,
                "relative_volume": rv, "rsi14": rsi,
                "sma20": float(r["sma20"]), "sma50": float(r["sma50"]),
                "previous_close": float(prev["close"]),
                "previous_low": float(prev["low"]),
                "previous_high": float(prev["high"]),
                "candle_time_utc": str(r["close_time"]),
            },
        }
    lookback = int(t["breakout_lookback"])
    prior = d.iloc[-lookback-1:-1]
    prior_before = d.iloc[-lookback-2:-2]
    crossed_support = (len(prior) == lookback and len(prior_before) == lookback
        and r["close"] < prior["low"].min() and prev["close"] >= prior_before["low"].min())
    crossed_resistance = (len(prior) == lookback and len(prior_before) == lookback
        and r["close"] > prior["high"].max() and prev["close"] <= prior_before["high"].max())
    buffer = float(t.get("breakout_buffer_atr", 0.10)) * float(r["atr14"])
    min_d_volume = max(float(t["relative_volume"]), float(t.get("breakout_relative_volume", 2.0)))
    min_swing_volume = float(t.get("swing_pullback_relative_volume", 1.2))
    prior_bull = prev["close"] > prev["sma20"] > prev["sma50"]
    prior_bear = prev["close"] < prev["sma20"] < prev["sma50"]
    regime_warning = (prior_bull and r["close"] < r["sma20"]
        and r["sma20"] < prev["sma20"]
        and rsi <= float(t.get("regime_warning_rsi_max", 48))
        and (rv >= float(t.get("regime_warning_relative_volume", 1.0)) or r["close"] < prev["low"]))
    rows = [
        record("Bot A — Altas expressivas", {"move_threshold_met": change >= move_threshold, "relative_volume_met": rv >= float(t["relative_volume"])}),
        record("Bot B — Quedas expressivas", {"move_threshold_met": change <= -move_threshold, "relative_volume_met": rv >= float(t["relative_volume"])}),
        record("Bot C — Reversões", {
            "bullish_extreme_reversal": {
                "rsi_cross": bool(prev["rsi14"] <= float(t["rsi_oversold"]) and rsi > float(t["rsi_oversold"])),
                "price_break": bool(r["close"] > prev["high"] and r["close"] > r["open"]),
                "volume": rv >= float(t.get("reversal_relative_volume", 1.5)),
            },
            "bearish_extreme_reversal": {
                "rsi_cross": bool(prev["rsi14"] >= float(t["rsi_overbought"]) and rsi < float(t["rsi_overbought"])),
                "price_break": bool(r["close"] < prev["low"] and r["close"] < r["open"]),
                "volume": rv >= float(t.get("reversal_relative_volume", 1.5)),
            },
            "bullish_midline_shift": {
                "rsi_cross": bool(prev["rsi14"] <= 50 < rsi),
                "price_above_sma20": bool(r["close"] > r["sma20"]),
                "bullish_candle": bool(r["close"] > r["open"]),
                "volume": rv >= float(t.get("reversal_momentum_relative_volume", 1.2)),
            },
            "bearish_midline_shift": {
                "rsi_cross": bool(prev["rsi14"] >= 50 > rsi),
                "price_below_sma20": bool(r["close"] < r["sma20"]),
                "bearish_candle": bool(r["close"] < r["open"]),
                "volume": rv >= float(t.get("reversal_momentum_relative_volume", 1.2)),
            },
        }),
        record("Bot D — Rompimentos", {
            "bullish_breakout": {
                "crossed_resistance": bool(crossed_resistance),
                "bullish_regime": bool(r["close"] > r["sma20"] > r["sma50"]),
                "short_slope_positive": bool(float(r.get("sma20_slope_3_pct", 0.0)) > 0),
                "medium_slope_positive": bool(float(r.get("sma50_slope_5_pct", 0.0)) > 0),
                "bullish_candle": bool(r["close"] > r["open"]),
                "relative_volume": rv >= min_d_volume,
                "breakout_clearance_atr": bool(crossed_resistance and r["close"] > prior["high"].max() + buffer),
                "rsi_range": float(t.get("breakout_rsi_long_min", 50)) <= rsi <= float(t.get("breakout_rsi_long_max", 75)),
            },
            "bearish_breakdown": {
                "crossed_support": bool(crossed_support),
                "bearish_regime": bool(r["close"] < r["sma20"] < r["sma50"]),
                "short_slope_negative": bool(float(r.get("sma20_slope_3_pct", 0.0)) < 0),
                "medium_slope_negative": bool(float(r.get("sma50_slope_5_pct", 0.0)) < 0),
                "bearish_candle": bool(r["close"] < r["open"]),
                "relative_volume": rv >= min_d_volume,
                "breakout_clearance_atr": bool(crossed_support and r["close"] < prior["low"].min() - buffer),
                "rsi_range": float(t.get("breakout_rsi_short_min", 25)) <= rsi <= float(t.get("breakout_rsi_short_max", 50)),
            },
        }),
        record("Bot E — Swing trade", {
            "bullish_pullback_reclaim": {
                "bullish_prior_trend": bool(prev["close"] > prev["sma50"] and prev["sma20"] > prev["sma50"]),
                "trend_slopes_aligned": bool(trend_slope_aligned(r, 1)),
                "pullback_to_sma20": bool(prev["low"] <= prev["sma20"]),
                "confirmed_reclaim": bool(r["close"] > r["sma20"] > r["sma50"] and r["close"] > prev["high"]),
                "rsi_range": float(t.get("swing_pullback_rsi_min", 45)) <= rsi <= float(t.get("swing_pullback_rsi_max", 65)),
                "relative_volume": rv >= min_swing_volume,
            },
            "bearish_retest_breakdown": {
                "bearish_prior_trend": bool(prev["close"] < prev["sma50"] and prev["sma20"] < prev["sma50"]),
                "trend_slopes_aligned": bool(trend_slope_aligned(r, -1)),
                "retest_to_sma20": bool(prev["high"] >= prev["sma20"]),
                "confirmed_breakdown": bool(r["close"] < r["sma20"] < r["sma50"] and r["close"] < prev["low"]),
                "rsi_range": float(t.get("swing_pullback_short_rsi_min", 35)) <= rsi <= float(t.get("swing_pullback_short_rsi_max", 55)),
                "relative_volume": rv >= min_swing_volume,
            },
        }),
        record("Bot E — Mudança de regime", {
            "previous_bullish_structure": bool(prior_bull), "close_below_sma20": bool(r["close"] < r["sma20"]),
            "sma20_slope_negative": bool(r["sma20"] < prev["sma20"]),
            "rsi_weak": rsi <= float(t.get("regime_warning_rsi_max", 48)),
            "downside_confirmation": bool(rv >= float(t.get("regime_warning_relative_volume", 1.0)) or r["close"] < prev["low"]),
        }),
    ]
    return rows
