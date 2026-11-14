"""Live, read-only smoke test for the Bot F UNCX BSC adapter."""
import json
import os
from pathlib import Path

from app.onchain_lp import JsonRpc
from app.uncx_bsc_lock import (
    PANCAKE_V2_FACTORY,
    PINNED_UNCX_LOCKER_RUNTIME_CODE_HASH,
    UNCX_BSC_PANCAKE_V2_LOCKER,
    verify_uncx_bsc_pancakeswap_v2_lock,
)

POOLS = (
    "0x8b8677f508f0758d05b2957e16371be3f231fcd8",
    "0x7500d19c281066887c1d5bb885997375738bf517",
)
OUTPUT = Path("data/bot_f_uncx_adapter_validation.json")


def validate_pool(rpc, pool):
    result = verify_uncx_bsc_pancakeswap_v2_lock(pool, rpc, lookback_blocks=50000)
    identity_ok = (
        result.get("chain_id") == "56"
        and result.get("pool_factory", "").lower() == PANCAKE_V2_FACTORY
        and result.get("locker_factory", "").lower() == PANCAKE_V2_FACTORY
        and result.get("lock_contract", UNCX_BSC_PANCAKE_V2_LOCKER).lower() == UNCX_BSC_PANCAKE_V2_LOCKER
        and result.get("locker_runtime_code_hash", "").lower() == PINNED_UNCX_LOCKER_RUNTIME_CODE_HASH
        and int(result.get("reserve_block_number", 0)) > 0
        and len(result.get("reserve_block_hash", "")) == 66
    )
    if not identity_ok:
        return {"pool": pool, "status": "inconclusive", "reason": result.get("reason")}
    if result.get("status") == "lock_verified_transaction_linked":
        if float(result.get("protected_fraction", 0)) < 0.999 or not result.get("lock_transactions"):
            return {"pool": pool, "status": "inconclusive", "reason": "lock proof fields incomplete"}
        return {"pool": pool, "status": "active_lock_verified", "approval": False, "proof": result}
    if result.get("status") == "blocked" and result.get("reason") == "locker não registra locks para este LP":
        return {"pool": pool, "status": "no_active_locks_confirmed", "approval": False, "identity": {
            "chain_id": result["chain_id"],
            "pool_factory": result["pool_factory"],
            "locker_factory": result["locker_factory"],
            "locker_runtime_code_hash": result["locker_runtime_code_hash"],
            "block_number": result["reserve_block_number"],
            "block_hash": result["reserve_block_hash"],
        }}
    return {"pool": pool, "status": "inconclusive", "reason": result.get("reason")}


def main():
    url = os.getenv("BOT_F_RPC_URL_BSC", "").strip()
    if not url.startswith(("https://", "http://")):
        print("BLOCKED: BOT_F_RPC_URL_BSC ausente; endpoint não será exibido.")
        return 2
    rpc = JsonRpc(url)
    results = [validate_pool(rpc, pool) for pool in POOLS]
    payload = {
        "mode": "read_only",
        "diagnostic_only": True,
        "approval": False,
        "locker": UNCX_BSC_PANCAKE_V2_LOCKER,
        "expected_factory": PANCAKE_V2_FACTORY,
        "expected_runtime_hash": PINNED_UNCX_LOCKER_RUNTIME_CODE_HASH,
        "lookback_blocks": 50000,
        "results": results,
        "status": "success" if all(item["status"] != "inconclusive" for item in results) else "inconclusive",
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
