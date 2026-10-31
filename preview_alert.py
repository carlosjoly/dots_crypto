"""Print a deterministic Telegram alert preview without sending messages."""
from app.alerts import format_message


sample_alert = {
    "agent": "Bot E — Swing trade",
    "symbol": "ETHUSDT",
    "timeframe": "15m",
    "price": 2251.94,
    "signal": "potencial de alta",
    "reason": (
        "Tendência SMA20>SMA50; RSI=56.3; volume relativo 1.19; "
        "Invalidação indicativa: 2246.9495; objetivo ilustrativo: 2259.4258."
    ),
    "change_pct": 0.047,
    "relative_volume": 1.19,
    "rsi": 56.3,
    "atr": 3.32701348,
    "confidence": "não calibrada",
    "risk_note": (
        "Estratégia experimental, ainda sem validação por backtest. "
        "Spread, custos, liquidez e contexto de mercado precisam ser verificados."
    ),
    "candle_time_utc": "2026-10-31 16:14:59+00:00",
}

message = format_message(sample_alert)
print("========== PRÉVIA DA MENSAGEM TELEGRAM ==========")
print(message)
print("=================================================")
print(f"Quebras reais de linha: {message.count(chr(10))}")
print(f"Sequências literais \\n: {message.count(chr(92) + 'n')}")
