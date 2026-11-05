import requests
import pandas as pd

TIMEOUT = 12
INTERVAL_DELTAS = {
    "1m": pd.Timedelta(minutes=1),
    "3m": pd.Timedelta(minutes=3),
    "5m": pd.Timedelta(minutes=5),
    "15m": pd.Timedelta(minutes=15),
    "30m": pd.Timedelta(minutes=30),
    "1h": pd.Timedelta(hours=1),
    "2h": pd.Timedelta(hours=2),
    "4h": pd.Timedelta(hours=4),
    "6h": pd.Timedelta(hours=6),
    "8h": pd.Timedelta(hours=8),
    "12h": pd.Timedelta(hours=12),
    "1d": pd.Timedelta(days=1),
    "3d": pd.Timedelta(days=3),
    "1w": pd.Timedelta(weeks=1),
}


def validate_candle_sequence(df, interval, symbol="unknown"):
    """Reject duplicate, unordered, unsupported, or gapped closed-candle series."""
    if interval not in INTERVAL_DELTAS:
        raise ValueError(f"Unsupported candle interval: {interval}")
    if df.empty:
        raise ValueError(f"Invalid or empty candle set: {symbol} {interval}")
    if df["open_time"].duplicated().any():
        raise ValueError(f"Duplicate candles: {symbol} {interval}")
    if not df["open_time"].is_monotonic_increasing:
        raise ValueError(f"Unordered candles: {symbol} {interval}")
    deltas = df["open_time"].diff().dropna()
    expected = INTERVAL_DELTAS[interval]
    gaps = deltas[deltas != expected]
    if not gaps.empty:
        first_bad_index = gaps.index[0]
        previous_time = df.loc[first_bad_index - 1, "open_time"]
        current_time = df.loc[first_bad_index, "open_time"]
        raise ValueError(
            f"Candle gap or irregular interval: {symbol} {interval}; "
            f"previous={previous_time}; current={current_time}; expected={expected}"
        )
    return df


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
    now = pd.Timestamp.now(tz="UTC")
    df = df[df["close_time"] < now].copy()
    if df.empty or df[["open","high","low","close","volume"]].isna().any().any():
        raise ValueError(f"Invalid or empty candle set: {symbol} {interval}")
    validate_candle_sequence(df, interval, symbol)
    return df.reset_index(drop=True)


def get_24h_tickers(base_url):
    r = requests.get(f"{base_url.rstrip('/')}/api/v3/ticker/24hr", timeout=TIMEOUT)
    r.raise_for_status()
    return {x["symbol"]: x for x in r.json()}


def get_book_ticker_details(base_url, symbol):
    r = requests.get(f"{base_url.rstrip('/')}/api/v3/ticker/bookTicker",
                     params={"symbol": symbol}, timeout=TIMEOUT)
    r.raise_for_status()
    d = r.json()
    bid, ask = float(d["bidPrice"]), float(d["askPrice"])
    if bid <= 0 or ask <= 0 or ask < bid:
        raise ValueError(f"Invalid order book for {symbol}")
    mid = (bid + ask) / 2
    spread = ((ask - bid) / mid * 100) if mid > 0 else 999
    return {
        "bid": bid,
        "ask": ask,
        "spread_pct": spread,
        "collected_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
    }


def get_book_ticker(base_url, symbol):
    """Backward-compatible spread-only helper."""
    return get_book_ticker_details(base_url, symbol)["spread_pct"]
