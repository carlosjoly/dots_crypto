"""Read-only live BSC/UNCX diagnostic for GitHub Actions.

Requires BOT_F_RPC_URL_BSC. The endpoint is never printed or written to artifacts.
This diagnostic gathers deployment and pool observations; it does NOT approve LP locks.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

import requests

LOCKER = "0xc765bddb93b0d1c1a88282ba0fa6b2d00e3e0c83"
PANCAKE_FACTORY = "0xca143ce32fe78f1f7019d7d551a6402fc5350c73"
ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


class Rpc:
    def __init__(self, url):
        self.url = url
        self.i = 0

    def call(self, method, params):
        self.i += 1
        try:
            response = requests.post(
                self.url,
                json={"jsonrpc": "2.0", "id": self.i, "method": method, "params": params},
                timeout=20,
            )
            if response.status_code < 200 or response.status_code >= 300:
                raise RuntimeError(f"RPC HTTP status {response.status_code}")
            body = response.json()
        except requests.RequestException as exc:
            # Requests exceptions may embed the credential-bearing URL.
            raise RuntimeError(f"RPC transport failure ({type(exc).__name__}); endpoint redacted") from None
        except ValueError:
            raise RuntimeError("RPC returned invalid JSON") from None
        if not isinstance(body, dict) or body.get("error") or "result" not in body:
            raise RuntimeError(f"RPC method {method} returned an error or malformed response")
        return body["result"]


def selector(rpc, signature):
    value = rpc.call("web3_sha3", ["0x" + signature.encode("ascii").hex()])
    if not isinstance(value, str) or len(value) < 10:
        raise RuntimeError(f"{signature}: invalid selector response")
    return value[:10]


def topic(rpc, signature):
    value = rpc.call("web3_sha3", ["0x" + signature.encode("ascii").hex()])
    if not isinstance(value, str) or len(value) != 66:
        raise RuntimeError(f"{signature}: invalid event topic response")
    return value


def word(n):
    return format(int(n), "064x")


def addr_arg(value):
    return value[2:].lower().rjust(64, "0")


def eth_call(rpc, to, data, block):
    return rpc.call("eth_call", [{"to": to, "data": data}, block])


def read_address(rpc, to, signature, block):
    result = eth_call(rpc, to, selector(rpc, signature), block)
    if not isinstance(result, str) or len(result) < 66:
        raise RuntimeError(f"{signature}: unexpected ABI return")
    return "0x" + result[-40:]


def diagnose_pool(rpc, pool, block):
    result = {"pool": pool.lower()}
    try:
        code = rpc.call("eth_getCode", [pool, block])
        if not isinstance(code, str) or code in ("0x", "0x0"):
            raise RuntimeError("pool has no bytecode")
        result["pool_code_bytes"] = max(0, (len(code) - 2) // 2)
        factory = read_address(rpc, pool, "factory()", block)
        token0 = read_address(rpc, pool, "token0()", block)
        token1 = read_address(rpc, pool, "token1()", block)
        result.update({"factory": factory, "token0": token0, "token1": token1})
        result["pancakeswap_v2_factory_match"] = factory.lower() == PANCAKE_FACTORY
        pair_data = selector(rpc, "getPair(address,address)") + addr_arg(token0) + addr_arg(token1)
        pair_result = eth_call(rpc, factory, pair_data, block)
        if not isinstance(pair_result, str) or len(pair_result) < 66:
            raise RuntimeError("factory.getPair returned malformed data")
        result["factory_pair"] = "0x" + pair_result[-40:]
        result["factory_pair_matches_pool"] = result["factory_pair"].lower() == pool.lower()

        try:
            count_data = eth_call(
                rpc, LOCKER,
                selector(rpc, "getNumLocksForToken(address)") + addr_arg(pool),
                block,
            )
            if not isinstance(count_data, str) or not count_data.startswith("0x"):
                raise RuntimeError("getNumLocksForToken returned malformed data")
            lock_count = int(count_data, 16)
            result["getNumLocksForToken_count"] = lock_count
            if lock_count > 0:
                try:
                    token_locks_data = eth_call(
                        rpc, LOCKER,
                        selector(rpc, "tokenLocks(address,uint256)") + addr_arg(pool) + word(0),
                        block,
                    )
                    result["tokenLocks_index0_return_bytes"] = (
                        max(0, (len(token_locks_data) - 2) // 2)
                        if isinstance(token_locks_data, str) else None
                    )
                    result["tokenLocks_index0_raw"] = token_locks_data
                except Exception as exc:
                    result["tokenLocks_probe_error"] = str(exc)[:200]
        except Exception as exc:
            # Legacy and v2.1 UNCX deployments expose different lock getters.
            # Keep collecting independent pool/factory/reserve evidence.
            result["getNumLocksForToken_probe_error"] = str(exc)[:200]

        reserves = eth_call(rpc, pool, "0x0902f1ac", block)
        if not isinstance(reserves, str) or len(reserves) < 194:
            raise RuntimeError("getReserves returned malformed data")
        result["reserve0_raw"] = str(int(reserves[2:66], 16))
        result["reserve1_raw"] = str(int(reserves[66:130], 16))
        result["total_supply_raw"] = str(int(eth_call(rpc, pool, "0x18160ddd", block), 16))
        result["status"] = "observed"
    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)[:300]
    return result


def main():
    parser = argparse.ArgumentParser(description="Read-only BSC UNCX deployment diagnostic")
    parser.add_argument("--pool", action="append", default=[], help="BSC LP pair address; repeatable")
    args = parser.parse_args()

    out = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only",
        "diagnostic_only": True,
        "approval": False,
    }
    url = os.getenv("BOT_F_RPC_URL_BSC", "").strip()
    if not url.startswith(("https://", "http://")):
        out.update({"status": "blocked", "reason": "BOT_F_RPC_URL_BSC secret is missing or invalid"})
        print(json.dumps(out, indent=2))
        return 2
    if not args.pool:
        out.update({"status": "blocked", "reason": "no pool addresses supplied"})
        print(json.dumps(out, indent=2))
        return 2
    invalid = [pool for pool in args.pool if not ADDRESS_RE.fullmatch(pool)]
    if invalid:
        out.update({"status": "blocked", "reason": "one or more pool addresses are invalid"})
        print(json.dumps(out, indent=2))
        return 2

    rpc = Rpc(url)
    try:
        chain = rpc.call("eth_chainId", [])
        if int(chain, 16) != 56:
            raise RuntimeError(f"RPC chainId is {chain}, expected 0x38 (56)")
        block = rpc.call("eth_getBlockByNumber", ["latest", False])
        if not isinstance(block, dict) or not block.get("number") or not block.get("hash"):
            raise RuntimeError("latest block response is incomplete")
        block_tag = block["number"]
        out.update({
            "chain_id": 56,
            "block_number": int(block_tag, 16),
            "block_hash": block["hash"],
            "locker": LOCKER,
            "pancakeswap_v2_factory_expected": PANCAKE_FACTORY,
        })
        code = rpc.call("eth_getCode", [LOCKER, block_tag])
        if not isinstance(code, str) or code in ("0x", "0x0"):
            raise RuntimeError("UNCX locker has no runtime bytecode")
        out["locker_runtime_bytes"] = (len(code) - 2) // 2
        out["locker_runtime_keccak"] = rpc.call("web3_sha3", [code])
        out["locker_factory"] = read_address(rpc, LOCKER, "uniswapFactory()", block_tag)
        out["locker_factory_matches_pancakeswap_v2"] = (
            out["locker_factory"].lower() == PANCAKE_FACTORY
        )
        signatures = [
            "getNumLocksForToken(address)",
            "tokenLocks(address,uint256)",
            "getUserNumLocksForToken(address,address)",
            "getUserLockForTokenAtIndex(address,address,uint256)",
        ]
        out["selectors"] = {sig: selector(rpc, sig) for sig in signatures}
        out["event_topics"] = {
            "legacy_onDeposit": topic(rpc, "onDeposit(address,address,uint256,uint256,uint256)"),
            "v2_1_onNewLock": topic(rpc, "onNewLock(uint256,address,address,uint256,uint256,uint256,uint16)"),
            "v2_1_onRelock": topic(rpc, "onRelock(uint256,address,address,uint256,uint256,uint256)"),
        }
        out["pools"] = [diagnose_pool(rpc, pool, block_tag) for pool in args.pool]
        pool_errors = sum(item.get("status") != "observed" for item in out["pools"])
        out["status"] = "completed_with_pool_errors" if pool_errors else "observed"
        out["pool_error_count"] = pool_errors
        out["warning"] = (
            "Observations only. Selectors, return data and runtime hash do not authenticate "
            "the deployment, establish ABI semantics, prove active LP locks, or approve alerts."
        )
        print(json.dumps(out, indent=2))
        return 1 if pool_errors else 0
    except Exception as exc:
        # Rpc.call deliberately strips the endpoint from transport errors.
        out.update({"status": "blocked", "reason": str(exc)[:500]})
        print(json.dumps(out, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
