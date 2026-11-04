"""Download historical Binance Spot candles and run a non-overlapping event study.

This is an event study, not a portfolio simulator: every signal is evaluated
independently. Entry is the next candle open; exits are fixed-horizon closes.
"""
import argparse
import json
import logging
import math
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
    frame = frame.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    if frame[["open", "high", "low", "close", "volume"]].isna().any().any():
        raise ValueError(f"Historical candles contain invalid numeric values: {symbol} {interval}")
    expected = pd.Timedelta(milliseconds=BASE_INTERVAL_MS[interval])
    gaps = frame["open_time"].diff().dropna()
    if not gaps.eq(expected).all():
        missing = int((gaps / expected).round().sub(1).clip(lower=0).sum())
        raise ValueError(f"Historical sample has {missing} missing candles: {symbol} {interval}")
    return frame


def signal_direction(alert):
    signal = str(alert.get("signal", "")).lower()
    if any(term in signal for term in ("baixa", "suporte", "perda de força", "movimento de baixa")):
        return -1
    return 1


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
    records = []
    first_signal_index = 54
    for index in range(first_signal_index, len(enriched) - max(HORIZONS)):
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
                    "direction": "long" if direction > 0 else "short",
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
    grouped = frame.groupby(["symbol", "timeframe", "agent", "horizon_bars", "split"], dropna=False)
    summary = []
    for keys, group in grouped:
        values = group["net_return_pct"].astype(float)
        summary.append({
            "symbol": keys[0], "timeframe": keys[1], "agent": keys[2],
            "horizon_bars": int(keys[3]), "split": keys[4],
            "events": int(len(group)),
            "win_rate_pct": round(float((values > 0).mean() * 100), 3),
            "mean_net_return_pct": round(float(values.mean()), 5),
            "median_net_return_pct": round(float(values.median()), 5),
            "p05_net_return_pct": round(float(values.quantile(0.05)), 5),
            "p95_net_return_pct": round(float(values.quantile(0.95)), 5),
        })
    return summary


def run(days=180, fee_per_side_pct=0.10, slippage_per_side_pct=0.05):
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
        "warning": "Signals are evaluated independently and may overlap. Results are not portfolio returns or calibrated probabilities.",
        "summary": summary,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest_path.write_text(json.dumps(dataset_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Backtest concluído: datasets válidos=%d/%d; eventos=%d; grupos_resumo=%d.",
             sum(x["status"] == "valid" for x in dataset_manifest), len(dataset_manifest),
             len(all_records), len(summary))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--fee-per-side-pct", type=float, default=0.10)
    parser.add_argument("--slippage-per-side-pct", type=float, default=0.05)
    args = parser.parse_args()
    if args.days < 60 or args.days > 365:
        parser.error("--days must be between 60 and 365")
    if args.fee_per_side_pct < 0 or args.slippage_per_side_pct < 0:
        parser.error("cost assumptions cannot be negative")
    raise SystemExit(run(args.days, args.fee_per_side_pct, args.slippage_per_side_pct))
