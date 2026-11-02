"""Isolated manual validation for Bot G + Telegram delivery.

This entry point deliberately does not run market bots A-F.
"""
from datetime import datetime, timezone
import logging
import os

from .alerts import emit_alert
from .bot_g import run_bot_g_observer
from .config import load_config
from .logging_setup import setup_logging


def main():
    cfg = load_config()
    setup_logging(cfg["_root"])
    env = cfg["_env"]

    if not os.getenv("LUNARCRUSH_API_KEY", "").strip() and not os.getenv("SOCIAL_API_KEY", "").strip():
        raise SystemExit("LUNARCRUSH_API_KEY ausente; validação interrompida.")
    if not env.get("telegram_bot_token") or not env.get("telegram_chat_id"):
        raise SystemExit("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID ausentes; validação interrompida.")

    # Runs only Bot G. If real social conditions are met, Bot G emits its alerts.
    result = run_bot_g_observer(cfg)
    if result.get("status") == "disabled":
        raise SystemExit("Bot G permaneceu desativado; nenhuma validação foi concluída.")

    # A clearly labelled transport test confirms Telegram delivery without
    # presenting a synthetic test as a market signal.
    test_alert = {
        "agent": "BOT G — TESTE DE ENTREGA",
        "symbol": "BTCUSDT",
        "timeframe": "validação",
        "price": None,
        "signal": "TESTE DE TELEGRAM — NÃO É SINAL DE MERCADO",
        "reason": "Teste controlado de formatação e transporte Telegram do Bot G.",
        "confidence": "Não aplicável",
        "risk_note": "Mensagem técnica de validação. Não representa leitura de mercado, recomendação ou ordem.",
        "candle_time_utc": datetime.now(timezone.utc).isoformat(),
    }
    emit_alert(cfg, test_alert)
    logging.getLogger("dot_cripto.bot_g").info(
        "Validação isolada concluída: status=%s; ativos=%s; alertas sociais=%s; teste Telegram enviado.",
        result.get("status"), result.get("collected"), result.get("alerts"),
    )
    print(
        "BOT_G_VALIDATION_OK "
        f"status={result.get('status')} "
        f"collected={result.get('collected', 0)} "
        f"social_alerts={result.get('alerts', 0)} "
        f"delivery_failed={result.get('delivery_failed', 0)}"
    )


if __name__ == "__main__":
    main()
