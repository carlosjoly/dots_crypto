"""Read-only live validation of a real BSC PancakeSwap V2 UNCX LP lock."""
import argparse
import json
import os
from pathlib import Path
import sys

from .new_launches import fetch_current_pool_liquidity
from .onchain_lp import JsonRpc
from .uncx_bsc_lock import (
    build_bsc_pancakeswap_v2_lp_proof,
    is_valid_bsc_pancakeswap_v2_lp_proof,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool-address", required=True)
    parser.add_argument("--lookback-blocks", type=int, default=15_000_000)
    args = parser.parse_args()

    rpc_url = os.getenv("BOT_F_RPC_URL_BSC", "").strip()
    if not rpc_url.startswith(("https://", "http://")):
        parser.error("BOT_F_RPC_URL_BSC ausente ou inválido")

    pool = args.pool_address.strip()
    audit_path = Path("data/bot_f_uncx_live_validation.json")
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "pool_address": pool.lower(),
        "network": "bsc",
        "dex_expected": "PancakeSwap V2",
        "lookback_blocks": args.lookback_blocks,
        "status": "blocked",
        "valid": False,
        "reason": None,
    }
    try:
        fresh = fetch_current_pool_liquidity("bsc", pool)
        result["fresh_market_data"] = fresh
        if fresh.get("classification") != "passed":
            raise RuntimeError(
                "liquidez fresca não atingiu o mínimo: "
                f"{fresh.get('classification')} / {fresh.get('liquidity_usd')}"
            )
        proof = build_bsc_pancakeswap_v2_lp_proof(
            pool,
            JsonRpc(rpc_url),
            liquidity_usd=fresh["liquidity_usd"],
            audit_path=audit_path.with_suffix(".jsonl"),
            lookback_blocks=args.lookback_blocks,
        )
        result["proof"] = proof
        expected_token = proof.get("token0") if isinstance(proof, dict) else None
        valid = is_valid_bsc_pancakeswap_v2_lp_proof(
            proof, pool, fresh["liquidity_usd"], expected_token
        )
        result["valid"] = bool(valid)
        result["status"] = "verified" if valid else str(proof.get("status", "blocked"))
        result["reason"] = None if valid else proof.get("reason", "prova não passou na validação final")
    except Exception as exc:
        result["reason"] = f"{type(exc).__name__}: {str(exc)[:400]}"

    temporary = audit_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(audit_path)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
