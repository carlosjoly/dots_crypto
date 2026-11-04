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
)


def _pool_quality(pool, now):
    """Return field-level checks; this is diagnostic, not a token approval."""
    base = pool.get("base") if isinstance(pool.get("base"), dict) else {}
    quote = pool.get("quote") if isinstance(pool.get("quote"), dict) else {}
    created = _created_at(pool.get("created_at"))
    liquidity = _number(pool.get("liquidity_usd"))
    volume = _number(pool.get("volume_24h_usd"))
    txns = pool.get("txns_24h")
    buys = _number(txns.get("buys")) if isinstance(txns, dict) else None
    sells = _number(txns.get("sells")) if isinstance(txns, dict) else None

    checks = {
        "pool_id": bool(pool.get("pool_id")),
        "pool_address": bool(ADDRESS_RE.fullmatch(str(pool.get("pool_address") or ""))),
        "created_at": created is not None and created <= now + timedelta(minutes=5),
        "liquidity_usd": liquidity is not None and liquidity >= 0,
        "volume_24h_usd": volume is not None and volume >= 0,
        "txns_24h": buys is not None and sells is not None and buys >= 0 and sells >= 0,
        "base_token_address": bool(ADDRESS_RE.fullmatch(str(base.get("address") or ""))),
        "quote_token_address": bool(ADDRESS_RE.fullmatch(str(quote.get("address") or ""))),
    }
    age_minutes = (now - created).total_seconds() / 60 if created and created <= now + timedelta(minutes=5) else None
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
            for field, valid in checks.items():
                counts[field] += int(valid)
            complete += int(all(checks.values()))
            if age_minutes is not None:
                if age_minutes < 0:
                    future_count += 1
                else:
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

    result["status"] = "success" if request_error is None else "partial_failure"
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
