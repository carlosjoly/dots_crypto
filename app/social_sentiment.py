"""Bot G: normalized social-volume/sentiment adapter with mandatory GoPlus screening.

The configured social provider must return the documented normalized JSON schema.
No social signal is emitted when data is stale, incomplete, suspicious, or unverified.
"""
from datetime import datetime, timezone
import logging
import os

import requests

from .security import goplus_token_security, evaluate_launch_security

log = logging.getLogger("dot_cripto.bot_g")


def _finite_number(value, name, minimum=None, maximum=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"Bot G: campo {name} ausente ou inválido")
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"Bot G: campo {name} não finito")
    if minimum is not None and number < minimum:
        raise ValueError(f"Bot G: {name} abaixo do mínimo")
    if maximum is not None and number > maximum:
        raise ValueError(f"Bot G: {name} acima do máximo")
    return number


def fetch_social_metrics(symbol, cfg, session=requests):
    """Fetch canonical metrics from a configured, licensed social-data gateway.

    Expected JSON fields: symbol, observed_at, social_volume,
    previous_social_volume, sentiment_score, previous_sentiment_score,
    unique_authors, bot_ratio, source_count, sources.
    Scores and bot_ratio use a 0..100 scale; volume is a non-negative count.
    """
    env = cfg.get("_env", {})
    endpoint = env.get("social_api_url", "").strip()
    api_key = env.get("social_api_key", "").strip()
    if not endpoint or not api_key:
        raise RuntimeError("Bot G desabilitado: SOCIAL_API_URL e SOCIAL_API_KEY não configurados")
    response = session.get(
        endpoint,
        params={"symbol": symbol},
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Bot G: resposta social não é um objeto JSON")
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise ValueError("Bot G: objeto data ausente ou inválido")
    return data


def evaluate_social_metrics(data, cfg):
    settings = cfg.get("social", {})
    observed = data.get("observed_at")
    if not observed:
        raise ValueError("Bot G: timestamp social ausente")
    raw = str(observed).replace("Z", "+00:00")
    timestamp = datetime.fromisoformat(raw)
    if timestamp.tzinfo is None:
        raise ValueError("Bot G: timestamp sem fuso horário")
    age = (datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)).total_seconds()
    if age < -300 or age > int(settings.get("max_data_age_minutes", 90)) * 60:
        raise ValueError("Bot G: dados sociais futuros ou desatualizados")

    volume = _finite_number(data.get("social_volume"), "social_volume", 0)
    previous_volume = _finite_number(data.get("previous_social_volume"), "previous_social_volume", 0)
    sentiment = _finite_number(data.get("sentiment_score"), "sentiment_score", 0, 100)
    previous_sentiment = _finite_number(data.get("previous_sentiment_score"), "previous_sentiment_score", 0, 100)
    authors = int(_finite_number(data.get("unique_authors"), "unique_authors", 0))
    bot_ratio = _finite_number(data.get("bot_ratio"), "bot_ratio", 0, 100)
    source_count = int(_finite_number(data.get("source_count"), "source_count", 1))
    sources = data.get("sources")
    if not isinstance(sources, list) or len(sources) < int(settings.get("min_source_count", 2)):
        raise ValueError("Bot G: diversidade mínima de fontes não comprovada")
    if authors < int(settings.get("min_unique_authors", 25)):
        raise ValueError("Bot G: número insuficiente de autores únicos")
    if source_count < int(settings.get("min_source_count", 2)):
        raise ValueError("Bot G: número insuficiente de fontes")
    if bot_ratio > float(settings.get("max_bot_ratio_pct", 25)):
        raise ValueError("Bot G: proporção estimada de bots/spam acima do limite")
    if previous_volume <= 0:
        raise ValueError("Bot G: base histórica de volume inválida")

    volume_change_pct = (volume / previous_volume - 1) * 100
    sentiment_change = sentiment - previous_sentiment
    surge = volume_change_pct >= float(settings.get("volume_surge_pct", 50))
    shift = abs(sentiment_change) >= float(settings.get("sentiment_shift_points", 8))
    if not (surge and shift):
        return None

    direction = "positiva" if sentiment_change > 0 else "negativa"
    return {
        "social_volume": volume,
        "previous_social_volume": previous_volume,
        "volume_change_pct": round(volume_change_pct, 2),
        "sentiment_score": sentiment,
        "previous_sentiment_score": previous_sentiment,
        "sentiment_change": round(sentiment_change, 2),
        "unique_authors": authors,
        "bot_ratio_pct": bot_ratio,
        "source_count": source_count,
        "sources": [str(s)[:80] for s in sources[:10]],
        "observed_at": timestamp.astimezone(timezone.utc).isoformat(),
        "direction": direction,
    }


def screen_social_asset(symbol, cfg):
    """GoPlus must return a complete clean EVM record; all other outcomes block."""
    mapping = cfg.get("social", {}).get("contracts", {}).get(symbol)
    if not isinstance(mapping, dict) or not mapping.get("chain_id") or not mapping.get("address"):
        raise ValueError(f"Bot G: contrato EVM/chain_id não mapeado para {symbol}")
    env = cfg.get("_env", {})
    if not env.get("goplus_enabled"):
        raise RuntimeError("Bot G bloqueado: GoPlus não está habilitado")
    record = goplus_token_security(
        str(mapping["chain_id"]), str(mapping["address"]),
        app_key=env.get("goplus_app_key", ""),
        app_secret=env.get("goplus_app_secret", ""),
    )
    assessment = evaluate_launch_security(
        record, max_tax=float(cfg.get("launches", {}).get("max_buy_sell_tax", 0.05))
    )
    if assessment["status"] != "eligible_for_watchlist":
        raise ValueError(
            "Bot G bloqueado pela triagem GoPlus: "
            + "; ".join(assessment.get("flags", [])[:8])
        )
    return assessment


def run_social_agent(symbol, cfg, session=requests):
    if not cfg.get("social", {}).get("enabled", False):
        return None
    metrics = fetch_social_metrics(symbol, cfg, session=session)
    evaluated = evaluate_social_metrics(metrics, cfg)
    if evaluated is None:
        return None
    security = screen_social_asset(symbol, cfg)
    sentiment = evaluated["direction"]
    signal = "aumento de atenção social com sentimento " + sentiment
    return {
        "agent": "Bot G — Sentimento & Volume Social",
        "symbol": symbol,
        "timeframe": "social",
        "price": None,
        "signal": signal,
        "reason": (
            f"Volume social {evaluated['volume_change_pct']:+.2f}% vs. período anterior; "
            f"sentimento {evaluated['sentiment_score']:.1f}/100 "
            f"(variação {evaluated['sentiment_change']:+.1f} pontos)."
        ),
        "social_metrics": evaluated,
        "goplus_status": security["status"],
        "confidence": "não calibrada",
        "risk_note": (
            "Métrica de atenção, não sinal de compra/venda. Sentimento pode ser manipulado; "
            "a triagem GoPlus não garante segurança. Confirmar liquidez, spread e dados de mercado."
        ),
        "candle_time_utc": evaluated["observed_at"],
    }
