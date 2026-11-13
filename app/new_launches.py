"""Discovery and fail-closed screening of newly created EVM DEX pools."""
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import json
import logging
import math
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
REQUEST_INTERVAL_SECONDS = 12
MAX_RETRIES = 3
RETRY_JITTER_SECONDS = 2
MIN_RETRY_AFTER_SECONDS = REQUEST_INTERVAL_SECONDS
API_METRICS_PATH = Path(__file__).resolve().parents[1] / "data" / "bot_f_api_metrics.jsonl"
POOL_DIAGNOSTICS_PATH = Path(__file__).resolve().parents[1] / "data" / "bot_f_pool_diagnostics.json"
LIQUIDITY_CATEGORIES = ("below_minimum", "missing", "invalid", "zero", "passed")
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


def _record_api_metric(url, status, attempt, retry_after=None, wait_seconds=0,
                       params=None, latency_ms=None, error_type=None):
    if not isinstance(status, int):
        status = None
    network_slug = url.split("/networks/")[-1].split("/")[0] if "/networks/" in url else "N/D"
    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "network_slug": network_slug,
        "endpoint": url.split(GECKO_BASE, 1)[-1].split("?", 1)[0],
        "page": (params or {}).get("page"),
        "status": status,
        "attempt": attempt + 1,
        "retry_after_seconds": retry_after,
        "wait_seconds": round(float(wait_seconds), 3),
        "latency_ms": round(float(latency_ms), 2) if latency_ms is not None else None,
        "error_type": error_type,
    }
    API_METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with API_METRICS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + chr(10))


def _get_json(url, params=None):
    """GET JSON with bounded backoff and per-response observability."""
    retryable_statuses = {429, 500, 502, 503, 504}
    for attempt in range(MAX_RETRIES + 1):
        started = time.monotonic()
        try:
            response = requests.get(
                url, params=params,
                headers={"Accept": GECKO_ACCEPT, "User-Agent": "DotCripto-BotF/1.0"},
                timeout=TIMEOUT,
            )
            latency_ms = (time.monotonic() - started) * 1000
            response.raise_for_status()
            _record_api_metric(url, response.status_code, attempt, params=params,
                               latency_ms=latency_ms)
            return response.json()
        except requests.exceptions.HTTPError as exc:
            latency_ms = (time.monotonic() - started) * 1000
            response = exc.response
            status = getattr(response, "status_code", None)
            retry_after = _retry_after_seconds(response) if status == 429 else None
            if status not in retryable_statuses or attempt >= MAX_RETRIES:
                _record_api_metric(url, status, attempt, retry_after, 0, params,
                                   latency_ms, type(exc).__name__)
                raise
            exponential = REQUEST_INTERVAL_SECONDS * (2 ** attempt)
            wait = max(retry_after, MIN_RETRY_AFTER_SECONDS) if retry_after is not None else (
                exponential + random.uniform(0, RETRY_JITTER_SECONDS)
            )
            network_slug = url.split("/networks/")[-1].split("/")[0] if "/networks/" in url else "N/D"
            _record_api_metric(url, status, attempt, retry_after, wait, params,
                               latency_ms, type(exc).__name__)
            log.warning(
                "GeckoTerminal rate_limit_retry network=%s status=%s; Retry-After=%s; "
                "backoff_aplicado=%.2fs; tentativa=%d/%d.",
                network_slug, status, retry_after, wait, attempt + 1, MAX_RETRIES,
            )
            time.sleep(wait)
        except requests.exceptions.RequestException as exc:
            latency_ms = (time.monotonic() - started) * 1000
            if attempt >= MAX_RETRIES:
                _record_api_metric(url, None, attempt, None, 0, params,
                                   latency_ms, type(exc).__name__)
                raise
            wait = REQUEST_INTERVAL_SECONDS * (2 ** attempt) + random.uniform(0, RETRY_JITTER_SECONDS)
            _record_api_metric(url, None, attempt, None, wait, params,
                               latency_ms, type(exc).__name__)
            log.warning(
                "GeckoTerminal request_retry network=%s error=%s; "
                "backoff_aplicado=%.2fs; tentativa=%d/%d.",
                url.split("/networks/")[-1].split("/")[0] if "/networks/" in url else "N/D",
                type(exc).__name__, wait, attempt + 1, MAX_RETRIES,
            )
            time.sleep(wait)
    raise RuntimeError("GeckoTerminal: limite de tentativas de rede excedido.")


def gecko_network_id(network):
    """Resolve project network names to the exact GeckoTerminal network slug."""
    return GECKO_NETWORK_IDS.get(network, network)

def search_pools_by_resource_id(network, pool_id):
    """Search GeckoTerminal by the exact original resource ID; never derive an address."""
    gecko_network = gecko_network_id(network)
    return _get_json(
        f"{GECKO_BASE}/search/pools",
        params={"query": str(pool_id), "network": gecko_network},
    )


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
        # GeckoTerminal resource IDs use "<network>_<pool-address>". Prefer
        # the explicit attribute, but recover a missing/invalid address only when
        # the resource ID has the exact network prefix and a valid EVM address.
        raw_pool_address = attrs.get("address")
        pool_address_source = "api_attribute"
        if not re.fullmatch(r"0x[a-fA-F0-9]{40}", str(raw_pool_address or "")):
            resource_id = str(item.get("id") or "")
            prefix = f"{gecko_network}_"
            candidate = resource_id[len(prefix):] if resource_id.startswith(prefix) else ""
            if re.fullmatch(r"0x[a-fA-F0-9]{40}", candidate):
                raw_pool_address = candidate
                pool_address_source = "resource_id_fallback"
            else:
                raw_pool_address = None
                pool_address_source = "missing_or_invalid"

        pools.append({
            "pool_id": item.get("id"),
            "pool_address": raw_pool_address,
            "pool_address_source": pool_address_source,
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

def fetch_current_pool_liquidity(network, pool_address):
    """Refresh the exact pool's reserve immediately before Telegram delivery."""
    address = str(pool_address or "").strip()
    if not re.fullmatch(r"0x[a-fA-F0-9]{40}", address):
        raise ValueError("endereço do pool ausente ou inválido")
    slug = gecko_network_id(network)
    payload = _get_json(
        f"{GECKO_BASE}/networks/{slug}/pools/{address}",
        params=None,
    )
    data = payload.get("data") if isinstance(payload, dict) else None
    attrs = data.get("attributes") if isinstance(data, dict) else None
    if not isinstance(attrs, dict):
        raise ValueError("GeckoTerminal não retornou atributos válidos para o pool")

    # Fail closed unless the response itself identifies the exact requested pool.
    returned_address = str(attrs.get("address") or "").strip()
    if not re.fullmatch(r"0x[a-fA-F0-9]{40}", returned_address):
        raise ValueError("GeckoTerminal não confirmou um endereço de pool válido na resposta")
    if returned_address.lower() != address.lower():
        raise ValueError("GeckoTerminal retornou um endereço diferente do pool solicitado")

    resource_id = str(data.get("id") or "").strip()
    if resource_id:
        prefix = f"{slug}_"
        resource_address = resource_id[len(prefix):] if resource_id.startswith(prefix) else ""
        if (
            not re.fullmatch(r"0x[a-fA-F0-9]{40}", resource_address)
            or resource_address.lower() != address.lower()
        ):
            raise ValueError("ID do recurso GeckoTerminal não corresponde à rede/pool solicitados")

    classification, liquidity = _classify_liquidity(attrs.get("reserve_in_usd"), 25000)
    return {
        "classification": classification,
        "liquidity_usd": liquidity,
        "provider": "GeckoTerminal",
        "pool_address": returned_address.lower(),
        "pool_id": resource_id or None,
        "network_id": slug,
    }


def independent_lp_proof_is_valid(alert):
    """Accept only a complete BSC/UNCX proof bound to this exact alert.

    A dictionary containing plausible-looking fields is not enough: the proof
    must identify the same network, chain and pool, the allowlisted PancakeSwap
    V2 factory and UNCX locker, and a pinned block/runtime hash. Other networks
    remain blocked until their own reviewed lock adapters are allowlisted.
    """
    proof = alert.get("independent_lp_proof")
    if not isinstance(proof, dict) or proof.get("status") != "verified":
        return False
    try:
        liquidity = float(proof.get("liquidity_usd"))
        protected = float(proof.get("protected_fraction"))
        block_number = int(proof.get("reserve_block_number"))
    except (TypeError, ValueError, OverflowError):
        return False

    expected_pool = str(alert.get("pool_address") or "").lower()
    proof_pool = str(proof.get("pool_address") or "").lower()
    expected_chain = str(alert.get("chain_id") or "")
    proof_chain = str(proof.get("chain_id") or "")
    txs = proof.get("lock_transactions")
    if not isinstance(txs, list) or not txs:
        return False
    if not all(
        isinstance(item, dict)
        and re.fullmatch(r"0x[a-fA-F0-9]{64}", str(item.get("transaction_hash") or ""))
        for item in txs
    ):
        return False

    return (
        math.isfinite(liquidity)
        and math.isfinite(protected)
        and liquidity >= 25000
        and protected >= 0.999
        and alert.get("network") == "bsc"
        and proof.get("network") == "bsc"
        and expected_chain == "56"
        and proof_chain == expected_chain
        and re.fullmatch(r"0x[a-f0-9]{40}", expected_pool) is not None
        and proof_pool == expected_pool
        and str(proof.get("pool_factory") or "").lower() == "0xca143ce32fe78f1f7019d7d551a6402fc5350c73"
        and str(proof.get("lock_contract") or "").lower() == "0xc765bddb93b0d1c1a88282ba0fa6b2d00e3e0c83"
        and str(proof.get("source") or "") == "EVM_JSON_RPC"
        and block_number > 0
        and re.fullmatch(r"0x[a-fA-F0-9]{64}", str(proof.get("reserve_block_hash") or "")) is not None
        and re.fullmatch(r"0x[a-fA-F0-9]{64}", str(proof.get("locker_runtime_code_hash") or "")) is not None
    )


def _number(value):
    try:
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            return None
        return number
    except (TypeError, ValueError):
        return None

def _classify_liquidity(value, minimum):
    """Classify provider liquidity data without changing the fail-closed threshold."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return "missing", None
    parsed = _number(value)
    if parsed is None:
        return "invalid", None
    if parsed == 0:
        return "zero", parsed
    if parsed < float(minimum):
        return "below_minimum", parsed
    return "passed", parsed


def _write_pool_diagnostics(records, generated_at, minimum_liquidity, network_dex_summary):
    """Persist every returned pool and grouped diagnostics for the current run."""
    counts = {category: 0 for category in LIQUIDITY_CATEGORIES}
    for record in records:
        counts[record["liquidity_classification"]] += 1
    payload = {
        "generated_at_utc": generated_at.isoformat(),
        "provider": "GeckoTerminal",
        "liquidity_minimum_usd": float(minimum_liquidity),
        "liquidity_classification_counts": counts,
        "network_dex_summary": network_dex_summary,
        "pools": records,
    }
    POOL_DIAGNOSTICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = POOL_DIAGNOSTICS_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(POOL_DIAGNOSTICS_PATH)
    log.info("Bot F diagnóstico individual: arquivo=%s; pools=%d; liquidez=%s",
             POOL_DIAGNOSTICS_PATH, len(records), json.dumps(counts, ensure_ascii=False, sort_keys=True))


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

def _pool_url(network, pool):
    """Return a GeckoTerminal pool URL only when a valid EVM pool address exists."""
    address = str(pool.get("pool_address") or "").strip()
    if not re.fullmatch(r"0x[a-fA-F0-9]{40}", address):
        return None
    return f"https://www.geckoterminal.com/{gecko_network_id(network)}/pools/{address}"


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

def _holder_count_review_reason(security, min_holder_count):
    """Return a review reason when holder distribution is unknown or too concentrated."""
    raw = security.get("holder_count") if isinstance(security, dict) else None
    try:
        count_value = float(raw)
        if not count_value.is_integer() or count_value < 0:
            raise ValueError()
    except (TypeError, ValueError, OverflowError):
        return "holder_count: não informado ou inválido; revisão manual obrigatória"
    count = int(count_value)
    if count < int(min_holder_count):
        return f"holder_count={count}: abaixo do mínimo configurado ({int(min_holder_count)}); revisão manual obrigatória"
    return None


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
    liquidity_class, liquidity = _classify_liquidity(
        pool.get("liquidity_usd"), settings["min_liquidity_usd"]
    )
    volume = _number(pool.get("volume_24h_usd"))
    txns = pool.get("txns_24h") or {}
    txn_count = None
    if isinstance(txns, dict):
        buys = _number(txns.get("buys"))
        sells = _number(txns.get("sells"))
        if buys is not None and sells is not None:
            txn_count = buys + sells
    if liquidity_class != "passed":
        reasons = {
            "below_minimum": "liquidez abaixo do mínimo",
            "missing": "liquidez ausente no fornecedor",
            "invalid": "liquidez inválida no fornecedor",
            "zero": "liquidez igual a zero no fornecedor",
        }
        return False, reasons[liquidity_class]
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

def _load_review_state(path):
    """Load temporary review decisions without changing the permanent seen set."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_review_state(path, state):
    """Persist review timestamps atomically so scheduled runs can share cooldowns."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)


def _review_recheck_due(identity, review_state, now, interval_minutes=30):
    """Recheck a review-required token only after its configured cooldown."""
    record = review_state.get(identity)
    checked_at = _created_at(record.get("checked_at") if isinstance(record, dict) else record)
    if checked_at is None:
        return True
    age_seconds = (now - checked_at).total_seconds()
    if age_seconds < -300:
        return True
    return age_seconds >= max(1.0, float(interval_minutes)) * 60


def release_seen_token(cfg, alert):
    """Release a failed alert for retry without losing pending-review state."""
    token_address = alert.get("token_address")
    chain_id = alert.get("chain_id")
    if not token_address or not chain_id:
        return
    identity = f"{chain_id}:{str(token_address).lower()}"
    state_path = Path(cfg["_root"]) / cfg["launches"]["seen_state_file"]
    seen = _load_seen(state_path)
    seen.discard(identity)
    _save_seen(state_path, seen)

    if alert.get("discovery_status") == "PENDENTE DE REVISÃO — NÃO APROVADO":
        review_path = Path(cfg["_root"]) / cfg["launches"].get(
            "review_state_file", "data/bot_f_review_state.json"
        )
        review_state = _load_review_state(review_path)
        record = review_state.get(identity)
        if isinstance(record, dict):
            record["discovery_alert_sent"] = False
            _save_review_state(review_path, review_state)


def _build_launch_alert(network, chain_id, token, pool, now, assessment,
                        pending=False, security_error=None, market_reason=None):
    created = _created_at(pool.get("created_at"))
    if pending:
        status = "PENDENTE DE REVISÃO — NÃO APROVADO"
        signal = "NOVO POOL DETECTADO — PENDENTE DE REVISÃO DE SEGURANÇA"
        security_text = (
            f"GoPlus indisponível: {security_error}" if security_error
            else "GoPlus identificou dados insuficientes/itens que exigem revisão manual"
        )
        risk_note = (
            "ATENÇÃO: este pool passou apenas pelos filtros básicos de mercado. "
            "A validação de contrato, honeypot, taxas, holders e bloqueio da liquidez "
            "não foi concluída. NÃO É ELEGÍVEL, NÃO É RECOMENDAÇÃO e não deve ser "
            "interpretado como seguro. Aguarde uma atualização de triagem; sem compra "
            "ou execução automática."
        )
    else:
        status = "ELEGÍVEL PARA WATCHLIST — NÃO É RECOMENDAÇÃO"
        signal = "CANDIDATO A ANÁLISE — SEGURANÇA VALIDADA"
        security_text = (
            "GoPlus: verificações obrigatórias completas, sem bloqueadores; "
            f"observações: {', '.join(assessment.get('notes') or []) or 'nenhuma'}"
        )
        risk_note = (
            "Triagem automatizada preliminar. GoPlus não certifica legitimidade nem rentabilidade; "
            "liquidez pode ser retirada, preços podem ser manipulados e a cobertura pode ser incompleta. "
            "Exige análise manual adicional. Sem compra ou execução automática."
        )

    liquidity = _number(pool.get("liquidity_usd"))
    volume = _number(pool.get("volume_24h_usd"))
    txns = pool.get("txns_24h") or {}
    buys = _number(txns.get("buys")) if isinstance(txns, dict) else None
    sells = _number(txns.get("sells")) if isinstance(txns, dict) else None
    txn_count = int(buys + sells) if buys is not None and sells is not None else None
    reason_parts = [
        f"Novo pool detectado na rede {network}; DEX: {pool.get('dex')}.",
        f"Contrato: {token['address']}.",
        f"Liquidez observada: US$ {liquidity:,.2f}." if liquidity is not None else "Liquidez: N/D.",
        f"Volume 24h: US$ {volume:,.2f}." if volume is not None else "Volume 24h: N/D.",
        f"Transações 24h: {txn_count}." if txn_count is not None else "Transações 24h: N/D.",
        security_text + ".",
    ]
    if pending:
        reason_parts.append(
            "Motivos para revisão: " + "; ".join(assessment.get("flags") or ["validação de segurança não concluída"]) + "."
        )
    return {
        "agent": "Bot F — Descoberta de pools",
        "symbol": token["symbol"],
        "timeframe": "Lançamento DEX",
        "price": token["price_usd"],
        "signal": signal,
        "discovery_status": status,
        "reason": " ".join(reason_parts),
        "confidence": "não calibrada",
        "risk_note": risk_note,
        "candle_time_utc": now.isoformat(),
        "pool_created_at_utc": created.isoformat() if created else None,
        "pool_url": _pool_url(network, pool),
        "pool_id": pool.get("pool_id"),
        "pool_address": pool.get("pool_address"),
        "token_address": token["address"],
        "network": network,
        "chain_id": chain_id,
        "liquidity_usd": liquidity,
        "volume_24h_usd": volume,
        "goplus_status": assessment.get("status", "unavailable"),
        "goplus_flags": list(assessment.get("flags") or []),
        "market_filter": market_reason or "filtros básicos de mercado aprovados",
    }


def scan_new_launches(cfg):
    """Discover basic-market-qualified pools and separately report security outcomes."""
    env = cfg["_env"]
    if not env.get("goplus_enabled"):
        raise RuntimeError("Bot F bloqueado: GOPLUS_ENABLED não está habilitado.")
    settings = cfg["launches"]
    now = datetime.now(timezone.utc)
    state_path = Path(cfg["_root"]) / settings["seen_state_file"]
    seen = _load_seen(state_path)
    review_state_path = Path(cfg["_root"]) / settings.get(
        "review_state_file", "data/bot_f_review_state.json"
    )
    review_state = _load_review_state(review_state_path)
    review_recheck_minutes = max(1.0, float(settings.get("review_recheck_minutes", 30)))
    review_cutoff = now - timedelta(days=7)
    for identity, record in list(review_state.items()):
        checked_at = _created_at(record.get("checked_at") if isinstance(record, dict) else record)
        if checked_at is None or checked_at < review_cutoff:
            review_state.pop(identity, None)

    alerts = []
    examined = 0
    checked_this_cycle = set()
    network_summary = {}
    pool_diagnostic_records = []
    network_dex_summary = {}
    diagnostics = {
        "pools_received": 0,
        "invalid_token_or_address": 0,
        "already_seen": 0,
        "review_cooldown": 0,
        "market_eligible": 0,
        "market_rejected": {"age": 0, "liquidity": 0, "volume": 0, "transactions": 0, "other": 0},
        "market_rejection_reasons": {},
        "goplus_attempted": 0,
        "goplus_errors": 0,
        "goplus_rejected": 0,
        "goplus_review_required": 0,
        "goplus_approved": 0,
        "goplus_rejection_reasons": {},
        "lp_findings": {},
        "security_limit_skipped": 0,
        "pending_discovery_alerts": 0,
        "approved_alerts": 0,
    }

    if bool(env.get("goplus_app_key")) != bool(env.get("goplus_app_secret")):
        raise RuntimeError("Configure GOPLUS_APP_KEY e GOPLUS_APP_SECRET em conjunto.")
    access_token = None
    if env.get("goplus_app_key") and env.get("goplus_app_secret"):
        access_token = get_goplus_access_token(env["goplus_app_key"], env["goplus_app_secret"])

    max_security_checks = max(0, int(settings["max_security_checks_per_cycle"]))
    for network in settings["networks"]:
        network_slug = gecko_network_id(network)
        network_summary[network] = {
            "gecko_slug": network_slug, "pools": 0, "market_eligible": 0,
            "security_checked": 0, "security_errors": 0, "rejected": 0,
            "review_required": 0, "approved": 0, "pending_alerts": 0,
            "status": "started",
        }
        chain_id = NETWORKS.get(network)
        if not chain_id:
            network_summary[network]["status"] = "unsupported_network"
            log.warning("Bot F: rede sem mapeamento GoPlus, ignorada: %s", network)
            continue
        try:
            pools = fetch_new_pools(network)
            network_summary[network]["pools"] = len(pools)
            diagnostics["pools_received"] += len(pools)
            network_summary[network]["status"] = "collected"
        except Exception as exc:
            network_summary[network]["status"] = f"collection_error:{type(exc).__name__}"
            log.exception("Bot F: falha ao consultar GeckoTerminal network_config=%s gecko_slug=%s",
                          network, network_slug)
            continue
        finally:
            time.sleep(REQUEST_INTERVAL_SECONDS)

        for pool in pools:
            liquidity_class, parsed_liquidity = _classify_liquidity(
                pool.get("liquidity_usd"), settings["min_liquidity_usd"]
            )
            dex_name = str(pool.get("dex") or "N/D")
            group_key = f"{network_slug}|{dex_name}"
            group = network_dex_summary.setdefault(group_key, {
                "network": network_slug, "dex": dex_name, "pools": 0,
                **{category: 0 for category in LIQUIDITY_CATEGORIES},
            })
            group["pools"] += 1
            group[liquidity_class] += 1
            base = pool.get("base") if isinstance(pool.get("base"), dict) else {}
            quote = pool.get("quote") if isinstance(pool.get("quote"), dict) else {}
            pool_record = {
                "network": network_slug,
                "dex": dex_name,
                "pool_id": pool.get("pool_id"),
                "pool_address": pool.get("pool_address"),
                "created_at": pool.get("created_at"),
                "base_symbol": base.get("symbol"),
                "base_address": base.get("address"),
                "quote_symbol": quote.get("symbol"),
                "quote_address": quote.get("address"),
                "liquidity_raw_usd": pool.get("liquidity_usd"),
                "liquidity_parsed_usd": parsed_liquidity,
                "liquidity_classification": liquidity_class,
                "liquidity_threshold_usd": float(settings["min_liquidity_usd"]),
                "volume_24h_usd_raw": pool.get("volume_24h_usd"),
                "transactions_24h_raw": pool.get("txns_24h"),
                "market_filter_result": "not_evaluated",
                "market_filter_reason": None,
            }
            pool_diagnostic_records.append(pool_record)
            token = _select_token(pool, settings["quote_symbols"])
            if not token:
                diagnostics["invalid_token_or_address"] += 1
                continue
            identity = f"{chain_id}:{token['address']}"
            if identity in seen or identity in checked_this_cycle:
                diagnostics["already_seen"] += 1
                continue
            if not _review_recheck_due(identity, review_state, now, review_recheck_minutes):
                diagnostics["review_cooldown"] += 1
                continue

            eligible, market_reason = _market_eligibility(pool, cfg, now)
            if not eligible:
                pool_record["market_filter_result"] = "rejected"
                pool_record["market_filter_reason"] = market_reason
                reason = market_reason.lower()
                if "pool" in reason or "data de criação" in reason:
                    category = "age"
                elif "liquidez" in reason:
                    category = "liquidity"
                elif "volume" in reason:
                    category = "volume"
                elif "transaç" in reason:
                    category = "transactions"
                else:
                    category = "other"
                diagnostics["market_rejected"][category] += 1
                diagnostics["market_rejection_reasons"][market_reason] = (
                    diagnostics["market_rejection_reasons"].get(market_reason, 0) + 1
                )
                continue

            pool_record["market_filter_result"] = "passed"
            pool_record["market_filter_reason"] = market_reason
            diagnostics["market_eligible"] += 1
            network_summary[network]["market_eligible"] += 1
            checked_this_cycle.add(identity)

            if examined >= max_security_checks:
                diagnostics["security_limit_skipped"] += 1
                log.warning(
                    "Bot F: limite GoPlus atingido; pool básico elegível ficou sem validação: %s:%s",
                    network, token["address"],
                )
                prior = review_state.get(identity, {})
                already_notified = bool(prior.get("discovery_alert_sent")) if isinstance(prior, dict) else False
                review_state[identity] = {
                    "checked_at": now.isoformat(),
                    "status": "security_check_deferred",
                    "flags": ["limite de consultas GoPlus do ciclo atingido; validação adiada"],
                    "discovery_alert_sent": already_notified,
                }
                if not already_notified:
                    deferred = {
                        "status": "security_check_deferred",
                        "flags": ["limite de consultas GoPlus do ciclo atingido; validação adiada"],
                        "notes": [],
                    }
                    # Pending candidates stay in internal review state only; never Telegram.
                    diagnostics["pending_discovery_alerts"] += 1
                    network_summary[network]["pending_alerts"] += 1
                    review_state[identity]["discovery_alert_sent"] = False
                continue

            examined += 1
            diagnostics["goplus_attempted"] += 1
            network_summary[network]["security_checked"] += 1
            try:
                security = goplus_token_security(
                    chain_id, token["address"],
                    env.get("goplus_app_key", ""), env.get("goplus_app_secret", ""),
                    access_token=access_token,
                )
                assessment = evaluate_launch_security(
                    security, max_tax=float(settings["max_buy_sell_tax"]), require_lp=True
                )
                if assessment["status"] != "rejected":
                    holder_reason = _holder_count_review_reason(
                        security, settings.get("min_holder_count", 10)
                    )
                    if holder_reason:
                        assessment["status"] = "review_required"
                        assessment["flags"].append(holder_reason)
            except Exception as exc:
                diagnostics["goplus_errors"] += 1
                network_summary[network]["security_errors"] += 1
                error_reason = f"{type(exc).__name__}: {str(exc)[:180]}"
                log.exception("Bot F: GoPlus indisponível para %s:%s", network, token["address"])
                prior = review_state.get(identity, {})
                already_notified = bool(prior.get("discovery_alert_sent")) if isinstance(prior, dict) else False
                review_state[identity] = {
                    "checked_at": now.isoformat(),
                    "status": "goplus_unavailable",
                    "flags": [error_reason],
                    "discovery_alert_sent": already_notified,
                }
                if not already_notified:
                    unknown = {"status": "goplus_unavailable", "flags": [error_reason], "notes": []}
                    # GoPlus errors remain visible in diagnostics/review state only.
                    diagnostics["pending_discovery_alerts"] += 1
                    network_summary[network]["pending_alerts"] += 1
                    review_state[identity]["discovery_alert_sent"] = False
                continue

            flags = list(assessment.get("flags") or [])
            for flag in flags:
                diagnostics["goplus_rejection_reasons"][flag] = (
                    diagnostics["goplus_rejection_reasons"].get(flag, 0) + 1
                )
                if "lp" in flag.lower() or "liquidez" in flag.lower():
                    diagnostics["lp_findings"][flag] = diagnostics["lp_findings"].get(flag, 0) + 1

            if assessment["status"] == "rejected":
                diagnostics["goplus_rejected"] += 1
                network_summary[network]["rejected"] += 1
                seen.add(identity)
                review_state.pop(identity, None)
                log.warning(
                    "Bot F bloqueado: %s %s — %s",
                    network, token["address"], "; ".join(flags or ["risco crítico"]),
                )
                continue

            if assessment["status"] != "eligible_for_watchlist":
                diagnostics["goplus_review_required"] += 1
                network_summary[network]["review_required"] += 1
                prior = review_state.get(identity, {})
                already_notified = bool(prior.get("discovery_alert_sent")) if isinstance(prior, dict) else False
                review_state[identity] = {
                    "checked_at": now.isoformat(),
                    "status": "review_required",
                    "flags": flags,
                    "discovery_alert_sent": already_notified,
                }
                log.info(
                    "Bot F pendente de revisão: %s %s — %s",
                    network, token["address"], "; ".join(flags or ["dados insuficientes"]),
                )
                if not already_notified:
                    # LP/holder evidence incomplete: retain internally, suppress Telegram.
                    diagnostics["pending_discovery_alerts"] += 1
                    network_summary[network]["pending_alerts"] += 1
                    review_state[identity]["discovery_alert_sent"] = False
                continue

            diagnostics["goplus_approved"] += 1
            network_summary[network]["approved"] += 1
            seen.add(identity)
            review_state.pop(identity, None)
            alerts.append(_build_launch_alert(
                network, chain_id, token, pool, now, assessment,
                pending=False, market_reason=market_reason,
            ))
            diagnostics["approved_alerts"] += 1

    _write_pool_diagnostics(
        pool_diagnostic_records, now, settings["min_liquidity_usd"], network_dex_summary
    )
    _save_seen(state_path, seen)
    _save_review_state(review_state_path, review_state)
    for network, stats in network_summary.items():
        log.info("Bot F resumo rede: network_config=%s; gecko_slug=%s; %s",
                 network, stats["gecko_slug"], json.dumps(stats, ensure_ascii=False, sort_keys=True))
    log.info("Bot F diagnóstico ciclo: %s", json.dumps(diagnostics, ensure_ascii=False, sort_keys=True))
    log.info(
        "Bot F resumo ciclo: redes=%d; pools=%d; verificações_GoPlus=%d; "
        "pendentes_revisão=%d; aprovados=%d; rejeitados=%d; erros_GoPlus=%d.",
        len(network_summary), diagnostics["pools_received"], diagnostics["goplus_attempted"],
        diagnostics["pending_discovery_alerts"], diagnostics["approved_alerts"],
        diagnostics["goplus_rejected"], diagnostics["goplus_errors"],
    )
    return alerts
