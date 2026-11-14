"""Read-only UNCX V2.1 live diagnostic for BSC PancakeSwap V2 LP locks.

This diagnostic never approves an alert. It checks the documented V2.1
per-user lock getters, indexed lock events, transaction receipts and LP custody
at one pinned block. The legacy deposit event is collected separately as
historical context only.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

import requests

LOCKER = "0xc765bddb93b0d1c1a88282ba0fa6b2d00e3e0c83"
FACTORY = "0xca143ce32fe78f1f7019d7d551a6402fc5350c73"
POOLS_DEFAULT = (
    "0x8b8677f508f0758d05b2957e16371be3f231fcd8",
    "0x7500d19c281066887c1d5bb885997375738bf517",
)
ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
HASH_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")
# Selectors and topics are fixed from Keccak signatures and the official UNCX V2.1 ABI.
GET_NUM_LOCKS = "0x1f2a1d2f"
GET_USER_NUM_LOCKS = "0xa69d9c4f"
GET_USER_LOCK = "0xd4ff493f"
ON_NEW_LOCK = "0x3e5a874dd8f086c73bba5a860cb04972e50b6207f84cc020037d576afa6ebc13"
ON_DEPOSIT_LEGACY = "0x830357565da6ecfc26d8d9f69df488ed6f70361af9a07e570544aeb5c5e765e"


class DiagnosticError(RuntimeError):
    pass


class Rpc:
    def __init__(self, url):
        self.url = url
        self.request_id = 0

    def call(self, method, params):
        self.request_id += 1
        try:
            response = requests.post(
                self.url,
                json={"jsonrpc": "2.0", "id": self.request_id, "method": method, "params": params},
                timeout=25,
            )
            if not 200 <= response.status_code < 300:
                raise DiagnosticError(f"RPC HTTP {response.status_code}")
            payload = response.json()
        except requests.RequestException as exc:
            raise DiagnosticError(f"RPC transport failure ({type(exc).__name__}); endpoint redacted") from None
        except ValueError:
            raise DiagnosticError("RPC returned invalid JSON") from None
        if not isinstance(payload, dict) or payload.get("error") or "result" not in payload:
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            message = str(error.get("message", "malformed JSON-RPC response"))[:180]
            raise DiagnosticError(f"{method}: {message}")
        return payload["result"]


def word(value):
    return format(int(value), "064x")


def address_arg(value):
    if not ADDRESS_RE.fullmatch(str(value or "")):
        raise DiagnosticError("invalid EVM address argument")
    return str(value)[2:].lower().rjust(64, "0")


def decode_words(raw, expected):
    if not isinstance(raw, str) or not raw.startswith("0x"):
        raise DiagnosticError("invalid ABI hex return")
    body = raw[2:]
    if len(body) != expected * 64 or not re.fullmatch(r"[a-fA-F0-9]*", body):
        raise DiagnosticError(f"ABI return must contain exactly {expected} words")
    return [int(body[i * 64:(i + 1) * 64], 16) for i in range(expected)]


def decode_address_word(value):
    return "0x" + format(int(value), "064x")[-40:]


def decode_v21_new_lock(log):
    topics = log.get("topics") or []
    if len(topics) != 4 or str(topics[0]).lower() != ON_NEW_LOCK:
        raise DiagnosticError("invalid UNCX V2.1 onNewLock topics")
    lock_id = int(topics[1], 16)
    pool = decode_address_word(int(topics[2], 16))
    owner = decode_address_word(int(topics[3], 16))
    amount, lock_date, unlock_date, country_code = decode_words(log.get("data"), 4)
    return {
        "lock_id": lock_id,
        "pool": pool.lower(),
        "owner": owner.lower(),
        "amount_raw": str(amount),
        "lock_date": lock_date,
        "unlock_date": unlock_date,
        "country_code": country_code,
        "transaction_hash": str(log.get("transactionHash") or "").lower(),
        "block_number": int(str(log.get("blockNumber") or "0x0"), 16),
        "log_index": int(str(log.get("logIndex") or "0x0"), 16),
        "raw_data": str(log.get("data") or "").lower(),
    }


def _eth_call(rpc, to, data, block_tag):
    result = rpc.call("eth_call", [{"to": to, "data": data}, block_tag])
    if not isinstance(result, str) or not result.startswith("0x"):
        raise DiagnosticError("eth_call returned non-hex result")
    return result


def _uint_call(rpc, to, data, block_tag):
    return decode_words(_eth_call(rpc, to, data, block_tag), 1)[0]


def _chunked_logs(rpc, query, start, end, chunk_size):
    logs = []
    cursor = start
    while cursor <= end:
        stop = min(end, cursor + chunk_size - 1)
        request = dict(query, fromBlock=hex(cursor), toBlock=hex(stop))
        result = rpc.call("eth_getLogs", [request])
        if not isinstance(result, list):
            raise DiagnosticError("eth_getLogs returned a non-list result")
        logs.extend(result)
        cursor = stop + 1
    return logs


def _read_user_lock(rpc, owner, pool, index, block_tag):
    data = GET_USER_LOCK + address_arg(owner) + address_arg(pool) + word(index)
    values = decode_words(_eth_call(rpc, LOCKER, data, block_tag), 8)
    return {
        "lp_token": decode_address_word(values[0]).lower(),
        "lock_date": values[1],
        "amount_raw": str(values[2]),
        "initial_amount_raw": str(values[3]),
        "unlock_date": values[4],
        "lock_id": values[5],
        "owner": decode_address_word(values[6]).lower(),
        "country_code": values[7],
    }


def diagnose_pool(rpc, pool, block, lookback_blocks, chunk_size):
    pool = pool.lower()
    block_number = int(block["number"], 16)
    block_tag = hex(block_number)
    result = {"pool": pool, "status": "blocked", "approval": False}
    try:
        code = rpc.call("eth_getCode", [pool, block_tag])
        if not isinstance(code, str) or code in ("0x", "0x0"):
            raise DiagnosticError("pool has no runtime bytecode")
        factory = "0x" + _eth_call(rpc, pool, "0xc45a0155", block_tag)[-40:]
        token0 = "0x" + _eth_call(rpc, pool, "0x0dfe1681", block_tag)[-40:]
        token1 = "0x" + _eth_call(rpc, pool, "0xd21220a7", block_tag)[-40:]
        pair = "0x" + _eth_call(
            rpc, factory, "0xe6a43905" + address_arg(token0) + address_arg(token1), block_tag
        )[-40:]
        reserves = decode_words(_eth_call(rpc, pool, "0x0902f1ac", block_tag), 3)
        total_supply = _uint_call(rpc, pool, "0x18160ddd", block_tag)
        global_count = _uint_call(rpc, LOCKER, GET_NUM_LOCKS + address_arg(pool), block_tag)
        result.update({
            "pool_code_bytes": (len(code) - 2) // 2,
            "factory": factory.lower(),
            "factory_matches_pancakeswap_v2": factory.lower() == FACTORY,
            "token0": token0.lower(),
            "token1": token1.lower(),
            "factory_pair": pair.lower(),
            "factory_pair_matches_pool": pair.lower() == pool,
            "reserve0_raw": str(reserves[0]),
            "reserve1_raw": str(reserves[1]),
            "reserve_timestamp": reserves[2],
            "lp_total_supply_raw": str(total_supply),
            "v21_global_lock_count": global_count,
        })
        if factory.lower() != FACTORY or pair.lower() != pool:
            raise DiagnosticError("pool/factory identity mismatch")
        if total_supply <= 0:
            raise DiagnosticError("LP totalSupply is zero")

        start = max(0, block_number - lookback_blocks)
        pool_topic = "0x" + address_arg(pool)
        v21_logs = _chunked_logs(rpc, {
            "address": LOCKER,
            "topics": [ON_NEW_LOCK, None, pool_topic],
        }, start, block_number, chunk_size)
        decoded_events = []
        for item in v21_logs:
            if str(item.get("address", "")).lower() == LOCKER:
                event = decode_v21_new_lock(item)
                if event["pool"] == pool:
                    decoded_events.append(event)

        # Legacy onDeposit has no indexed fields in the published legacy ABI.
        legacy_logs = _chunked_logs(rpc, {
            "address": LOCKER, "topics": [ON_DEPOSIT_LEGACY],
        }, start, block_number, chunk_size)
        legacy_pool_events = []
        for item in legacy_logs:
            data = str(item.get("data") or "")
            if len(data) >= 66 and data.startswith("0x"):
                logged_pool = "0x" + data[2:66][-40:]
                if logged_pool.lower() == pool:
                    legacy_pool_events.append({
                        "transaction_hash": str(item.get("transactionHash") or "").lower(),
                        "block_number": int(str(item.get("blockNumber") or "0x0"), 16),
                    })

        result["history"] = {
            "from_block": start,
            "to_block": block_number,
            "lookback_blocks": lookback_blocks,
            "v21_onNewLock_events": len(decoded_events),
            "legacy_onDeposit_events": len(legacy_pool_events),
            "coverage": "bounded_lookback",
        }
        if global_count == 0:
            result["status"] = "no_current_locks"
            result["reason"] = (
                "getNumLocksForToken returned zero at the pinned block; historical events "
                "are reported separately and do not imply a current active lock"
            )
            if decoded_events:
                result["consistency_warning"] = (
                    "onNewLock events exist in the lookback but the current global lock count is zero"
                )
            return result

        owners = sorted({event["owner"] for event in decoded_events})
        if not owners:
            raise DiagnosticError(
                "current lock count is nonzero but no V2.1 onNewLock owner was found in the bounded lookback"
            )
        records = []
        owner_counts = {}
        for owner in owners:
            count = _uint_call(
                rpc, LOCKER, GET_USER_NUM_LOCKS + address_arg(owner) + address_arg(pool), block_tag
            )
            owner_counts[owner] = count
            if count > 500:
                raise DiagnosticError("per-owner lock count exceeds safety limit")
            for index in range(count):
                lock = _read_user_lock(rpc, owner, pool, index, block_tag)
                if lock["lp_token"] != pool or lock["owner"] != owner:
                    raise DiagnosticError("V2.1 getter returned a lock for a different pool or owner")
                if int(lock["amount_raw"]) > int(lock["initial_amount_raw"]):
                    raise DiagnosticError("lock amount exceeds initial amount")
                records.append(lock)
        if len(records) != global_count:
            raise DiagnosticError(
                f"owner-indexed records ({len(records)}) do not match global count ({global_count})"
            )

        locker_balance = _uint_call(
            rpc, pool, "0x70a08231" + address_arg(LOCKER), block_tag
        )
        active = [
            item for item in records
            if int(item["amount_raw"]) > 0 and int(item["unlock_date"]) > int(block.get("timestamp", "0x0"), 16)
        ]
        locked_active = sum(int(item["amount_raw"]) for item in active)
        fraction = locked_active / total_supply
        if locker_balance < locked_active:
            raise DiagnosticError("locker LP balance is below the sum of active lock records")

        linked = []
        for record in active:
            matches = [
                event for event in decoded_events
                if event["lock_id"] == record["lock_id"]
                and event["pool"] == pool
                and event["owner"] == record["owner"]
            ]
            if len(matches) != 1:
                raise DiagnosticError("active lock has no unique matching onNewLock event in lookback")
            event = matches[0]
            tx_hash = event["transaction_hash"]
            if not HASH_RE.fullmatch(tx_hash):
                raise DiagnosticError("onNewLock transaction hash is missing or invalid")
            receipt = rpc.call("eth_getTransactionReceipt", [tx_hash])
            if not isinstance(receipt, dict) or receipt.get("status") != "0x1":
                raise DiagnosticError("onNewLock transaction receipt is absent or unsuccessful")
            if int(str(receipt.get("blockNumber") or "0x0"), 16) != event["block_number"]:
                raise DiagnosticError("receipt block does not match onNewLock event block")
            receipt_match = any(
                str(log.get("address", "")).lower() == LOCKER
                and [str(x).lower() for x in (log.get("topics") or [])] == [
                    ON_NEW_LOCK,
                    "0x" + word(event["lock_id"]),
                    "0x" + address_arg(pool),
                    "0x" + address_arg(event["owner"]),
                ]
                and str(log.get("data", "")).lower() == str(next(
                    source.get("data") for source in v21_logs
                    if str(source.get("transactionHash", "")).lower() == tx_hash
                    and int(str(source.get("logIndex") or "0x0"), 16) == event["log_index"]
                )).lower()
                for log in (receipt.get("logs") or [])
            )
            if not receipt_match:
                raise DiagnosticError("receipt does not contain the exact onNewLock event")
            linked.append({
                "lock_id": record["lock_id"],
                "transaction_hash": tx_hash,
                "event_block_number": event["block_number"],
                "owner": record["owner"],
                "amount_raw": record["amount_raw"],
                "unlock_date": record["unlock_date"],
            })

        result.update({
            "status": "locks_observed_and_receipts_linked",
            "reason": "lock records and receipts linked; this diagnostic does not approve LP protection",
            "owner_counts": owner_counts,
            "lock_records": records,
            "active_lock_records": len(active),
            "locker_lp_balance_raw": str(locker_balance),
            "active_locked_lp_raw": str(locked_active),
            "protected_fraction": fraction,
            "lock_transactions": linked,
            "protection_threshold_999pct_met": fraction >= 0.999,
        })
    except Exception as exc:
        result["status"] = "error"
        result["reason"] = str(exc)[:400]
    return result


def main():
    parser = argparse.ArgumentParser(description="Read-only UNCX V2.1 BSC lock diagnostic")
    parser.add_argument("--pool", action="append", default=[])
    parser.add_argument("--lookback-blocks", type=int, default=50000)
    parser.add_argument("--chunk-size", type=int, default=2000)
    args = parser.parse_args()
    pools = args.pool or list(POOLS_DEFAULT)
    output = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only",
        "diagnostic_only": True,
        "approval": False,
        "locker": LOCKER,
        "expected_factory": FACTORY,
        "lookback_blocks": args.lookback_blocks,
    }
    url = os.getenv("BOT_F_RPC_URL_BSC", "").strip()
    if not url.startswith(("https://", "http://")):
        output.update({"status": "blocked", "reason": "BOT_F_RPC_URL_BSC secret missing or invalid"})
        print(json.dumps(output, indent=2))
        return 2
    if not 1 <= args.lookback_blocks <= 500000 or not 100 <= args.chunk_size <= 10000:
        output.update({"status": "blocked", "reason": "lookback/chunk configuration outside safe bounds"})
        print(json.dumps(output, indent=2))
        return 2
    if any(not ADDRESS_RE.fullmatch(pool) for pool in pools):
        output.update({"status": "blocked", "reason": "invalid pool address"})
        print(json.dumps(output, indent=2))
        return 2
    rpc = Rpc(url)
    try:
        chain = rpc.call("eth_chainId", [])
        if int(chain, 16) != 56:
            raise DiagnosticError(f"RPC chainId is {chain}, expected 0x38")
        block = rpc.call("eth_getBlockByNumber", ["latest", False])
        if not isinstance(block, dict) or not block.get("number") or not block.get("hash"):
            raise DiagnosticError("latest block missing number/hash")
        locker_code = rpc.call("eth_getCode", [LOCKER, block["number"]])
        if not isinstance(locker_code, str) or locker_code in ("0x", "0x0"):
            raise DiagnosticError("UNCX locker has no runtime bytecode")
        factory = "0x" + _eth_call(rpc, LOCKER, "0xc45a0155", block["number"])[-40:]
        output.update({
            "chain_id": 56,
            "block_number": int(block["number"], 16),
            "block_hash": block["hash"],
            "block_timestamp": int(block.get("timestamp", "0x0"), 16),
            "locker_runtime_bytes": (len(locker_code) - 2) // 2,
            "locker_runtime_keccak": rpc.call("web3_sha3", [locker_code]),
            "locker_factory": factory.lower(),
            "locker_factory_matches_expected": factory.lower() == FACTORY,
        })
        if factory.lower() != FACTORY:
            raise DiagnosticError("locker uniswapFactory() does not match PancakeSwap V2")
        output["pools"] = [
            diagnose_pool(rpc, pool, block, args.lookback_blocks, args.chunk_size)
            for pool in pools
        ]
        output["status"] = "completed"
        output["warning"] = (
            "Observational only. Bounded event history, runtime hash and linked receipts do not "
            "authenticate UNCX deployment independently, calculate USD liquidity, or approve alerts."
        )
        print(json.dumps(output, indent=2))
        return 0
    except Exception as exc:
        output.update({"status": "blocked", "reason": str(exc)[:400]})
        print(json.dumps(output, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
