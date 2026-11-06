import numpy as np


def rsi(close, period=14):
    """Wilder RSI with explicit handling for flat, rising-only and falling-only series."""
    if int(period) <= 0:
        raise ValueError("period must be a positive integer")
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    result = 100 - (100 / (1 + rs))
    flat = gain.eq(0) & loss.eq(0)
    rising_only = loss.eq(0) & gain.gt(0)
    falling_only = gain.eq(0) & loss.gt(0)
    return result.mask(flat, 50.0).mask(rising_only, 100.0).mask(falling_only, 0.0)


def atr(df, period=14):
    if int(period) <= 0:
        raise ValueError("period must be a positive integer")
    prev = df["close"].shift(1)
    tr = (df["high"] - df["low"]).to_frame("a")
    tr["b"] = (df["high"] - prev).abs()
    tr["c"] = (df["low"] - prev).abs()
    return tr.max(axis=1).ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def enrich(df):
    out = df.copy()
    out["sma20"] = out["close"].rolling(20).mean()
    out["sma50"] = out["close"].rolling(50).mean()
    out["rsi14"] = rsi(out["close"])
    out["atr14"] = atr(out)
    out["vol_avg20"] = out["volume"].rolling(20).mean()
    out["rel_volume"] = out["volume"] / out["vol_avg20"].replace(0, np.nan)
    out["change_pct"] = out["close"].pct_change() * 100
    return out
