"""Discovery and fail-closed screening of newly created EVM DEX pools."""
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re

import requests

from .security import goplus_token_security, evaluate_launch_security

log = logging.getLogger("dot_cripto.bot_f")
GECKO_BASE = "https://api.geckoterminal.com/api/v2"
TIMEOUT = 15

NETWORKS = {
    "eth": "1",
    "base": "8453",
    "bsc": "56",
    "arbitrum": "42161",
    "polygon_pos": "137",
    "optimism": "10",
    "avalanche": "43114",
}

def _get_json(url, params=None):
    response = requests.get(
        url, params=params, headers={"Accept": "application/json"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return response.json()

def fetch_new_pools(network, page=1):
    """Fetch recent pools plus token and DEX relationships from GeckoTerminal."""
    payload = _get_json(
        f"{GECKO_BASE}/networks/{network}/new_pools",
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
            "network": network,
        })
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
        "symbol": str(selected.get("symbol") or "N/D"),
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

    for network in settings["networks"]:
        chain_id = NETWORKS.get(network)
        if not chain_id:
            log.warning("Bot F: rede sem mapeamento GoPlus, ignorada: %s", network)
            continue
        try:
            pools = fetch_new_pools(network)
        except Exception:
            log.exception("Bot F: falha ao consultar GeckoTerminal para %s", network)
            continue

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
            examined += 1
            checked_this_cycle.add(identity)
            if examined > int(settings["max_security_checks_per_cycle"]):
                log.warning("Bot F: limite de verificações GoPlus por ciclo atingido.")
                break
            try:
                security = goplus_token_security(
                    chain_id, token["address"],
                    env.get("goplus_app_key", ""), env.get("goplus_app_secret", ""),
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
                "pool_url": f"https://www.geckoterminal.com/{network}/pools/{pool.get('pool_address')}",
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
    return alerts
