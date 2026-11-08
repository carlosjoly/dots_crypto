"""Standalone scheduled runner for Bot F; no trading or order execution."""
import logging

from .alerts import emit_alert
from .config import load_config
from .logging_setup import setup_logging
from .new_launches import release_seen_token, scan_new_launches

log = logging.getLogger("dot_cripto.bot_f.runner")


def main():
    cfg = load_config()
    setup_logging(cfg["_root"])

    if not cfg.get("launches", {}).get("enabled", False):
        log.error("Bot F está desativado em config.yaml; execução cancelada.")
        return 2

    try:
        alerts = scan_new_launches(cfg)
    except Exception:
        log.exception("Bot F: falha crítica na coleta/triagem; ciclo encerrado.")
        return 1

    processed = 0
    delivery_errors = 0
    for alert in alerts:
        try:
            emit_alert(cfg, alert)
            processed += 1
            log.info(
                "Bot F: alerta processado para %s na rede %s.",
                alert.get("symbol", "N/D"),
                alert.get("network", "N/D"),
            )
        except Exception:
            delivery_errors += 1
            release_seen_token(cfg, alert)
            log.exception(
                "Bot F: falha ao emitir alerta de %s; candidato liberado para nova tentativa.",
                alert.get("token_address", "N/D"),
            )

    log.info(
        "Bot F standalone concluído: candidatos_aprovados=%d; alertas_processados=%d; "
        "falhas_de_entrega=%d; telegram_configurado=%s.",
        len(alerts),
        processed,
        delivery_errors,
        bool(cfg["_env"].get("telegram_bot_token") and cfg["_env"].get("telegram_chat_id")),
    )
    return 1 if delivery_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
