"""Isolated live smoke test for GeckoTerminal's Avalanche new-pools endpoint."""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .new_launches import fetch_new_pools, gecko_network_id

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("dot_cripto.bot_f.avalanche_validation")


def main():
    result = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "configured_network": "avalanche",
        "expected_gecko_slug": "avax",
        "resolved_gecko_slug": gecko_network_id("avalanche"),
        "status": "failed",
        "pools_received": 0,
        "sample_pool_ids": [],
    }
    try:
        pools = fetch_new_pools("avalanche")
        result["status"] = "success"
        result["pools_received"] = len(pools)
        result["sample_pool_ids"] = [p.get("pool_id") for p in pools[:5]]
        log.info("Avalanche smoke test OK: slug=%s; pools=%d.",
                 result["resolved_gecko_slug"], result["pools_received"])
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {str(exc)[:240]}"
        log.exception("Avalanche smoke test failed: slug=%s.", result["resolved_gecko_slug"])
    output = Path("data/bot_f_avalanche_validation.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["status"] == "success" and result["resolved_gecko_slug"] == "avax" else 1


if __name__ == "__main__":
    raise SystemExit(main())
