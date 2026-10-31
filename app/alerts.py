import json
import re
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests


BRASILIA_TZ = timezone(timedelta(hours=-3))

TIMEFRAME_LABELS = {
    "1m": "1 minuto",
    "3m": "3 minutos",
    "5m": "5 minutos",
    "15m": "15 minutos",
    "30m": "30 minutos",
    "1h": "1 hora",
    "2h": "2 horas",
    "4h": "4 horas",
    "6h": "6 horas",
    "12h": "12 horas",
    "1d": "1 dia",
    "1w": "1 semana",
}

REFERENCE_PATTERNS = {
    "invalidation": re.compile(
        r"(?:invalida(?:ç|c)[ãa]o(?:\s+indicativa)?|stop(?:\s+indicativo)?)\s*:\s*"
        r"([-+]?\d+(?:[.,]\d+)?)",
        re.IGNORECASE,
    ),
    "target": re.compile(
        r"(?:objetivo(?:\s+ilustrativo)?|alvo(?:\s+ilustrativo)?)\s*:\s*"
        r"([-+]?\d+(?:[.,]\d+)?)",
        re.IGNORECASE,
    ),
}


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
        number = Decimal(str(value).replace(",", "."))
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


def _parse_decimal(value):
    try:
        return Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _extract_reference(reason, pattern):
    match = pattern.search(reason or "")
    if not match:
        return None
    value = _parse_decimal(match.group(1))
    if value is None or not value.is_finite():
        return None
    return value


def _build_reasons(reason, rsi, relative_volume):
    source = str(reason or "").strip()
    source = REFERENCE_PATTERNS["invalidation"].sub("", source)
    source = REFERENCE_PATTERNS["target"].sub("", source)
    source = re.sub(r"\bRSI\s*[:=]\s*[-+]?\d+(?:[.,]\d+)?", "", source, flags=re.IGNORECASE)
    source = re.sub(
        r"(?:volume\s+relativo|vol(?:ume)?\s+rel)\s*[:=]\s*[-+]?\d+(?:[.,]\d+)?x?",
        "",
        source,
        flags=re.IGNORECASE,
    )
    source = re.sub(
        r"refer[eê]ncia t[eé]cnica,?\s*n[aã]o recomenda[cç][aã]o",
        "",
        source,
        flags=re.IGNORECASE,
    )
    source = source.replace(";", ".")
    source = re.sub(r"\bSMA\s*20\s*>\s*SMA\s*50\b", "SMA 20 acima da SMA 50", source, flags=re.IGNORECASE)
    source = re.sub(r"\s+([,.;])", r"\1", source)
    source = re.sub(r"([,.;])\s*([,.;])+", r"\1", source)
    source = re.sub(r"\s+", " ", source).strip(" .;,-")
    items = []
    if source and source.lower() not in {"referência técnica, não recomendação", "referencia tecnica, nao recomendacao"}:
        items.append(source.rstrip(".") + ".")
    if rsi is not None:
        items.append(f"RSI: {rsi}.")
    if relative_volume is not None:
        items.append(f"Volume relativo: {relative_volume}x.")
    return items or ["Não informado."]


def format_message(alert):
    """Build the approved Telegram layout; raw alert values remain unchanged in JSONL."""
    agent = alert.get("agent") or "Agente não identificado"
    symbol = str(alert.get("symbol") or "N/D")
    if symbol.endswith("USDT") and "/" not in symbol:
        symbol = f"{symbol[:-4]}/USDT"

    raw_timeframe = str(alert.get("timeframe") or "N/D")
    timeframe = TIMEFRAME_LABELS.get(raw_timeframe.lower(), raw_timeframe)
    price = _number(alert.get("price"), 2)
    signal = alert.get("signal") or "Não informado"
    rsi = _number(alert.get("rsi"), 1) if alert.get("rsi") is not None else None
    relative_volume = _number(alert.get("relative_volume"), 2) if alert.get("relative_volume") is not None else None
    reasons = _build_reasons(alert.get("reason"), rsi, relative_volume)

    reason_source = str(alert.get("reason") or "")
    invalidation = _extract_reference(reason_source, REFERENCE_PATTERNS["invalidation"])
    target = _extract_reference(reason_source, REFERENCE_PATTERNS["target"])
    change = _number(alert.get("change_pct"), 3, signed=True)
    atr = _number(alert.get("atr"), 2)
    confidence = alert.get("confidence") or "Não calibrada"
    risk = alert.get(
        "risk_note",
        "Sinal preliminar. Verifique custos, liquidez, spread e contexto de mercado.",
    )
    candle_time = _time_brasilia(alert.get("candle_time_utc"))

    reason_lines = "\n".join(f"- {item}" for item in reasons)
    reference_lines = [
        f"- Invalidação: US$ {_number(invalidation, 2)}.",
        f"- Objetivo ilustrativo: US$ {_number(target, 2)}.",
        f"- Variação: {change}%.",
        f"- ATR: {atr}.",
    ]

    return (
        f"DOT CRIPTO | {str(agent).upper()}\n\n"
        f"Ativo: {symbol}\n"
        f"Período: {timeframe}\n"
        f"Preço de referência: US$ {price}\n\n"
        f"SINAL: {str(signal).upper()}\n\n"
        f"Motivos:\n{reason_lines}\n\n"
        f"Referências técnicas:\n{chr(10).join(reference_lines)}\n\n"
        f"CONFIANÇA: {confidence}.\n\n"
        f"RISCO: {risk}\n\n"
        f"Horário da coleta: {candle_time}.\n\n"
        "Alerta exclusivamente informativo. Não constitui recomendação de investimento, "
        "não executa ordens e não garante resultados."
    )
