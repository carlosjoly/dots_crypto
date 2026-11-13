"""Standalone scheduled runner for Bot F; no trading or order execution."""
import logging

from .alerts import emit_alert
from .config import load_config
from .logging_setup import setup_logging
from .new_launches import (
    release_seen_token, scan_new_launches, fetch_current_pool_liquidity,
    independent_lp_proof_is_valid,
)

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
            fresh = fetch_current_pool_liquidity(alert.get("network"), alert.get("pool_address"))
            if fresh["classification"] != "passed":
                log.warning(
                    "Bot F Telegram bloqueado: liquidez atual %s (%s) para %s; mínimo US$ 25.000.",
                    fresh["classification"], fresh.get("liquidity_usd"),
                    alert.get("pool_id", alert.get("symbol", "N/D")),
                )
                continue
            if not independent_lp_proof_is_valid(alert):
                log.warning(
                    "Bot F Telegram bloqueado: sem prova independente on-chain de reservas e bloqueio LP para %s.",
                    alert.get("pool_id", alert.get("symbol", "N/D")),
                )
                continue
            alert["liquidity_usd"] = fresh["liquidity_usd"]
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

    pending = sum(
        1 for alert in alerts
        if alert.get("discovery_status") == "PENDENTE DE REVISÃO — NÃO APROVADO"
    )
    approved = len(alerts) - pending
    log.info(
        "Bot F standalone concluído: descoberta_pendente=%d; candidatos_aprovados=%d; "
        "alertas_processados=%d; falhas_de_entrega=%d; telegram_configurado=%s.",
        pending,
        approved,
        processed,
        delivery_errors,
        bool(cfg["_env"].get("telegram_bot_token") and cfg["_env"].get("telegram_chat_id")),
    )
    return 1 if delivery_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
