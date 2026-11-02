"""Bot G: social signal validation with per-asset Telegram cooldown.

Signals are informational only. The provider's social-metric freshness is
currently unverified, so every message carries that limitation explicitly.
"""
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path

from .alerts import emit_alert
from .lunarcrush_social import fetch_social_snapshots
from .security import goplus_token_security, evaluate_launch_security

log = logging.getLogger("dot_cripto.bot_g")

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
ALERT_COOLDOWN = timedelta(hours=6)
SENTIMENT_HIGH = 75.0
SENTIMENT_LOW = 25.0
SOCIAL_VOLUME_ACCELERATION_PCT = 30.0


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
        return {"last_collected_at": None, "assets": {}, "goplus": {}, "alerts": {}}
    try:
        state = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise RuntimeError("Histórico do Bot G ilegível; coleta bloqueada para preservar os dados.") from None
    if not isinstance(state, dict) or not isinstance(state.get("assets", {}), dict):
        raise RuntimeError("Estrutura do histórico do Bot G inválida; coleta bloqueada.")
    for key, default in (("last_collected_at", None), ("assets", {}), ("goplus", {}), ("alerts", {})):
        state.setdefault(key, default)
    if not isinstance(state["alerts"], dict) or not isinstance(state["goplus"], dict):
        raise RuntimeError("Estado de alertas/GoPlus do Bot G inválido; coleta bloqueada.")
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


def _signal_for_snapshot(symbol, snapshot, previous):
    """Return a social alert only for extreme sentiment plus confirmed volume acceleration.

    On an asset's first observation there is no baseline: an extreme sentiment
    reading is allowed as a clearly labelled preliminary alert, without claiming
    that social volume accelerated.
    """
    sentiment = float(snapshot["sentiment"])
    volume = float(snapshot["social_volume_24h"])
    if sentiment >= SENTIMENT_HIGH:
        direction = "POSITIVO"
        sentiment_label = "muito positivo"
    elif sentiment <= SENTIMENT_LOW:
        direction = "NEGATIVO"
        sentiment_label = "muito negativo"
    else:
        return None

    acceleration = None
    if previous is not None:
        previous_volume = float(previous.get("social_volume_24h", 0))
        if previous_volume <= 0:
            return None
        acceleration = round(((volume / previous_volume) - 1.0) * 100.0, 2)
        if acceleration < SOCIAL_VOLUME_ACCELERATION_PCT:
            return None

    if acceleration is None:
        volume_text = "Primeira observação: aceleração do volume social ainda não verificável."
    else:
        volume_text = f"Aceleração do volume social em relação à coleta anterior: +{acceleration:.1f}%."

    return {
        "agent": "BOT G — VALIDAÇÃO SOCIAL",
        "symbol": symbol,
        "timeframe": "24h social",
        "price": None,
        "signal": f"SENTIMENTO SOCIAL {direction}",
        "rsi": None,
        "relative_volume": None,
        "change_pct": None,
        "atr": None,
        "confidence": "Não calibrada",
        "reason": (
            f"Sentimento {sentiment_label}: {sentiment:.1f}/100. "
            f"Volume social 24h: {volume:g}. {volume_text}"
        ),
        "risk_note": (
            "Sinal experimental de sentimento, não é sinal de compra/venda. "
            "A atualização temporal das métricas sociais da fonte não está verificada; "
            "pode haver atraso, ruído ou manipulação. Confirme preço, liquidez e contexto "
            "em fontes independentes. Não executa ordens."
        ),
        "candle_time_utc": snapshot["collected_at"],
        "social_sentiment": sentiment,
        "social_volume_24h": volume,
        "social_volume_acceleration_pct": acceleration,
        "source_freshness_verified": bool(snapshot.get("source_freshness_verified", False)),
        "source": "LunarCrush",
    }


def _emit_social_alerts(cfg, state, snapshots, now):
    env = cfg.get("_env", {})
    alerts_cfg = cfg.get("alerts", {})
    telegram_ready = (
        alerts_cfg.get("telegram_enabled", True)
        and bool(env.get("telegram_bot_token"))
        and bool(env.get("telegram_chat_id"))
    )
    emitted = 0
    pending = 0
    failed = 0

    for symbol, snapshot in snapshots.items():
        history = state["assets"].get(symbol, [])
        # The current snapshot is appended before signal evaluation. Exclude it
        # from the baseline; only an earlier observation may confirm acceleration.
        previous = history[-2] if len(history) >= 2 else None
        alert = _signal_for_snapshot(symbol, snapshot, previous)
        if alert is None:
            continue

        last_sent = _parse_time(state["alerts"].get(symbol, {}).get("last_sent_at"))
        if last_sent and now - last_sent < ALERT_COOLDOWN:
            log.info("Bot G: alerta %s suprimido pelo intervalo mínimo de 6h.", symbol)
            continue

        if not telegram_ready:
            pending += 1
            log.warning("Bot G: sinal social %s identificado, mas Telegram não está configurado; envio pendente.", symbol)
            continue

        # emit_alert writes the JSONL record and sends Telegram. If it raises,
        # do not advance the per-asset cooldown; the next cycle can retry.
        try:
            emit_alert(cfg, alert)
        except Exception as exc:
            # Avoid logging exception text: Telegram URLs can contain bot tokens.
            failed += 1
            log.error("Bot G: falha no envio Telegram para %s (%s); cooldown não consumido.", symbol, type(exc).__name__)
            continue
        state["alerts"][symbol] = {
            "last_sent_at": now.isoformat(),
            "signal": alert["signal"],
        }
        emitted += 1
        log.info("Bot G: alerta Telegram emitido para %s.", symbol)

    return {"emitted": emitted, "pending_telegram": pending, "failed": failed}


def run_bot_g_observer(cfg, now=None):
    """Collect hourly snapshots, evaluate social signals, and persist 7-day history."""
    # Canonical secret name; SOCIAL_API_KEY remains as a backward-compatible alias.
    api_key = (
        os.getenv("LUNARCRUSH_API_KEY", "").strip()
        or os.getenv("SOCIAL_API_KEY", "").strip()
    )
    if not api_key:
        log.info("Bot G inativo: SOCIAL_API_KEY não configurada.")
        return {"status": "disabled", "collected": 0, "alerts": 0}

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Bot G requires an offset-aware timestamp.")
    now = now.astimezone(timezone.utc)
    state_path = Path(cfg["_root"]) / "data" / "bot_g_social_history.json"
    state = _load_state(state_path)

    last = _parse_time(state.get("last_collected_at"))
    if last and now - last < COLLECTION_INTERVAL:
        log.info("Bot G: janela horária ainda não venceu; coleta social ignorada.")
        return {"status": "throttled", "collected": 0, "alerts": 0}

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
            "verificada" if snapshot.get("source_freshness_verified") else "não verificada",
        )

    alert_result = _emit_social_alerts(cfg, state, snapshots, now)
    state["last_collected_at"] = now.isoformat()
    _check_link_goplus(state, now, cfg.get("_env", {}))
    _save_state(state_path, state)
    log.info(
        "Bot G concluído: %d ativos; alertas Telegram=%d; pendentes=%d; histórico de até 7 dias.",
        len(snapshots), alert_result["emitted"], alert_result["pending_telegram"],
    )
    return {
        "status": "observed",
        "collected": len(snapshots),
        "alerts": alert_result["emitted"],
        "pending_telegram": alert_result["pending_telegram"],
        "delivery_failed": alert_result["failed"],
    }
