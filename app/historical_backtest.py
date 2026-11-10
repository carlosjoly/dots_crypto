"""Download historical Binance Spot candles and run a point-in-time event study.

This is an event study, not a portfolio simulator: every signal is evaluated
independently. Entry is the next candle open; exits are fixed-horizon closes.
"""
import argparse
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

from .agents import run_market_agents_enriched
from .config import load_config
from .indicators import enrich

log = logging.getLogger("dot_cripto.backtest")
BASE_INTERVAL_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}
BARS_PER_24H = {"15m": 96, "1h": 24, "4h": 6}
COLUMNS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
           "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"]
HORIZONS = (1, 4, 16, 48)


def download_historical_klines(base_url, symbol, interval, start_ms, end_ms, session=requests):
    if interval not in BASE_INTERVAL_MS:
        raise ValueError(f"Unsupported timeframe: {interval}")
    url = f"{base_url.rstrip('/')}/api/v3/klines"
    cursor = int(start_ms)
    rows = []
    while cursor < int(end_ms):
        response = session.get(
            url,
            params={"symbol": symbol, "interval": interval, "startTime": cursor,
                    "endTime": int(end_ms), "limit": 1000},
            timeout=20,
        )
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break
        rows.extend(batch)
        next_cursor = int(batch[-1][0]) + BASE_INTERVAL_MS[interval]
        if next_cursor <= cursor:
            raise RuntimeError(f"Historical pagination did not advance: {symbol} {interval}")
        cursor = next_cursor
        if len(batch) < 1000:
            break
        time.sleep(0.15)

    if not rows:
        raise ValueError(f"No historical candles returned: {symbol} {interval}")
    frame = pd.DataFrame(rows, columns=COLUMNS)
    for column in ["open", "high", "low", "close", "volume", "quote_volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["open_time"] = pd.to_datetime(frame["open_time"], unit="ms", utc=True)
    frame["close_time"] = pd.to_datetime(frame["close_time"], unit="ms", utc=True)
    if frame["open_time"].duplicated().any():
        raise ValueError(f"Historical sample contains duplicate candles: {symbol} {interval}")
    if not frame["open_time"].is_monotonic_increasing:
        raise ValueError(f"Historical sample is not ordered: {symbol} {interval}")
    frame = frame.reset_index(drop=True)
    frame = frame[frame["close_time"] < pd.Timestamp.now(tz="UTC")].copy().reset_index(drop=True)
    if frame.empty:
        raise ValueError(f"Historical sample contains no fully closed candles: {symbol} {interval}")
    if frame[["open", "high", "low", "close", "volume", "quote_volume"]].isna().any().any():
        raise ValueError(f"Historical candles contain invalid numeric values: {symbol} {interval}")
    if (frame[["open", "high", "low", "close"]] <= 0).any().any() or (frame[["volume", "quote_volume"]] < 0).any().any():
        raise ValueError(f"Historical candles contain out-of-range numeric values: {symbol} {interval}")
    expected = pd.Timedelta(milliseconds=BASE_INTERVAL_MS[interval])
    gaps = frame["open_time"].diff().dropna()
    if not gaps.eq(expected).all():
        missing = int((gaps / expected).round().sub(1).clip(lower=0).sum())
        raise ValueError(f"Historical sample has {missing} missing candles: {symbol} {interval}")
    return frame


def signal_direction(alert):
    """Return a direction only for explicitly recognized signal semantics."""
    signal = str(alert.get("signal", "")).strip().casefold()
    directions = {
        "movimento de alta": 1,
        "movimento de baixa": -1,
        "possível estabilização após sobrevenda": 1,
        "possível perda de força após sobrecompra": -1,
        "possível rompimento de resistência": 1,
        "possível rompimento de suporte": -1,
        "configuração potencial de alta": 1,
        "configuração potencial de baixa": -1,
    }
    try:
        return directions[signal]
    except KeyError as exc:
        raise ValueError(f"Unrecognized signal direction; refusing to guess: {signal!r}") from exc


def net_event_return(entry_price, exit_price, direction, fee_per_side_pct=0.10,
                     slippage_per_side_pct=0.05):
    if entry_price <= 0 or exit_price <= 0 or direction not in (-1, 1):
        raise ValueError("Invalid event-study price or direction")
    gross = direction * (exit_price / entry_price - 1.0) * 100.0
    round_trip_cost = 2.0 * (float(fee_per_side_pct) + float(slippage_per_side_pct))
    return gross - round_trip_cost


def _split_for_index(index, count):
    train_end = int(count * 0.60)
    validation_end = int(count * 0.80)
    if index < train_end:
        return "train", train_end
    if index < validation_end:
        return "validation", validation_end
    return "test", count


def event_study(symbol, timeframe, candles, cfg, fee_per_side_pct=0.10,
                slippage_per_side_pct=0.05):
    enriched = enrich(candles)
    if "quote_volume" not in enriched.columns:
        raise ValueError("Historical candles lack quote_volume; live liquidity filter cannot be approximated.")
    bars_24h = BARS_PER_24H[timeframe]
    enriched["quote_volume_24h_usdt"] = enriched["quote_volume"].rolling(bars_24h).sum()
    minimum_quote_volume = float(cfg["exchange"]["min_quote_volume_24h_usdt"])
    records = []
    first_signal_index = max(54, bars_24h - 1)
    for index in range(first_signal_index, len(enriched) - max(HORIZONS)):
        if float(enriched.iloc[index]["quote_volume_24h_usdt"]) < minimum_quote_volume:
            continue
        split, split_end = _split_for_index(index, len(enriched))
        alerts = run_market_agents_enriched(
            symbol, timeframe, enriched.iloc[:index + 1], cfg
        )
        for alert in alerts:
            direction = signal_direction(alert)
            entry_index = index + 1
            for horizon in HORIZONS:
                exit_index = entry_index + horizon - 1
                if exit_index >= split_end or exit_index >= len(enriched):
                    continue
                entry = float(enriched.iloc[entry_index]["open"])
                exit_price = float(enriched.iloc[exit_index]["close"])
                net = net_event_return(
                    entry, exit_price, direction, fee_per_side_pct, slippage_per_side_pct
                )
                records.append({
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "agent": alert["agent"],
                    "signal": alert["signal"],
                    "signal_time_utc": str(enriched.iloc[index]["close_time"]),
                    "entry_time_utc": str(enriched.iloc[entry_index]["open_time"]),
                    "exit_time_utc": str(enriched.iloc[exit_index]["close_time"]),
                    "direction": "bullish" if direction > 0 else "bearish",
                    "horizon_bars": horizon,
                    "entry_price": entry,
                    "exit_price": exit_price,
                    "gross_return_pct": direction * (exit_price / entry - 1.0) * 100.0,
                    "estimated_round_trip_cost_pct": 2.0 * (fee_per_side_pct + slippage_per_side_pct),
                    "net_return_pct": net,
                    "split": split,
                })
    return records


def summarize_events(records):
    frame = pd.DataFrame(records)
    if frame.empty:
        return []
    grouped = frame.groupby(["symbol", "timeframe", "agent", "direction", "horizon_bars", "split"], dropna=False)
    summary = []
    for keys, group in grouped:
        values = group["net_return_pct"].astype(float)
        summary.append({
            "symbol": keys[0], "timeframe": keys[1], "agent": keys[2], "direction": keys[3],
            "market_mode": "spot_long" if keys[3] == "bullish" else "short_research",
            "horizon_bars": int(keys[4]), "split": keys[5],
            "events": int(len(group)),
            "win_rate_pct": round(float((values > 0).mean() * 100), 3),
            "mean_net_return_pct": round(float(values.mean()), 5),
            "median_net_return_pct": round(float(values.median()), 5),
            "p05_net_return_pct": round(float(values.quantile(0.05)), 5),
            "p95_net_return_pct": round(float(values.quantile(0.95)), 5),
        })
    return summary


def run(days=365, fee_per_side_pct=0.10, slippage_per_side_pct=0.05):
    cfg = load_config()
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=int(days))
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)
    base = cfg["exchange"]["base_url"]
    data_dir = Path(cfg["_root"]) / "data" / "historical"
    output_dir = Path(cfg["_root"]) / "data" / "backtest"
    data_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    all_records = []
    dataset_manifest = []
    for symbol in cfg["exchange"]["symbols"]:
        for timeframe in cfg["exchange"]["timeframes"]:
            try:
                candles = download_historical_klines(
                    base, symbol, timeframe, start_ms, end_ms
                )
                path = data_dir / f"{symbol}_{timeframe}.csv"
                candles.to_csv(path, index=False)
                records = event_study(
                    symbol, timeframe, candles, cfg,
                    fee_per_side_pct, slippage_per_side_pct,
                )
                all_records.extend(records)
                dataset_manifest.append({
                    "symbol": symbol, "timeframe": timeframe, "status": "valid",
                    "candles": len(candles), "first_open_utc": str(candles.iloc[0]["open_time"]),
                    "last_close_utc": str(candles.iloc[-1]["close_time"]),
                    "events": len(records), "file": str(path.relative_to(cfg["_root"])),
                })
                log.info("Backtest dataset validado: %s %s; candles=%d; eventos=%d.",
                         symbol, timeframe, len(candles), len(records))
            except Exception as exc:
                dataset_manifest.append({
                    "symbol": symbol, "timeframe": timeframe, "status": "invalid",
                    "reason": f"{type(exc).__name__}: {str(exc)[:200]}",
                })
                log.exception("Backtest dataset rejeitado: %s %s", symbol, timeframe)

    events_path = output_dir / "events.csv"
    summary_path = output_dir / "summary.json"
    manifest_path = output_dir / "dataset_manifest.json"
    pd.DataFrame(all_records).to_csv(events_path, index=False)
    summary = summarize_events(all_records)
    summary_path.write_text(json.dumps({
        "method": "event_study_not_portfolio_backtest",
        "days_requested": int(days),
        "fee_per_side_pct_assumption": fee_per_side_pct,
        "slippage_per_side_pct_assumption": slippage_per_side_pct,
        "round_trip_cost_pct_assumption": 2 * (fee_per_side_pct + slippage_per_side_pct),
        "horizons_bars": list(HORIZONS),
        "splits": {"train": "first 60%", "validation": "next 20%", "test": "last 20%"},
        "historical_market_filter": "24h quote volume is approximated from candle quote_volume; historical bid/ask spread is unavailable and therefore not reconstructed. Results are not an exact replay of live symbol eligibility.",
        "warning": "Signals are evaluated independently and may overlap. Bullish outcomes are Spot-long directional studies; bearish outcomes are inverse directional research, not short-selling trades and not executable on Binance Spot. Results are not portfolio returns or calibrated probabilities.",
        "summary": summary,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest_path.write_text(json.dumps(dataset_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Backtest concluído: datasets válidos=%d/%d; eventos=%d; grupos_resumo=%d.",
             sum(x["status"] == "valid" for x in dataset_manifest), len(dataset_manifest),
             len(all_records), len(summary))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--fee-per-side-pct", type=float, default=0.10)
    parser.add_argument("--slippage-per-side-pct", type=float, default=0.05)
    args = parser.parse_args()
    if args.days < 60 or args.days > 365:
        parser.error("--days must be between 60 and 365")
    if args.fee_per_side_pct < 0 or args.slippage_per_side_pct < 0:
        parser.error("cost assumptions cannot be negative")
    raise SystemExit(run(args.days, args.fee_per_side_pct, args.slippage_per_side_pct))
