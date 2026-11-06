"""Portfolio backtest for the live market agents.

Signals are calculated from closed candles and filled at the next candle open.
Stops/targets are checked intrabar, with stop-first handling when both are hit.
Long and short are simulated for research; short trades are hypothetical and
are not executable on Binance Spot.
"""
import argparse
import json
import logging
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from .agents import run_market_agents_enriched
from .config import load_config
from .historical_backtest import BARS_PER_24H, signal_direction
from .indicators import enrich

log = logging.getLogger("dot_cripto.portfolio_backtest")
AGENTS = (
    "Bot A — Altas expressivas",
    "Bot B — Quedas expressivas",
    "Bot C — Reversões",
    "Bot D — Rompimentos",
    "Bot E — Swing trade",
)
SPLITS = ("train", "validation", "test")
BARS_PER_YEAR = {"15m": 35064, "1h": 8766, "4h": 2191}


def chronological_bounds(count):
    train_end = int(count * 0.60)
    validation_end = int(count * 0.80)
    return {"train": (0, train_end), "validation": (train_end, validation_end),
            "test": (validation_end, count)}


def _exit_trade(symbol, position, bar, reason, fee_rate, slip_rate):
    direction = position["direction"]
    reference = float(bar["close"])
    if reason == "stop":
        level = position["stop_price"]
        if (direction == 1 and float(bar["open"]) <= level) or (direction == -1 and float(bar["open"]) >= level):
            reference = float(bar["open"])
        else:
            reference = level
    elif reason == "target":
        level = position["target_price"]
        if (direction == 1 and float(bar["open"]) >= level) or (direction == -1 and float(bar["open"]) <= level):
            reference = float(bar["open"])
        else:
            reference = level
    exit_price = reference * (1 - slip_rate if direction == 1 else 1 + slip_rate)
    exit_fee = position["quantity"] * exit_price * fee_rate
    gross = direction * position["quantity"] * (exit_price - position["entry_price"])
    trade = {
        "symbol": symbol, "direction": "long" if direction == 1 else "short",
        "entry_time_utc": position["entry_time_utc"],
        "exit_time_utc": str(bar.get("close_time", bar.get("open_time", ""))),
        "entry_price": position["entry_price"], "exit_price": exit_price,
        "quantity": position["quantity"], "stop_price": position["stop_price"],
        "target_price": position["target_price"], "gross_pnl": gross,
        "fees": position["entry_fee"] + exit_fee,
        "net_pnl": gross - position["entry_fee"] - exit_fee,
        "exit_reason": reason, "bars_held": position["bars_held"],
    }
    # Entry fee was deducted when the position opened.
    return trade, gross - exit_fee


def _mark_equity(cash, positions, marks):
    return cash + sum(
        p["direction"] * p["quantity"] * (marks.get(s, p["entry_price"]) - p["entry_price"])
        for s, p in positions.items()
    )


def simulate_portfolio(frames, events, starts, ends, initial_cash=10000.0,
                       fee_per_side_pct=0.10, slippage_per_side_pct=0.05,
                       risk_fraction=0.01, max_exposure_fraction=0.80,
                       max_positions=5, stop_atr_multiple=1.5, reward_risk=1.5,
                       timeframe="15m"):
    """Run one agent's multi-symbol portfolio; concurrent positions are allowed."""
    if initial_cash <= 0 or not 0 < risk_fraction <= 0.05:
        raise ValueError("initial_cash must be positive and risk_fraction must be in (0, 0.05].")
    if not 0 < max_exposure_fraction <= 1 or max_positions < 1:
        raise ValueError("Invalid exposure or max_positions.")
    if stop_atr_multiple <= 0 or reward_risk <= 0:
        raise ValueError("Stop and reward/risk multiples must be positive.")
    fee, slip = fee_per_side_pct / 100.0, slippage_per_side_pct / 100.0
    cash, positions, marks = float(initial_cash), {}, {}
    trades, curve = [], []
    bars_at_time, events_at_time = defaultdict(dict), defaultdict(list)

    for symbol, frame in frames.items():
        for i in range(int(starts[symbol]), int(ends[symbol])):
            ts = pd.Timestamp(frame.iloc[i]["open_time"])
            bars_at_time[ts][symbol] = (i, frame.iloc[i])
    for event in events:
        symbol, i = event["symbol"], int(event["entry_index"])
        if symbol in frames and int(starts[symbol]) <= i < int(ends[symbol]):
            ts = pd.Timestamp(frames[symbol].iloc[i]["open_time"])
            events_at_time[ts].append(event)

    for ts in sorted(bars_at_time):
        bars = bars_at_time[ts]

        # Existing positions are exited before new orders are considered.
        for symbol in list(positions):
            if symbol not in bars:
                continue
            _, bar = bars[symbol]
            p = positions[symbol]
            p["bars_held"] += 1
            hi, lo, op, d = float(bar["high"]), float(bar["low"]), float(bar["open"]), p["direction"]
            stop_hit = lo <= p["stop_price"] if d == 1 else hi >= p["stop_price"]
            target_hit = hi >= p["target_price"] if d == 1 else lo <= p["target_price"]
            reason = None
            if (d == 1 and op <= p["stop_price"]) or (d == -1 and op >= p["stop_price"]):
                reason = "stop"
            elif (d == 1 and op >= p["target_price"]) or (d == -1 and op <= p["target_price"]):
                reason = "target"
            elif stop_hit:
                reason = "stop"  # Conservative resolution if both levels trade in one candle.
            elif target_hit:
                reason = "target"
            if reason:
                trade, delta = _exit_trade(symbol, p, bar, reason, fee, slip)
                cash += delta
                trades.append(trade)
                del positions[symbol]

        # Fill signals from the previous closed candle at this candle's open.
        for event in sorted(events_at_time.get(ts, []), key=lambda x: x["symbol"]):
            symbol = event["symbol"]
            if symbol in positions or len(positions) >= max_positions:
                continue
            bar = bars[symbol][1]
            direction, atr_value = int(event["direction"]), float(event["atr"])
            opening = float(bar["open"])
            distance = atr_value * stop_atr_multiple
            if direction not in (-1, 1) or not math.isfinite(distance) or distance <= 0 or opening <= 0:
                continue
            entry = opening * (1 + slip if direction == 1 else 1 - slip)
            stop = entry - direction * distance
            target = entry + direction * distance * reward_risk
            equity = max(0.0, _mark_equity(cash, positions, marks))
            exposure = sum(p["quantity"] * marks.get(s, p["entry_price"]) for s, p in positions.items())
            remaining = max(0.0, equity * max_exposure_fraction - exposure)
            per_position_cap = equity * max_exposure_fraction / max_positions
            quantity = min(equity * risk_fraction / distance,
                           per_position_cap / entry, remaining / entry)
            if quantity <= 0:
                continue
            entry_fee = quantity * entry * fee
            cash -= entry_fee
            positions[symbol] = {
                "direction": direction, "entry_price": entry, "quantity": quantity,
                "entry_fee": entry_fee, "stop_price": stop, "target_price": target,
                "entry_time_utc": str(bar["open_time"]), "entry_index": bars[symbol][0],
                "bars_held": 0,
            }

        # A newly opened position can hit a stop/target in its entry candle.
        for symbol in list(positions):
            if symbol not in bars or positions[symbol]["entry_index"] != bars[symbol][0]:
                continue
            p, bar = positions[symbol], bars[symbol][1]
            hi, lo, op, d = float(bar["high"]), float(bar["low"]), float(bar["open"]), positions[symbol]["direction"]
            stop_hit = lo <= p["stop_price"] if d == 1 else hi >= p["stop_price"]
            target_hit = hi >= p["target_price"] if d == 1 else lo <= p["target_price"]
            reason = None
            if (d == 1 and op <= p["stop_price"]) or (d == -1 and op >= p["stop_price"]):
                reason = "stop"
            elif (d == 1 and op >= p["target_price"]) or (d == -1 and op <= p["target_price"]):
                reason = "target"
            elif stop_hit:
                reason = "stop"
            elif target_hit:
                reason = "target"
            if reason:
                trade, delta = _exit_trade(symbol, p, bar, reason, fee, slip)
                cash += delta
                trades.append(trade)
                del positions[symbol]

        for symbol, (_, bar) in bars.items():
            marks[symbol] = float(bar["close"])
        curve.append({"time_utc": str(ts), "equity": _mark_equity(cash, positions, marks)})

    # Do not carry open positions across train/validation/test boundaries.
    for symbol in list(positions):
        last = frames[symbol].iloc[int(ends[symbol]) - 1]
        trade, delta = _exit_trade(symbol, positions[symbol], last, "end_of_split", fee, slip)
        cash += delta
        trades.append(trade)
        del positions[symbol]
    if curve:
        curve[-1]["equity"] = cash

    values = np.asarray([x["equity"] for x in curve], dtype=float)
    peak = np.maximum.accumulate(values) if len(values) else np.asarray([initial_cash])
    max_dd = float(np.min((values / peak - 1) * 100)) if len(values) else 0.0
    pnl = [float(t["net_pnl"]) for t in trades]
    gains, losses = sum(x for x in pnl if x > 0), abs(sum(x for x in pnl if x < 0))
    returns = pd.Series(values).pct_change().dropna() if len(values) > 1 else pd.Series(dtype=float)
    sharpe = 0.0 if returns.empty or returns.std(ddof=1) == 0 else float(returns.mean() / returns.std(ddof=1) * math.sqrt(BARS_PER_YEAR[timeframe]))
    metrics = {
        "initial_equity": round(initial_cash, 2), "final_equity": round(cash, 2),
        "total_return_pct": round((cash / initial_cash - 1) * 100, 5),
        "max_drawdown_pct": round(max_dd, 5), "trades": len(trades),
        "win_rate_pct": round(sum(x > 0 for x in pnl) / len(pnl) * 100, 3) if pnl else 0.0,
        "profit_factor": round(gains / losses, 4) if losses else (None if gains == 0 else "infinity"),
        "sharpe_ratio": round(sharpe, 4),
    }
    return {"metrics": metrics, "trades": trades, "equity_curve": curve}


def buy_and_hold_benchmark(frames, starts, ends, initial_cash, fee_per_side_pct, slippage_per_side_pct):
    fee, slip = fee_per_side_pct / 100.0, slippage_per_side_pct / 100.0
    symbols = [s for s in frames if int(ends[s]) > int(starts[s])]
    if not symbols:
        return {"final_equity": initial_cash, "total_return_pct": 0.0, "by_symbol": {}}
    allocation, final_value, by_symbol = initial_cash / len(symbols), 0.0, {}
    for symbol in symbols:
        first, last = frames[symbol].iloc[int(starts[symbol])], frames[symbol].iloc[int(ends[symbol]) - 1]
        entry = float(first["open"]) * (1 + slip)
        exit_price = float(last["close"]) * (1 - slip)
        quantity = allocation / (entry * (1 + fee))
        value = quantity * exit_price * (1 - fee)
        final_value += value
        by_symbol[symbol] = {"return_pct": round((value / allocation - 1) * 100, 5)}
    return {"final_equity": round(final_value, 2),
            "total_return_pct": round((final_value / initial_cash - 1) * 100, 5),
            "by_symbol": by_symbol}


def _make_events(symbol, timeframe, raw, cfg):
    data = enrich(raw)
    cooldown_minutes = float(cfg.get("alerts", {}).get("cooldown_minutes", 0))
    if cooldown_minutes < 0:
        raise ValueError("alerts.cooldown_minutes cannot be negative")
    last_signal_at = {}
    bars_24h = BARS_PER_24H[timeframe]
    data["quote_volume_24h"] = data["quote_volume"].rolling(bars_24h).sum()
    minimum = float(cfg["exchange"]["min_quote_volume_24h_usdt"])
    events = defaultdict(list)
    for i in range(max(54, bars_24h - 1), len(data) - 1):
        if not np.isfinite(data.iloc[i]["quote_volume_24h"]) or data.iloc[i]["quote_volume_24h"] < minimum:
            continue
        # Agents inspect only the current candle, its previous candle and a bounded
        # breakout lookback. A 55-candle tail is sufficient and avoids O(n^2) slicing.
        window = data.iloc[max(0, i - 54):i + 1]
        for alert in run_market_agents_enriched(symbol, timeframe, window, cfg):
            direction = signal_direction(alert)
            signal_time = pd.Timestamp(data.iloc[i]["open_time"])
            cooldown_key = (alert["agent"], alert["signal"])
            previous_time = last_signal_at.get(cooldown_key)
            if (cooldown_minutes > 0 and previous_time is not None
                    and signal_time < previous_time + pd.Timedelta(minutes=cooldown_minutes)):
                continue
            atr_value = float(data.iloc[i]["atr14"])
            if np.isfinite(atr_value) and atr_value > 0:
                events[alert["agent"]].append({
                    "symbol": symbol, "signal_index": i, "entry_index": i + 1,
                    "direction": direction, "atr": atr_value, "signal": alert["signal"],
                })
                last_signal_at[cooldown_key] = signal_time
    return data, events


SPOT_LONG_GATE_AGENTS = {
    "Bot A — Altas expressivas",
    "Bot C — Reversões",
    "Bot D — Rompimentos",
    "Bot E — Swing trade",
}


def filter_events_by_market_mode(events, market_mode):
    """Keep executable Spot-long events separate from hypothetical short research."""
    directions = {"spot_long": 1, "short_research": -1}
    if market_mode not in directions:
        raise ValueError(f"Unsupported market mode: {market_mode}")
    direction = directions[market_mode]
    return [event for event in events if int(event["direction"]) == direction]


def failed_spot_long_test_rows(summaries):
    """The positive out-of-sample gate applies only to Spot-long candidates."""
    rows = [
        row for row in summaries
        if row["split"] == "test"
        and row["market_mode"] == "spot_long"
        and row["agent"] in SPOT_LONG_GATE_AGENTS
    ]
    return [
        row for row in rows
        if int(row["trades"]) == 0 or float(row["total_return_pct"]) <= 0
    ]


def run(days=180, fee_per_side_pct=0.10, slippage_per_side_pct=0.05,
        initial_cash=10000.0, risk_fraction=0.01, max_exposure_fraction=0.80,
        max_positions=5, stop_atr_multiple=1.5, reward_risk=1.5,
        require_positive_test=True):
    cfg = load_config()
    root = Path(cfg["_root"])
    historical_dir, output_dir = root / "data" / "historical", root / "data" / "backtest"
    output_dir.mkdir(parents=True, exist_ok=True)
    summaries, all_trades, all_curves, benchmarks, manifest = [], [], [], [], []
    market_modes = ("spot_long", "short_research")

    for timeframe in cfg["exchange"]["timeframes"]:
        frames, events_by_agent = {}, {agent: [] for agent in AGENTS}
        for symbol in cfg["exchange"]["symbols"]:
            path = historical_dir / f"{symbol}_{timeframe}.csv"
            if not path.exists():
                manifest.append({"symbol": symbol, "timeframe": timeframe, "status": "missing"})
                continue
            frame = pd.read_csv(path)
            frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
            frame["close_time"] = pd.to_datetime(frame["close_time"], utc=True)
            for col in ("open", "high", "low", "close", "volume", "quote_volume"):
                frame[col] = pd.to_numeric(frame[col], errors="raise")
            if frame["open_time"].duplicated().any() or not frame["open_time"].is_monotonic_increasing:
                raise ValueError(f"Invalid candle ordering: {symbol} {timeframe}")
            data, events = _make_events(symbol, timeframe, frame, cfg)
            frames[symbol] = data.reset_index(drop=True)
            for agent, rows in events.items():
                events_by_agent[agent].extend(rows)
            manifest.append({"symbol": symbol, "timeframe": timeframe, "status": "valid", "candles": len(data)})
        if not frames:
            continue

        bounds = {symbol: chronological_bounds(len(frame)) for symbol, frame in frames.items()}
        for market_mode in market_modes:
            for agent in AGENTS:
                mode_events = filter_events_by_market_mode(events_by_agent[agent], market_mode)
                for split in SPLITS:
                    starts = {symbol: bounds[symbol][split][0] for symbol in frames}
                    ends = {symbol: bounds[symbol][split][1] for symbol in frames}
                    selected = [
                        event for event in mode_events
                        if event["signal_index"] >= starts[event["symbol"]] - 1
                        and event["entry_index"] < ends[event["symbol"]]
                    ]
                    sim = simulate_portfolio(
                        frames, selected, starts, ends, initial_cash, fee_per_side_pct,
                        slippage_per_side_pct, risk_fraction, max_exposure_fraction,
                        max_positions, stop_atr_multiple, reward_risk, timeframe,
                    )
                    if market_mode == "spot_long":
                        bh = buy_and_hold_benchmark(
                            frames, starts, ends, initial_cash,
                            fee_per_side_pct, slippage_per_side_pct,
                        )
                        benchmark_name = "equal_weight_buy_and_hold"
                        buy_hold_return = bh["total_return_pct"]
                    else:
                        # A long-only Buy & Hold return is not meaningful for hypothetical shorts.
                        bh = {"final_equity": initial_cash, "total_return_pct": 0.0, "by_symbol": {}}
                        benchmark_name = "cash_zero_return"
                        buy_hold_return = None
                    row = dict(sim["metrics"])
                    row.update({
                        "market_mode": market_mode, "agent": agent, "timeframe": timeframe,
                        "split": split, "benchmark_name": benchmark_name,
                        "benchmark_return_pct": bh["total_return_pct"],
                        "excess_vs_benchmark_pct": round(row["total_return_pct"] - bh["total_return_pct"], 5),
                        "beats_benchmark": row["total_return_pct"] > bh["total_return_pct"],
                        "buy_hold_return_pct": buy_hold_return,
                        "excess_vs_buy_hold_pct": (
                            round(row["total_return_pct"] - buy_hold_return, 5)
                            if buy_hold_return is not None else None
                        ),
                        "beats_buy_hold": (
                            row["total_return_pct"] > buy_hold_return
                            if buy_hold_return is not None else None
                        ),
                        "buy_hold_final_equity": bh["final_equity"] if market_mode == "spot_long" else None,
                    })
                    summaries.append(row)
                    all_trades.extend({
                        "market_mode": market_mode, "agent": agent,
                        "timeframe": timeframe, "split": split, **trade,
                    } for trade in sim["trades"])
                    all_curves.extend({
                        "market_mode": market_mode, "agent": agent,
                        "timeframe": timeframe, "split": split, **point,
                    } for point in sim["equity_curve"])
                    if market_mode == "spot_long":
                        benchmarks.extend({
                            "market_mode": market_mode, "agent": agent,
                            "timeframe": timeframe, "split": split,
                            "symbol": symbol, **values,
                        } for symbol, values in bh["by_symbol"].items())

    summary_frame = pd.DataFrame(summaries)
    summary_frame.to_csv(output_dir / "portfolio_summary.csv", index=False)
    summary_frame[summary_frame["market_mode"] == "spot_long"].to_csv(
        output_dir / "portfolio_summary_spot_long.csv", index=False
    )
    summary_frame[summary_frame["market_mode"] == "short_research"].to_csv(
        output_dir / "portfolio_summary_short_research.csv", index=False
    )
    pd.DataFrame(all_trades).to_csv(output_dir / "portfolio_trades.csv", index=False)
    pd.DataFrame(all_curves).to_csv(output_dir / "portfolio_equity_curves.csv", index=False)
    pd.DataFrame(benchmarks).to_csv(output_dir / "buy_and_hold_by_symbol.csv", index=False)

    test_rows = [
        row for row in summaries
        if row["split"] == "test" and row["market_mode"] == "spot_long"
        and row["agent"] in SPOT_LONG_GATE_AGENTS
    ]
    failed = failed_spot_long_test_rows(summaries)
    gate_passed = bool(test_rows) and not failed
    report = {
        "method": "separate_spot_long_and_synthetic_short_portfolios",
        "days_requested": days,
        "market_modes": {
            "spot_long": "Only bullish signals; the executable research universe is Binance Spot long-only.",
            "short_research": "Only bearish signals; hypothetical inverse exposure, not executable on Binance Spot and excluded from the profitability gate.",
        },
        "execution": {
            "entry": "next candle open after closed signal",
            "intrabar": "stop first if stop and target both hit",
            "fees_and_slippage": "charged on entry and exit",
            "positions": "simultaneous across symbols within each agent and market mode",
            "split_liquidation": "open positions closed at final candle of each split",
        },
        "risk": {
            "initial_cash": initial_cash, "risk_per_trade_pct": risk_fraction * 100,
            "max_total_exposure_pct": max_exposure_fraction * 100,
            "max_positions": max_positions, "stop_atr_multiple": stop_atr_multiple,
            "reward_risk": reward_risk,
        },
        "splits": {"train": "first 60%", "validation": "next 20%", "test": "last 20%"},
        "benchmark": {
            "spot_long": "equal-weight Spot Buy & Hold on identical symbols and split, net of fee and slippage",
            "short_research": "0% cash reference; long-only Buy & Hold is not compared with hypothetical shorts",
        },
        "test_gate": {
            "required": require_positive_test,
            "scope": "spot_long only; Bots A, C, D and E. Bot B remains a downside/risk alert, not a Spot-long candidate.",
            "passed": gate_passed,
            "evaluated_combinations": len(test_rows),
            "failed": [
                {"agent": row["agent"], "timeframe": row["timeframe"],
                 "trades": row["trades"], "net_return_pct": row["total_return_pct"]}
                for row in failed
            ],
        },
        "dataset_manifest": manifest,
        "summary": summaries,
    }
    (output_dir / "portfolio_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    log.info(
        "Portfolio backtest: %d combinações; %d Spot-long no teste; %d reprovadas no gate.",
        len(summaries), len(test_rows), len(failed),
    )
    return 1 if require_positive_test and (not test_rows or failed) else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest de carteira com posições simultâneas.")
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--fee-per-side-pct", type=float, default=0.10)
    parser.add_argument("--slippage-per-side-pct", type=float, default=0.05)
    parser.add_argument("--initial-cash", type=float, default=10000.0)
    parser.add_argument("--risk-fraction", type=float, default=0.01)
    parser.add_argument("--max-exposure-fraction", type=float, default=0.80)
    parser.add_argument("--max-positions", type=int, default=5)
    parser.add_argument("--stop-atr-multiple", type=float, default=1.5)
    parser.add_argument("--reward-risk", type=float, default=1.5)
    parser.add_argument("--allow-non-positive-test", action="store_true")
    args = parser.parse_args()
    if not 60 <= args.days <= 365 or min(args.fee_per_side_pct, args.slippage_per_side_pct) < 0:
        parser.error("Invalid days or transaction-cost assumptions.")
    raise SystemExit(run(args.days, args.fee_per_side_pct, args.slippage_per_side_pct,
                         args.initial_cash, args.risk_fraction, args.max_exposure_fraction,
                         args.max_positions, args.stop_atr_multiple, args.reward_risk,
                         not args.allow_non_positive_test))
