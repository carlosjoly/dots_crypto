"""Discovery and fail-closed screening of newly created EVM DEX pools."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import logging
import random
from pathlib import Path
import re
import time

import requests

from .security import get_goplus_access_token, goplus_token_security, evaluate_launch_security

log = logging.getLogger("dot_cripto.bot_f")
GECKO_BASE = "https://api.geckoterminal.com/api/v2"
TIMEOUT = 15
# GeckoTerminal's public limit is approximately 10 calls/minute; keep a safety margin.
REQUEST_INTERVAL_SECONDS = 10
MAX_RETRIES = 3
RETRY_JITTER_SECONDS = 2
MIN_RETRY_AFTER_SECONDS = REQUEST_INTERVAL_SECONDS
API_METRICS_PATH = Path(__file__).resolve().parents[1] / "data" / "bot_f_api_metrics.jsonl"
GECKO_NETWORK_IDS = {"avalanche": "avax"}
GECKO_ACCEPT = "application/json;version=20230203"

NETWORKS = {
    "eth": "1",
    "base": "8453",
    "bsc": "56",
    "arbitrum": "42161",
    "polygon_pos": "137",
    "optimism": "10",
    "avalanche": "43114",
}

def _retry_after_seconds(response):
    """Parse Retry-After as delta seconds or an HTTP date."""
    value = (getattr(response, "headers", {}) or {}).get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(str(value))
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def _record_api_metric(url, status, attempt, retry_after=None, wait_seconds=0):
    if not isinstance(status, int):
        status = None
    network_slug = url.split("/networks/")[-1].split("/")[0] if "/networks/" in url else "N/D"
    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "network_slug": network_slug,
        "status": status,
        "attempt": attempt + 1,
        "retry_after_seconds": retry_after,
        "wait_seconds": round(float(wait_seconds), 3),
    }
    API_METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with API_METRICS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + chr(10))


def _get_json(url, params=None):
    """GET JSON with bounded backoff for rate limits and transient server errors."""
    retryable_statuses = {429, 500, 502, 503, 504}
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = requests.get(
                url, params=params,
                headers={"Accept": GECKO_ACCEPT, "User-Agent": "DotCripto-BotF/1.0"},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            _record_api_metric(url, response.status_code, attempt)
            return response.json()
        except requests.exceptions.HTTPError as exc:
            response = exc.response
            status = getattr(response, "status_code", None)
            retry_after = _retry_after_seconds(response) if status == 429 else None
            if status not in retryable_statuses or attempt >= MAX_RETRIES:
                _record_api_metric(url, status, attempt, retry_after, 0)
                raise
            exponential = REQUEST_INTERVAL_SECONDS * (2 ** attempt)
            wait = max(retry_after, MIN_RETRY_AFTER_SECONDS) if retry_after is not None else (
                exponential + random.uniform(0, RETRY_JITTER_SECONDS)
            )
            network_slug = url.split("/networks/")[-1].split("/")[0] if "/networks/" in url else "N/D"
            _record_api_metric(url, status, attempt, retry_after, wait)
            log.warning(
                "GeckoTerminal rate_limit_retry network=%s status=%s; Retry-After=%s; "
                "backoff_aplicado=%.2fs; tentativa=%d/%d.",
                network_slug, status, retry_after, wait, attempt + 1, MAX_RETRIES,
            )
            time.sleep(wait)
    raise RuntimeError("GeckoTerminal: limite de tentativas de rede excedido.")

def gecko_network_id(network):
    """Resolve project network names to the exact GeckoTerminal network slug."""
    return GECKO_NETWORK_IDS.get(network, network)

def fetch_new_pools(network, page=1):
    """Fetch recent pools plus token and DEX relationships from GeckoTerminal."""
    gecko_network = gecko_network_id(network)
    log.info("Bot F coleta iniciada: network_config=%s; gecko_slug=%s; page=%d.",
             network, gecko_network, page)
    payload = _get_json(
        f"{GECKO_BASE}/networks/{gecko_network}/new_pools",
        params={"page": page, "include": "base_token,quote_token,dex"},
    )
    included = {
        item.get("id"): item
        for item in payload.get("included", [])
        if isinstance(item, dict) and item.get("id")
    }
    pools = []
    for item in payload.get("data", []):
        attrs = item.get("attributes") or {}
        rel = item.get("relationships") or {}
        base_id = ((rel.get("base_token") or {}).get("data") or {}).get("id")
        quote_id = ((rel.get("quote_token") or {}).get("data") or {}).get("id")
        dex_id = ((rel.get("dex") or {}).get("data") or {}).get("id")
        base = included.get(base_id, {}).get("attributes") or {}
        quote = included.get(quote_id, {}).get("attributes") or {}
        dex = included.get(dex_id, {}).get("attributes") or {}
        pools.append({
            "pool_id": item.get("id"),
            "pool_address": attrs.get("address"),
            "created_at": attrs.get("pool_created_at"),
            "liquidity_usd": attrs.get("reserve_in_usd"),
            "volume_24h_usd": (attrs.get("volume_usd") or {}).get("h24"),
            "txns_24h": (attrs.get("transactions") or {}).get("h24"),
            "base": base,
            "quote": quote,
            "dex": dex.get("name") or dex_id or "N/D",
            "base_price_usd": attrs.get("base_token_price_usd"),
            "quote_price_usd": attrs.get("quote_token_price_usd"),
            "network": gecko_network,
        })
    log.info("Bot F coleta concluída: network_config=%s; gecko_slug=%s; pools_recebidos=%d.",
             network, gecko_network, len(pools))
    return pools

def _number(value):
    try:
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            return None
        return number
    except (TypeError, ValueError):
        return None

def _created_at(value):
    if not value:
        return None
    try:
        raw = str(value)
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None

def _select_token(pool, quote_symbols):
    base = pool.get("base") or {}
    quote = pool.get("quote") or {}
    base_symbol = str(base.get("symbol") or "").upper()
    quote_symbol = str(quote.get("symbol") or "").upper()
    stable = {str(x).upper() for x in quote_symbols}
    selected = quote if base_symbol in stable else base
    price = pool.get("quote_price_usd") if selected is quote else pool.get("base_price_usd")
    address = str(selected.get("address") or "").strip()
    if not re.fullmatch(r"0x[a-fA-F0-9]{40}", address):
        return None
    return {
        "address": address.lower(),
        "symbol": re.sub(r"[^A-Za-z0-9._-]", "", str(selected.get("symbol") or "N/D"))[:24] or "N/D",
        "name": str(selected.get("name") or "Token sem nome"),
        "price_usd": _number(price),
    }

def _market_eligibility(pool, cfg, now=None):
    now = now or datetime.now(timezone.utc)
    created = _created_at(pool.get("created_at"))
    if created is None:
        return False, "data de criação ausente ou inválida"
    age_minutes = (now - created).total_seconds() / 60
    settings = cfg["launches"]
    if age_minutes < float(settings["min_pool_age_minutes"]):
        return False, "pool recém-criado; aguardando maturação mínima"
    if age_minutes > float(settings["max_pool_age_hours"]) * 60:
        return False, "pool fora da janela de lançamento"
    liquidity = _number(pool.get("liquidity_usd"))
    volume = _number(pool.get("volume_24h_usd"))
    txns = pool.get("txns_24h") or {}
    txn_count = None
    if isinstance(txns, dict):
        buys = _number(txns.get("buys"))
        sells = _number(txns.get("sells"))
        if buys is not None and sells is not None:
            txn_count = buys + sells
    if liquidity is None or liquidity < float(settings["min_liquidity_usd"]):
        return False, "liquidez abaixo do mínimo ou não informada"
    if volume is None or volume < float(settings["min_volume_24h_usd"]):
        return False, "volume de 24h abaixo do mínimo ou não informado"
    if txn_count is None or txn_count < int(settings["min_txns_24h"]):
        return False, "número de transações de 24h abaixo do mínimo ou não informado"
    return True, "filtros básicos de mercado aprovados"

def _load_seen(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return set(data if isinstance(data, list) else [])
    except (OSError, ValueError, TypeError):
        return set()

def _save_seen(path, seen, limit=5000):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(sorted(seen)[-limit:], ensure_ascii=False), encoding="utf-8")

def release_seen_token(cfg, alert):
    """Allow a failed Telegram delivery to be retried on the next scheduled cycle."""
    token_address = alert.get("token_address")
    chain_id = alert.get("chain_id")
    if not token_address or not chain_id:
        return
    state_path = Path(cfg["_root"]) / cfg["launches"]["seen_state_file"]
    seen = _load_seen(state_path)
    seen.discard(f"{chain_id}:{str(token_address).lower()}")
    _save_seen(state_path, seen)


def scan_new_launches(cfg):
    """Return only screened candidates; no market order is ever submitted."""
    env = cfg["_env"]
    if not env.get("goplus_enabled"):
        raise RuntimeError("Bot F bloqueado: GOPLUS_ENABLED não está habilitado.")
    settings = cfg["launches"]
    now = datetime.now(timezone.utc)
    state_path = Path(cfg["_root"]) / settings["seen_state_file"]
    seen = _load_seen(state_path)
    alerts = []
    examined = 0
    checked_this_cycle = set()
    network_summary = {}
    if bool(env.get("goplus_app_key")) != bool(env.get("goplus_app_secret")):
        raise RuntimeError("Configure GOPLUS_APP_KEY e GOPLUS_APP_SECRET em conjunto.")
    access_token = None
    if env.get("goplus_app_key") and env.get("goplus_app_secret"):
        access_token = get_goplus_access_token(env["goplus_app_key"], env["goplus_app_secret"])

    for network in settings["networks"]:
        network_slug = gecko_network_id(network)
        network_summary[network] = {"gecko_slug": network_slug, "pools": 0, "market_eligible": 0,
                                    "security_checked": 0, "approved": 0, "status": "started"}
        chain_id = NETWORKS.get(network)
        if not chain_id:
            log.warning("Bot F: rede sem mapeamento GoPlus, ignorada: %s", network)
            continue
        try:
            pools = fetch_new_pools(network)
            network_summary[network]["pools"] = len(pools)
            network_summary[network]["status"] = "collected"
        except Exception as exc:
            network_summary[network]["status"] = f"collection_error:{type(exc).__name__}"
            log.exception("Bot F: falha ao consultar GeckoTerminal network_config=%s gecko_slug=%s",
                          network, network_slug)
            continue
        finally:
            # Keep a fixed inter-network pause even when one network fails.
            time.sleep(REQUEST_INTERVAL_SECONDS)

        for pool in pools:
            token = _select_token(pool, settings["quote_symbols"])
            if not token:
                continue
            identity = f"{chain_id}:{token['address']}"
            if identity in seen or identity in checked_this_cycle:
                continue
            eligible, market_reason = _market_eligibility(pool, cfg, now)
            if not eligible:
                continue
            network_summary[network]["market_eligible"] += 1
            examined += 1
            checked_this_cycle.add(identity)
            if examined > int(settings["max_security_checks_per_cycle"]):
                log.warning("Bot F: limite de verificações GoPlus por ciclo atingido.")
                break
            network_summary[network]["security_checked"] += 1
            try:
                security = goplus_token_security(
                    chain_id, token["address"],
                    env.get("goplus_app_key", ""), env.get("goplus_app_secret", ""),
                    access_token=access_token,
                )
                assessment = evaluate_launch_security(
                    security, max_tax=float(settings["max_buy_sell_tax"])
                )
            except Exception:
                log.exception("Bot F: GoPlus indisponível para %s:%s", network, token["address"])
                # Do not mark as seen: a later cycle may obtain a valid security response.
                continue

            if assessment["status"] == "rejected":
                seen.add(identity)
            if assessment["status"] != "eligible_for_watchlist":
                log.info(
                    "Bot F rejeitado/revisão: %s %s — %s",
                    network, token["address"], "; ".join(assessment["flags"]),
                )
                continue

            seen.add(identity)
            created = _created_at(pool.get("created_at"))
            network_summary[network]["approved"] += 1
            alerts.append({
                "agent": "Bot F — Novos lançamentos",
                "symbol": token["symbol"],
                "timeframe": "Lançamento DEX",
                "price": token["price_usd"],
                "signal": "CANDIDATO A ANÁLISE — NÃO É RECOMENDAÇÃO",
                "reason": (
                    f"Novo pool detectado na rede {network}; DEX: {pool.get('dex')}. "
                    f"Contrato: {token['address']}. "
                    f"Liquidez observada: US$ {_number(pool.get('liquidity_usd')):,.2f}. "
                    f"Volume 24h: US$ {_number(pool.get('volume_24h_usd')):,.2f}. "
                    f"Transações 24h: {int(pool['txns_24h']['buys']) + int(pool['txns_24h']['sells'])}. "
                    f"GoPlus: verificações obrigatórias sem alerta crítico; "
                    f"campos adicionais: {', '.join(assessment['notes']) or 'sem observação adicional'}."
                ),
                "change_pct": None, "relative_volume": None, "rsi": None, "atr": None,
                "confidence": "não calibrada",
                "risk_note": (
                    "Triagem automatizada preliminar. GoPlus não certifica legitimidade nem rentabilidade; "
                    "liquidez pode ser retirada, preços podem ser manipulados e a cobertura pode ser incompleta. "
                    "Exige análise manual, verificação de LP, holders, equipe, tokenomics e execução de venda. "
                    "Sem compra ou execução automática."
                ),
                "candle_time_utc": now.isoformat(),
                "pool_created_at_utc": created.isoformat() if created else None,
                "pool_url": f"https://www.geckoterminal.com/{gecko_network_id(network)}/pools/{pool.get('pool_address')}",
                "token_address": token["address"],
                "network": network,
                "chain_id": chain_id,
                "liquidity_usd": _number(pool.get("liquidity_usd")),
                "volume_24h_usd": _number(pool.get("volume_24h_usd")),
                "goplus_status": assessment["status"],
                "goplus_flags": assessment["flags"],
                "market_filter": market_reason,
            })
    _save_seen(state_path, seen)
    for network, stats in network_summary.items():
        log.info("Bot F resumo rede: network_config=%s; gecko_slug=%s; status=%s; pools=%d; "
                 "market_eligible=%d; security_checked=%d; approved=%d.",
                 network, stats["gecko_slug"], stats["status"], stats["pools"],
                 stats["market_eligible"], stats["security_checked"], stats["approved"])
    log.info("Bot F resumo ciclo: redes=%d; pools=%d; verificações_GoPlus=%d; aprovados=%d.",
             len(network_summary), sum(x["pools"] for x in network_summary.values()),
             sum(x["security_checked"] for x in network_summary.values()), len(alerts))
    return alerts
