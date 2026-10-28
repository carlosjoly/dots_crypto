import json
import os
from pathlib import Path
from datetime import datetime, timezone
import requests

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
        r = requests.post(url, json={"chat_id": env["telegram_chat_id"], "text": msg,
                                    "disable_web_page_preview": True}, timeout=12)
        r.raise_for_status()

def format_message(a):
    return (
        f"🔎 DOT CRIPTO | {a.get('agent')}\\n"
        f"Par: {a.get('symbol')} | Período: {a.get('timeframe')}\\n"
        f"Preço de referência: {a.get('price')}\\n"
        f"Sinal: {a.get('signal')}\\n"
        f"Motivo: {a.get('reason')}\\n"
        f"Variação: {a.get('change_pct')}% | Volume relativo: {a.get('relative_volume')}x\\n"
        f"RSI: {a.get('rsi')} | ATR: {a.get('atr')}\\n"
        f"Confiança: {a.get('confidence', 'não calibrada')}\\n"
        f"Risco: {a.get('risk_note', 'Sinal preliminar; verificar custos, liquidez e contexto.')}\\n"
        f"Hora UTC: {a.get('candle_time_utc')}\\n"
        "Alerta informativo. Não é ordem nem garantia de resultado."
    )
