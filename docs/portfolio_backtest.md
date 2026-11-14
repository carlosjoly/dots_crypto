# Portfolio backtest (research mode)

The historical workflow now runs two distinct analyses:

1. The existing event study remains available in \`events.csv\` and \`summary.json\`.
2. \`app.portfolio_backtest\` simulates one portfolio per agent and timeframe using the same historical candles.

## Execution assumptions

- Signals are formed only after a candle has closed; entries occur at the next candle open.
- Stops are set at 1.5 ATR and targets at 1.5R by default.
- If stop and target are both touched inside one candle, the stop is assumed to execute first.
- Gaps through stop/target use the candle open as the execution reference.
- Fees, slippage and a conservative spread proxy are charged on both entry and exit; defaults are 0.10%, 0.05% and 0.125% per side (0.55% estimated round trip). Historical bid/ask is unavailable in OHLCV data, so the spread is an explicit stress assumption, not an observed historical quote.
- Risk budget is 1% of current equity per position, with estimated fees, spread and slippage included in stop-risk sizing; aggregate exposure is capped at 80%, with at most five simultaneous positions.
- Positions are liquidated at the end of each chronological split. No position crosses from train to validation or test.
- Buy & Hold is an equal-weight benchmark over the same symbols and exact split, including the same fee and slippage assumptions.

## Outputs

- \`portfolio_report.json\`: configuration, data validity, per-agent/timeframe/split metrics and the test gate.
- \`portfolio_summary.csv\`: net return, max drawdown, trade count, win rate, profit factor, Sharpe, Buy & Hold and excess return.
- \`portfolio_trades.csv\`: simulated entries/exits, stop/target, costs and realized P&L.
- \`portfolio_equity_curves.csv\`: marked-to-market equity series.
- \`buy_and_hold_by_symbol.csv\`: benchmark return per symbol.

## Acceptance gate and interpretation

The workflow fails closed unless every configured symbol/timeframe dataset is valid and contiguous. Every Spot-long strategy/timeframe test row must contain at least 50 trades, positive net return, positive excess versus equal-weight Buy & Hold, profit factor above 1 and maximum drawdown no worse than -20% in the final 20% test segment. Training or validation returns cannot satisfy this gate.

A positive test return is a minimum engineering gate, not proof of durable profitability. Compare the strategy with Buy & Hold, inspect drawdown and trade count, then run paper trading. Historical candle quote volume is only an approximation of the live liquidity filter. Internal missing candles are recovered only from exact public kline records; unresolved gaps invalidate the dataset, and stale files from prior runs are removed. Historical bid/ask spread is unavailable and is modelled with the explicit proxy above.

Bearish signals are simulated as synthetic short positions to test direction. They are research-only and are not executable on Binance Spot. The project remains alert-only and never submits orders.
