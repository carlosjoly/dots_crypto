"""Standalone scheduled runner for Bot F; no trading or order execution."""
import logging
import os

from .alerts import emit_alert
from .config import load_config
from .logging_setup import setup_logging
from .new_launches import (
    release_seen_token, scan_new_launches, fetch_current_pool_liquidity,
    defer_lp_proof_review,
)
from .onchain_lp import JsonRpc
from .uncx_bsc_lock import build_bsc_pancakeswap_v2_lp_proof, is_valid_bsc_pancakeswap_v2_lp_proof

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
    proof_verified = 0
    blocked_lp = 0
    delivery_errors = 0
    for alert in alerts:
        if (
            alert.get("discovery_status") != "ELEGÍVEL PARA WATCHLIST — NÃO É RECOMENDAÇÃO"
            or alert.get("goplus_status") != "eligible_for_watchlist"
        ):
            log.warning(
                "Bot F Telegram bloqueado: candidato pendente ou sem aprovação de segurança para %s.",
                alert.get("pool_id", alert.get("symbol", "N/D")),
            )
            continue
        if alert.get("network") != "bsc" or str(alert.get("chain_id")) != "56":
            log.warning("Bot F Telegram bloqueado: sem adaptador de lock on-chain aprovado para %s/%s.",
                        alert.get("network", "N/D"), alert.get("pool_id", alert.get("symbol", "N/D")))
            continue
        rpc_url = os.getenv("BOT_F_RPC_URL_BSC", "").strip()
        if not rpc_url.startswith(("https://", "http://")):
            blocked_lp += 1
            defer_lp_proof_review(cfg, alert, "RPC BSC ausente ou inválido")
            log.error("Bot F BSC bloqueado: BOT_F_RPC_URL_BSC ausente ou inválido; endpoint não será exibido.")
            continue
        try:
            fresh = fetch_current_pool_liquidity(alert.get("network"), alert.get("pool_address"))
            if fresh["classification"] != "passed":
                blocked_lp += 1
                defer_lp_proof_review(cfg, alert, f"liquidez atual {fresh['classification']}: abaixo de US$ 25.000 ou indisponível")
                log.warning(
                    "Bot F Telegram bloqueado: liquidez atual %s (%s) para %s; mínimo US$ 25.000.",
                    fresh["classification"], fresh.get("liquidity_usd"),
                    alert.get("pool_id", alert.get("symbol", "N/D")),
                )
                continue
            # Replace discovery-supplied metadata with a fresh read-only RPC proof.
            proof = build_bsc_pancakeswap_v2_lp_proof(
                alert.get("pool_address"), JsonRpc(rpc_url), liquidity_usd=fresh["liquidity_usd"]
            )
            alert["independent_lp_proof"] = proof
            if not is_valid_bsc_pancakeswap_v2_lp_proof(proof, alert.get("pool_address"), fresh["liquidity_usd"], alert.get("token_address")):
                blocked_lp += 1
                defer_lp_proof_review(cfg, alert, proof.get("reason") or "prova UNCX/BSC não aprovada")
                log.warning("Bot F Telegram bloqueado: prova UNCX/BSC não aprovada para %s; status=%s; motivo=%s",
                            alert.get("pool_id", alert.get("symbol", "N/D")), proof.get("status"), proof.get("reason"))
                continue
            proof_verified += 1
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

    log.info(
        "Bot F standalone concluído: candidatos_GoPlus_elegíveis=%d; provas_LP_válidas=%d; "
        "bloqueados_LP=%d; alertas_processados=%d; falhas_de_entrega=%d; telegram_configurado=%s.",
        len(alerts),
        proof_verified,
        blocked_lp,
        processed,
        delivery_errors,
        bool(cfg["_env"].get("telegram_bot_token") and cfg["_env"].get("telegram_chat_id")),
    )
    return 1 if delivery_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
