"""Continuous, read-only market-regime monitor with persistent transition state."""
import json
import os
from pathlib import Path

import pandas as pd

from .indicators import enrich


REGIME_LABELS = {
    "bullish": "tendência de alta",
    "bearish": "tendência de baixa",
    "sideways": "mercado lateral/estável",
}


def classify_trend(candles, slope_floor_pct=0.02):
    """Classify only closed-candle data; non-aligned/weak structure is sideways."""
    if candles is None or len(candles) < 55:
        return {"regime": None, "reason": "historico_insuficiente"}
    data = enrich(candles)
    row = data.iloc[-1]
    required = ["close", "sma20", "sma50", "rsi14", "atr14"]
    if any(pd.isna(row.get(key)) for key in required):
        return {"regime": None, "reason": "indicadores_indisponiveis"}
    close = float(row["close"])
    sma20 = float(row["sma20"])
    sma50 = float(row["sma50"])
    rsi = float(row["rsi14"])
    slope20 = float((sma20 / float(data["sma20"].iloc[-4]) - 1) * 100)
    slope50 = float((sma50 / float(data["sma50"].iloc[-6]) - 1) * 100)
    if close > sma20 > sma50 and slope20 > slope_floor_pct and slope50 > slope_floor_pct and rsi >= 50:
        regime = "bullish"
    elif close < sma20 < sma50 and slope20 < -slope_floor_pct and slope50 < -slope_floor_pct and rsi <= 50:
        regime = "bearish"
    else:
        regime = "sideways"
    return {
        "regime": regime,
        "reason": "estrutura_de_preco_medias_inclinacao_e_rsi",
        "price": close,
        "sma20": sma20,
        "sma50": sma50,
        "sma20_slope_pct": slope20,
        "sma50_slope_pct": slope50,
        "rsi": rsi,
        "candle_time_utc": str(row.get("close_time")),
    }


class TrendStateStore:
    """Atomic JSON persistence; transitions require distinct closed candles."""

    def __init__(self, path, confirmations=2):
        self.path = Path(path)
        self.confirmations = int(confirmations)
        if self.confirmations < 1:
            raise ValueError("confirmations must be >= 1")
        self.state = {"version": 1, "markets": {}}
        if self.path.exists():
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("markets"), dict):
                raise ValueError("Invalid trend monitor state file")
            self.state = payload

    @staticmethod
    def _key(symbol, timeframe):
        return f"{symbol}|{timeframe}"

    def observe(self, symbol, timeframe, snapshot):
        """Return an alert on first confirmed state or confirmed regime change."""
        regime = snapshot.get("regime")
        candle_time = snapshot.get("candle_time_utc")
        if regime not in REGIME_LABELS or not candle_time:
            return None
        key = self._key(symbol, timeframe)
        market = self.state["markets"].setdefault(key, {
            "regime": None, "pending_regime": None, "pending_count": 0,
            "last_candle_time_utc": None,
        })
        if market.get("last_candle_time_utc") == candle_time:
            return None
        market["last_candle_time_utc"] = candle_time
        if market.get("regime") == regime:
            market["pending_regime"] = None
            market["pending_count"] = 0
            return None
        if market.get("pending_regime") == regime:
            market["pending_count"] = int(market.get("pending_count", 0)) + 1
        else:
            market["pending_regime"] = regime
            market["pending_count"] = 1
        if market["pending_count"] < self.confirmations:
            return None
        previous = market.get("regime")
        market["regime"] = regime
        market["pending_regime"] = None
        market["pending_count"] = 0
        previous_label = REGIME_LABELS.get(previous, "estado inicial")
        return {
            "agent": "Monitor contínuo de tendência",
            "symbol": symbol,
            "timeframe": timeframe,
            "price": round(float(snapshot["price"]), 8),
            "signal": (
                f"estado inicial: {REGIME_LABELS[regime]}"
                if previous is None else
                f"mudança de {previous_label} para {REGIME_LABELS[regime]}"
            ),
            "reason": (
                f"Classificação confirmada em {self.confirmations} candles fechados distintos. "
                f"SMA20={snapshot['sma20']:.8g}; SMA50={snapshot['sma50']:.8g}; "
                f"inclinação SMA20={snapshot['sma20_slope_pct']:.4f}%; "
                f"inclinação SMA50={snapshot['sma50_slope_pct']:.4f}%; "
                f"RSI={snapshot['rsi']:.1f}."
            ),
            "rsi": round(float(snapshot["rsi"]), 1),
            "candle_time_utc": candle_time,
            "validation_status": "experimental",
            "execution_eligible": False,
            "risk_note": "Monitoramento informativo de tendência; não é recomendação nem ordem.",
        }

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
