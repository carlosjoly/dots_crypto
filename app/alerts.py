import json
import os
import re
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests


BRASILIA_TZ = timezone(timedelta(hours=-3))


def emit_alert(cfg, alert):
    root = Path(cfg["_root"])
    path = root / cfg["alerts"]["log_file"]
    path.parent.mkdir(parents=True, exist_ok=True)
    alert["created_at_utc"] = datetime.now(timezone.utc).isoformat()
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(alert, ensure_ascii=False, default=str) + "\n")

    env = cfg["_env"]
    if cfg["alerts"].get("telegram_enabled", True) and env["telegram_bot_token"] and env["telegram_chat_id"]:
        msg = format_message(alert)
        url = f"https://api.telegram.org/bot{env['telegram_bot_token']}/sendMessage"
        response = requests.post(
            url,
            json={
                "chat_id": env["telegram_chat_id"],
                "text": msg,
                "disable_web_page_preview": True,
            },
            timeout=12,
        )
        response.raise_for_status()


def _number(value, decimals=2, signed=False):
    """Format a numeric value using Brazilian separators without changing stored data."""
    if value is None or value == "":
        return "N/D"
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            return "N/D"
        rendered = f"{number:,.{decimals}f}"
        rendered = rendered.replace(",", "_").replace(".", ",").replace("_", ".")
        if signed and number > 0:
            rendered = f"+{rendered}"
        return rendered
    except (InvalidOperation, ValueError, TypeError):
        return str(value)


def _time_brasilia(value):
    if not value:
        return "N/D"
    try:
        raw = str(value).strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        local = parsed.astimezone(BRASILIA_TZ)
        return local.strftime("%d/%m/%Y às %H:%M:%S (UTC-3)")
    except (ValueError, TypeError, OverflowError):
        return str(value)


def _localize_reason(reason):
    """Localize decimal notation inside explanatory text; leave words and values intact."""
    if not reason:
        return "Não informado"

    def replace(match):
        token = match.group(0)
        try:
            value = Decimal(token)
            decimals = len(token.split(".", 1)[1]) if "." in token else 0
            decimals = min(max(decimals, 1), 4)
            return _number(value, decimals)
        except InvalidOperation:
            return token

    return re.sub(r"(?<![A-Za-z0-9])[-+]?\d+\.\d+(?![A-Za-z0-9])", replace, str(reason))


def format_message(alert):
    """Build a readable Telegram alert. Raw numeric values remain unchanged in JSONL."""
    agent = alert.get("agent") or "Agente não identificado"
    symbol = str(alert.get("symbol") or "N/D")
    if symbol.endswith("USDT") and "/" not in symbol:
        symbol = f"{symbol[:-4]}/USDT"

    timeframe = alert.get("timeframe") or "N/D"
    price = _number(alert.get("price"), 2)
    signal = alert.get("signal") or "Não informado"
    reason = _localize_reason(alert.get("reason"))
    change = _number(alert.get("change_pct"), 3, signed=True)
    relative_volume = _number(alert.get("relative_volume"), 2)
    rsi = _number(alert.get("rsi"), 1)
    atr = _number(alert.get("atr"), 2)
    confidence = alert.get("confidence") or "Não calibrada"
    risk = alert.get(
        "risk_note",
        "Sinal preliminar. Verifique custos, liquidez, spread e contexto de mercado.",
    )
    candle_time = _time_brasilia(alert.get("candle_time_utc"))

    return (
        f"DOT CRIPTO | {agent.upper()}\n\n"
        f"Ativo: {symbol}\n"
        f"Período: {timeframe}\n"
        f"Preço de referência: US$ {price}\n\n"
        f"SINAL: {str(signal).upper()}\n\n"
        f"Motivos:\n{reason}\n\n"
        f"Indicadores:\n"
        f"- Variação: {change}%\n"
        f"- Volume relativo: {relative_volume}x\n"
        f"- RSI: {rsi}\n"
        f"- ATR: {atr}\n\n"
        f"CONFIANÇA: {confidence}.\n\n"
        f"RISCO: {risk}\n\n"
        f"Horário da coleta: {candle_time}.\n\n"
        "Alerta exclusivamente informativo. Não constitui recomendação de investimento, "
        "não executa ordens e não garante resultados."
    )
