"""Live, read-only smoke test for the Bot F BSC pool and UNCX lock verifier.

The diagnostic never approves an alert, signs a transaction, or sends Telegram.
It records the exact pool reserve snapshot and the locker identity at the same
block so a no-lock result cannot be mistaken for a complete LP proof.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.onchain_lp import JsonRpc, verify_v2_pool_reserves
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
AUDIT = Path("data/bot_f_onchain_audit.jsonl")


def _reserve_summary(snapshot):
    return {
        "chain_id": snapshot.get("chain_id"),
        "pool_address": snapshot.get("pool_address"),
        "factory": snapshot.get("factory"),
        # reserve_verified_lock_unverified is returned only after factory.getPair
        # resolves the exact token0/token1 pair back to this pool address.
        "factory_get_pair_matches_pool": snapshot.get("status") == "reserve_verified_lock_unverified",
        "token0": snapshot.get("token0"),
        "token1": snapshot.get("token1"),
        "reserve0_raw": snapshot.get("reserve0_raw"),
        "reserve1_raw": snapshot.get("reserve1_raw"),
        "lp_total_supply_raw": snapshot.get("lp_total_supply_raw"),
        "block_number": snapshot.get("reserve_block_number"),
        "block_hash": snapshot.get("reserve_block_hash"),
    }


def _lock_identity_matches(lock, snapshot):
    return (
        lock.get("chain_id") == "56"
        and str(lock.get("pool_factory", "")).lower() == PANCAKE_V2_FACTORY
        and str(lock.get("locker_factory", "")).lower() == PANCAKE_V2_FACTORY
        and str(lock.get("lock_contract", "")).lower() == UNCX_BSC_PANCAKE_V2_LOCKER
        and str(lock.get("locker_runtime_code_hash", "")).lower() == PINNED_UNCX_LOCKER_RUNTIME_CODE_HASH
        and lock.get("reserve_block_number") == snapshot.get("reserve_block_number")
        and str(lock.get("reserve_block_hash", "")).lower() == str(snapshot.get("reserve_block_hash", "")).lower()
    )


def validate_pool(rpc, pool):
    snapshot = verify_v2_pool_reserves(
        "bsc",
        pool,
        allowed_factories=[PANCAKE_V2_FACTORY],
        audit_path=AUDIT,
        rpc=rpc,
    )
    if snapshot.get("status") != "reserve_verified_lock_unverified":
        return {
            "pool": pool,
            "status": "inconclusive",
            "approval": False,
            "reason": snapshot.get("reason") or "snapshot de reservas/identidade não validado",
        }

    lock = verify_uncx_bsc_pancakeswap_v2_lock(
        pool,
        rpc,
        audit_path=AUDIT,
        block_number=snapshot["reserve_block_number"],
        lookback_blocks=50000,
    )
    if not _lock_identity_matches(lock, snapshot):
        return {
            "pool": pool,
            "status": "inconclusive",
            "approval": False,
            "reason": lock.get("reason") or "identidade do locker/bloco não corresponde ao snapshot",
            "pool_identity": _reserve_summary(snapshot),
        }

    base = {
        "pool": pool,
        "approval": False,
        "pool_identity": _reserve_summary(snapshot),
        "locker_identity": {
            "address": lock.get("lock_contract"),
            "factory": lock.get("locker_factory"),
            "runtime_code_hash": lock.get("locker_runtime_code_hash"),
            "block_number": lock.get("reserve_block_number"),
            "block_hash": lock.get("reserve_block_hash"),
        },
    }

    if lock.get("status") == "blocked" and lock.get("reason") == "locker não registra locks para este LP":
        return {**base, "status": "no_active_locks_confirmed"}

    if lock.get("status") == "lock_verified_transaction_linked":
        try:
            protected = float(lock.get("protected_fraction"))
        except (TypeError, ValueError, OverflowError):
            protected = 0.0
        transactions = lock.get("lock_transactions")
        if protected < 0.999 or not isinstance(transactions, list) or not transactions:
            return {
                **base,
                "status": "inconclusive",
                "reason": "prova de lock ativa sem fração protegida/transações completas",
            }
        return {
            **base,
            "status": "active_lock_verified",
            "lock_evidence": {
                "protected_fraction": protected,
                "locked_lp_active_raw": lock.get("locked_lp_active_raw"),
                "locker_lp_balance_raw": lock.get("locker_lp_balance_raw"),
                "lp_total_supply_raw": lock.get("lp_total_supply_raw"),
                "lock_records_total": lock.get("lock_records_total"),
                "lock_records_active": lock.get("lock_records_active"),
                "lock_transactions": transactions,
            },
        }

    return {
        **base,
        "status": "inconclusive",
        "reason": lock.get("reason") or "estado do locker não foi comprovado",
    }


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
        "audit_file": str(AUDIT),
        "status": "success" if all(item["status"] != "inconclusive" for item in results) else "inconclusive",
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
