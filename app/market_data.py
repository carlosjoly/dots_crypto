import requests
import pandas as pd

TIMEOUT = 12

def get_klines(base_url, symbol, interval, limit=120):
    url = f"{base_url.rstrip('/')}/api/v3/klines"
    r = requests.get(url, params={"symbol": symbol, "interval": interval, "limit": limit},
                     timeout=TIMEOUT)
    r.raise_for_status()
    rows = r.json()
    cols = ["open_time","open","high","low","close","volume","close_time",
            "quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"]
    df = pd.DataFrame(rows, columns=cols)
    for c in ["open","high","low","close","volume","quote_volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    # Remove the currently forming candle to avoid signals from incomplete data.
    now = pd.Timestamp.now(tz="UTC")
    df = df[df["close_time"] < now].copy()
    if df.empty or df[["open","high","low","close","volume"]].isna().any().any():
        raise ValueError(f"Invalid or empty candle set: {symbol} {interval}")
    if df["open_time"].duplicated().any():
        raise ValueError(f"Duplicate candles: {symbol} {interval}")
    return df.reset_index(drop=True)

def get_24h_tickers(base_url):
    r = requests.get(f"{base_url.rstrip('/')}/api/v3/ticker/24hr", timeout=TIMEOUT)
    r.raise_for_status()
    return {x["symbol"]: x for x in r.json()}

def get_book_ticker(base_url, symbol):
    r = requests.get(f"{base_url.rstrip('/')}/api/v3/ticker/bookTicker",
                     params={"symbol": symbol}, timeout=TIMEOUT)
    r.raise_for_status()
    d = r.json()
    bid, ask = float(d["bidPrice"]), float(d["askPrice"])
    mid = (bid + ask) / 2
    return ((ask - bid) / mid * 100) if mid > 0 else 999
