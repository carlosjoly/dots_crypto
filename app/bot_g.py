"""Bot G: hourly social-metric observer with fail-closed GoPlus review.

This module records observations only. It never creates trading signals or
sends Telegram messages. Social source freshness remains explicitly unverified.
"""
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path

from .lunarcrush_social import fetch_social_snapshots
from .security import goplus_token_security, evaluate_launch_security

log = logging.getLogger("dot_cripto.bot_g")

# Official Chainlink documentation: Ethereum mainnet, chain ID 1.
# Native BTC/ETH/BNB are not EVM token contracts and are not sent to GoPlus.
LINK_CONTRACTS = {
    "ethereum": {
        "chain_id": "1",
        "address": "0x514910771AF9Ca656af840dff83E8264EcF986CA",
        "source": "https://docs.chain.link/resources/link-token-contracts",
    }
}
SOCIAL_SYMBOLS = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "LINKUSDT")
COLLECTION_INTERVAL = timedelta(hours=1)
HISTORY_RETENTION = timedelta(days=7)
GOPLUS_RECHECK_INTERVAL = timedelta(hours=24)


def _parse_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _load_state(path):
    target = Path(path)
    if not target.exists():
        return {"last_collected_at": None, "assets": {}, "goplus": {}}
    try:
        state = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise RuntimeError("Histórico do Bot G ilegível; coleta bloqueada para preservar os dados.") from None
    if not isinstance(state, dict) or not isinstance(state.get("assets", {}), dict):
        raise RuntimeError("Estrutura do histórico do Bot G inválida; coleta bloqueada.")
    state.setdefault("last_collected_at", None)
    state.setdefault("assets", {})
    state.setdefault("goplus", {})
    return state


def _save_state(path, state):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def _check_link_goplus(state, now, env):
    previous = state.get("goplus", {}).get("ethereum_link", {})
    last_success = _parse_time(previous.get("checked_at"))
    if last_success and now - last_success < GOPLUS_RECHECK_INTERVAL:
        return previous

    contract = LINK_CONTRACTS["ethereum"]
    try:
        raw = goplus_token_security(
            contract["chain_id"],
            contract["address"],
            env.get("goplus_app_key", ""),
            env.get("goplus_app_secret", ""),
        )
        assessment = evaluate_launch_security(raw)
        result = {
            "status": assessment["status"],
            "flags": assessment["flags"],
            "checked_at": now.isoformat(),
            "chain_id": contract["chain_id"],
            "contract": contract["address"],
            "source": contract["source"],
        }
        state["goplus"]["ethereum_link"] = result
        log.info("Bot G GoPlus LINK/Ethereum: %s; flags=%d", result["status"], len(result["flags"]))
        return result
    except Exception as exc:
        # Do not log response bodies, request headers, or credentials.
        result = {
            "status": "review_required",
            "flags": [f"GoPlus indisponível ({type(exc).__name__}); triagem não concluída"],
            "checked_at": None,
            "last_attempt_at": now.isoformat(),
            "chain_id": contract["chain_id"],
            "contract": contract["address"],
            "source": contract["source"],
        }
        state["goplus"]["ethereum_link"] = result
        log.warning("Bot G GoPlus indisponível para LINK/Ethereum (%s).", type(exc).__name__)
        return result


def run_bot_g_observer(cfg, now=None):
    """Collect a bounded hourly social snapshot and persist a 7-day observation history."""
    api_key = os.getenv("SOCIAL_API_KEY", "").strip()
    if not api_key:
        log.info("Bot G inativo: SOCIAL_API_KEY não configurada.")
        return {"status": "disabled", "collected": 0}

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Bot G requires an offset-aware timestamp.")
    now = now.astimezone(timezone.utc)
    state_path = Path(cfg["_root"]) / "data" / "bot_g_social_history.json"
    state = _load_state(state_path)

    last = _parse_time(state.get("last_collected_at"))
    if last and now - last < COLLECTION_INTERVAL:
        log.info("Bot G: janela horária ainda não venceu; coleta social ignorada.")
        return {"status": "throttled", "collected": 0}

    snapshots = fetch_social_snapshots(SOCIAL_SYMBOLS, api_key, now=now)
    if not snapshots:
        raise RuntimeError("LunarCrush não retornou ativos sociais monitorados; histórico não atualizado.")

    cutoff = now - HISTORY_RETENTION
    for symbol, snapshot in snapshots.items():
        history = state["assets"].setdefault(symbol, [])
        if not isinstance(history, list):
            raise RuntimeError(f"Histórico inválido para {symbol}; coleta bloqueada.")
        history.append(snapshot)
        state["assets"][symbol] = [
            item for item in history
            if _parse_time(item.get("collected_at")) and _parse_time(item.get("collected_at")) >= cutoff
        ][-200:]
        log.info(
            "Bot G social %s: volume_24h=%s; sentiment=%s; freshness=%s; contributors/spam/bot_ratio=N/D",
            symbol, snapshot["social_volume_24h"], snapshot["sentiment"],
            "não verificada",
        )

    state["last_collected_at"] = now.isoformat()
    _check_link_goplus(state, now, cfg.get("_env", {}))
    _save_state(state_path, state)
    log.info(
        "Bot G observador concluído: %d ativos; histórico de até 7 dias; sem sinais e sem Telegram.",
        len(snapshots),
    )
    return {"status": "observed", "collected": len(snapshots)}
