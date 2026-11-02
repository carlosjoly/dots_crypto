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

    if not env.get("telegram_bot_token") or not env.get("telegram_chat_id"):
        raise SystemExit("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID ausentes; validação interrompida.")

    # Test LunarCrush independently. A provider failure must not prevent the
    # Telegram transport test from running; report both outcomes separately.
    api_key_present = bool(
        os.getenv("LUNARCRUSH_API_KEY", "").strip()
        or os.getenv("SOCIAL_API_KEY", "").strip()
    )
    result = None
    social_error = None
    if api_key_present:
        try:
            result = run_bot_g_observer(cfg, force_collection=True)
            if result.get("status") != "observed":
                social_error = f"BotGStatus:{result.get('status')}"
            elif result.get("delivery_failed", 0) or result.get("pending_telegram", 0):
                social_error = "SocialAlertDeliveryIncomplete"
        except Exception as exc:
            # Avoid printing raw exception text, headers, URLs or credentials.
            social_error = type(exc).__name__
    else:
        social_error = "LunarCrushKeyMissing"

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
    try:
        emit_alert(cfg, test_alert)
    except Exception as exc:
        # Never print the raw exception: Telegram request URLs can contain the bot token.
        raise SystemExit(f"Falha no teste de entrega Telegram ({type(exc).__name__}).") from None
    social_status = result.get("status") if result else "not_tested"
    social_alerts = result.get("alerts", 0) if result else 0
    logging.getLogger("dot_cripto.bot_g").info(
        "Validação isolada: LunarCrush=%s; teste Telegram enviado.",
        social_status if not social_error else f"failed:{social_error}",
    )
    print(
        "BOT_G_TELEGRAM_TEST_SENT "
        f"social_status={social_status} "
        f"social_error={social_error or 'none'} "
        f"collected={result.get('collected', 0) if result else 0} "
        f"social_alerts={social_alerts} "
        f"delivery_failed={result.get('delivery_failed', 0) if result else 0}"
    )
    if social_error:
        raise SystemExit(
            "Telegram validado, mas a coleta LunarCrush não foi validada "
            f"({social_error})."
        )


if __name__ == "__main__":
    main()
