import numpy as np

def rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def atr(df, period=14):
    prev = df["close"].shift(1)
    tr = (df["high"] - df["low"]).to_frame("a")
    tr["b"] = (df["high"] - prev).abs()
    tr["c"] = (df["low"] - prev).abs()
    return tr.max(axis=1).ewm(alpha=1/period, min_periods=period, adjust=False).mean()

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
