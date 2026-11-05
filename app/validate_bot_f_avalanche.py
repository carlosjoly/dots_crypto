"""Isolated live pagination and data-quality validation for Avalanche pools."""
import json
import logging
import re
import statistics
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .new_launches import (
    REQUEST_INTERVAL_SECONDS,
    _created_at,
    _number,
    fetch_new_pools,
    search_pools_by_resource_id,
    gecko_network_id,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("dot_cripto.bot_f.avalanche_validation")

MAX_PAGES = 3
OUTPUT_PATH = Path("data/bot_f_avalanche_validation.json")
ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
QUALITY_FIELDS = (
    "pool_id",
    "pool_address",
    "created_at",
    "liquidity_usd",
    "volume_24h_usd",
    "txns_24h",
    "base_token_address",
    "quote_token_address",
    "base_token_price_usd",
    "quote_token_price_usd",
)


def _pool_quality(pool, now):
    """Return field-level checks; this is diagnostic, not a token approval."""
    base = pool.get("base") if isinstance(pool.get("base"), dict) else {}
    quote = pool.get("quote") if isinstance(pool.get("quote"), dict) else {}
    created = _created_at(pool.get("created_at"))
    liquidity = _number(pool.get("liquidity_usd"))
    volume = _number(pool.get("volume_24h_usd"))
    base_price = _number(pool.get("base_price_usd"))
    quote_price = _number(pool.get("quote_price_usd"))
    txns = pool.get("txns_24h")
    buys = _number(txns.get("buys")) if isinstance(txns, dict) else None
    sells = _number(txns.get("sells")) if isinstance(txns, dict) else None

    checks = {
        "pool_id": bool(pool.get("pool_id")),
        "pool_address": bool(ADDRESS_RE.fullmatch(str(pool.get("pool_address") or ""))),
        "created_at": created is not None and created <= now,
        "liquidity_usd": liquidity is not None and liquidity >= 0,
        "volume_24h_usd": volume is not None and volume >= 0,
        "txns_24h": buys is not None and sells is not None and buys >= 0 and sells >= 0,
        "base_token_address": bool(ADDRESS_RE.fullmatch(str(base.get("address") or ""))),
        "quote_token_address": bool(ADDRESS_RE.fullmatch(str(quote.get("address") or ""))),
        "base_token_price_usd": base_price is not None and base_price > 0,
        "quote_token_price_usd": quote_price is not None and quote_price > 0,
    }
    age_minutes = (now - created).total_seconds() / 60 if created and created <= now else None
    return checks, age_minutes


def validate():
    now = datetime.now(timezone.utc)
    result = {
        "schema_version": 2,
        "timestamp_utc": now.isoformat(),
        "configured_network": "avalanche",
        "expected_gecko_slug": "avax",
        "resolved_gecko_slug": gecko_network_id("avalanche"),
        "status": "failed",
        "max_pages": MAX_PAGES,
        "pages_completed": 0,
        "pagination_status": "not_started",
        "pagination_complete": False,
        "pools_received": 0,
        "unique_pool_count": 0,
        "duplicate_pool_count": 0,
        "sample_pool_ids": [],
        "address_diagnostics": [],
        "page_results": [],
        "quality_status": "not_evaluated",
        "field_quality": {},
        "complete_record_count": 0,
        "complete_record_pct": None,
        "pool_age_minutes": {},
        "future_created_at_count": 0,
        "spread_data_available": False,
        "limitations": [
            "GeckoTerminal new_pools não fornece livro bid/ask; spread executável não é medido neste teste.",
            "A paginação é limitada a três páginas; atingir o limite não comprova cobertura total da rede.",
            "Qualidade dos campos não equivale a aprovação de mercado nem à triagem GoPlus.",
        ],
    }
    all_pools = []
    seen = set()
    request_error = None

    try:
        for page in range(1, MAX_PAGES + 1):
            if page > 1:
                # Respect the conservative public API cadence between page requests.
                time.sleep(REQUEST_INTERVAL_SECONDS)
            started = time.monotonic()
            pools = fetch_new_pools("avalanche", page=page)
            elapsed_ms = (time.monotonic() - started) * 1000
            page_unique = 0
            page_duplicates = 0
            sample_ids = []

            for index, pool in enumerate(pools):
                pool_id = pool.get("pool_id")
                identity = str(pool_id).strip() if pool_id else ""
                if not identity:
                    address = str(pool.get("pool_address") or "").strip().lower()
                    identity = f"address:{address}" if address else f"missing:{page}:{index}"
                if identity in seen:
                    page_duplicates += 1
                    continue
                seen.add(identity)
                page_unique += 1
                all_pools.append(pool)
                if pool_id and len(sample_ids) < 5:
                    sample_ids.append(pool_id)

            result["pages_completed"] += 1
            result["pools_received"] += len(pools)
            result["duplicate_pool_count"] += page_duplicates
            result["page_results"].append({
                "page": page,
                "pools_received": len(pools),
                "unique_pools": page_unique,
                "duplicates": page_duplicates,
                "elapsed_ms": round(elapsed_ms, 2),
                "sample_pool_ids": sample_ids,
            })
            log.info(
                "Avalanche page=%d pools=%d unique=%d duplicates=%d elapsed_ms=%.2f",
                page, len(pools), page_unique, page_duplicates, elapsed_ms,
            )
            if not pools:
                result["pagination_status"] = "empty_page_reached"
                result["pagination_complete"] = True
                break
        else:
            result["pagination_status"] = "page_limit_reached"
    except Exception as exc:
        request_error = exc
        result["pagination_status"] = "partial_failure" if result["pages_completed"] else "failed"
        result["error"] = f"{type(exc).__name__}: {str(exc)[:240]}"
        log.exception("Avalanche pagination validation failed.")

    # Exact-ID lookup only; never infer a pool address from a hash-shaped ID.
    for pool in all_pools:
        pool_id = str(pool.get("pool_id") or "")
        suffix = pool_id[len("avax_"):] if pool_id.startswith("avax_") else ""
        suffix_hex = suffix[2:] if suffix.startswith("0x") else ""
        suffix_len = len(suffix_hex) if re.fullmatch(r"[0-9a-fA-F]+", suffix_hex) else None
        current_address = pool.get("pool_address")
        source = pool.get("pool_address_source") or (
            "api_attribute" if ADDRESS_RE.fullmatch(str(current_address or "")) else "missing_or_invalid"
        )
        diag = {
            "pool_id": pool_id or None,
            "resource_id_prefix_matches": pool_id.startswith("avax_"),
            "resource_id_suffix_format": (
                "evm_address_20_bytes" if suffix_len == 40 else
                "hex_32_bytes" if suffix_len == 64 else "other_or_non_hex"
            ),
            "resource_id_suffix_hex_length": suffix_len,
            "pool_address_source": source,
            "pool_address": current_address if ADDRESS_RE.fullmatch(str(current_address or "")) else None,
            "address_lookup_source": None,
            "address_lookup_status": "not_needed",
            "address_lookup_exact_match_count": 0,
        }
        if not ADDRESS_RE.fullmatch(str(current_address or "")):
            diag["address_lookup_source"] = "geckoterminal_search_pools"
            try:
                payload = search_pools_by_resource_id("avalanche", pool_id)
                rows = payload.get("data", []) if isinstance(payload, dict) else []
                exact = [row for row in rows if isinstance(row, dict) and row.get("id") == pool_id]
                valid = [
                    (row.get("attributes") or {}).get("address")
                    for row in exact
                    if ADDRESS_RE.fullmatch(str((row.get("attributes") or {}).get("address") or ""))
                ]
                diag["address_lookup_exact_match_count"] = len(exact)
                if len(valid) == 1:
                    pool["pool_address"] = valid[0]
                    pool["pool_address_source"] = "gecko_search_exact_match"
                    diag["pool_address"] = valid[0]
                    diag["pool_address_source"] = "gecko_search_exact_match"
                    diag["address_lookup_status"] = "exact_match_valid_address"
                elif len(valid) > 1:
                    diag["address_lookup_status"] = "ambiguous_exact_matches"
                elif exact:
                    diag["address_lookup_status"] = "exact_match_without_valid_address"
                else:
                    diag["address_lookup_status"] = "no_exact_match"
            except Exception as exc:
                diag["address_lookup_status"] = "lookup_error"
                diag["address_lookup_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        result["address_diagnostics"].append(diag)

    result["unique_pool_count"] = len(all_pools)
    result["sample_pool_ids"] = [
        pool.get("pool_id") for pool in all_pools if pool.get("pool_id")
    ][:10]

    if all_pools:
        counts = {field: 0 for field in QUALITY_FIELDS}
        complete = 0
        ages = []
        future_count = 0
        for pool in all_pools:
            checks, age_minutes = _pool_quality(pool, now)
            created_at = _created_at(pool.get("created_at"))
            if created_at is not None and created_at > now:
                future_count += 1
            for field, valid in checks.items():
                counts[field] += int(valid)
            complete += int(all(checks.values()))
            if age_minutes is not None:
                ages.append(age_minutes)
        total = len(all_pools)
        result["field_quality"] = {
            field: {
                "valid": count,
                "invalid_or_missing": total - count,
                "coverage_pct": round(count / total * 100, 2),
            }
            for field, count in counts.items()
        }
        result["complete_record_count"] = complete
        result["complete_record_pct"] = round(complete / total * 100, 2)
        result["quality_status"] = "measured"
        result["future_created_at_count"] = future_count
        if ages:
            result["pool_age_minutes"] = {
                "min": round(min(ages), 2),
                "median": round(statistics.median(ages), 2),
                "max": round(max(ages), 2),
            }
    else:
        result["quality_status"] = "no_data"

    if request_error is not None:
        result["status"] = "partial_failure"
    elif not all_pools:
        # A successful HTTP cycle with no pool data is not sufficient validation evidence.
        result["status"] = "no_data"
    else:
        result["status"] = "success"
    result["spread_data_available"] = False
    return result


def main():
    result = validate()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(
        "Avalanche validation status=%s; pages=%d; pools=%d; unique=%d; duplicates=%d; "
        "pagination=%s; quality=%s.",
        result["status"], result["pages_completed"], result["pools_received"],
        result["unique_pool_count"], result["duplicate_pool_count"],
        result["pagination_status"], result["quality_status"],
    )
    return 0 if result["status"] == "success" and result["resolved_gecko_slug"] == "avax" else 1


if __name__ == "__main__":
    raise SystemExit(main())
