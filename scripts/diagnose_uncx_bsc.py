"""Read-only live BSC/UNCX ABI diagnostic; never signs or sends transactions.

Run from repository root:
  python scripts/diagnose_uncx_bsc.py --pool 0x... --pool 0x...

Requires BOT_F_RPC_URL_BSC in the environment. RPC URL is never printed.
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
        response = requests.post(
            self.url,
            json={"jsonrpc": "2.0", "id": self.i, "method": method, "params": params},
            timeout=20,
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or body.get("error") or "result" not in body:
            raise RuntimeError(f"RPC {method} returned an error or malformed response")
        return body["result"]


def selector(rpc, signature):
    return rpc.call("web3_sha3", ["0x" + signature.encode("ascii").hex()])[:10]


def topic(rpc, signature):
    return rpc.call("web3_sha3", ["0x" + signature.encode("ascii").hex()])


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
        result["factory_pair"] = read_address(
            rpc, factory, "getPair(address,address)" if False else "factory()", block
        ) if False else None
        reserves = eth_call(rpc, pool, "0x0902f1ac", block)
        if not isinstance(reserves, str) or len(reserves) < 194:
            raise RuntimeError("getReserves returned malformed data")
        result["reserve0_raw"] = str(int(reserves[2:66], 16))
        result["reserve1_raw"] = str(int(reserves[66:130], 16))
        result["total_supply_raw"] = str(int(eth_call(rpc, pool, "0x18160ddd", block), 16))
    except Exception as exc:
        result["error"] = str(exc)[:300]
    return result


def main():
    parser = argparse.ArgumentParser(description="Read-only BSC UNCX ABI/deployment diagnostic")
    parser.add_argument("--pool", action="append", default=[], help="BSC LP pair address; repeatable")
    args = parser.parse_args()
    url = os.getenv("BOT_F_RPC_URL_BSC", "").strip()
    if not url.startswith(("https://", "http://")):
        print("BLOCKED: defina BOT_F_RPC_URL_BSC no ambiente; o valor não será exibido.")
        return 2
    for pool in args.pool:
        if not ADDRESS_RE.fullmatch(pool):
            print(f"BLOCKED: endereço de pool inválido: {pool}")
            return 2

    rpc = Rpc(url)
    out = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "mode": "read_only"}
    try:
        chain = rpc.call("eth_chainId", [])
        if int(chain, 16) != 56:
            raise RuntimeError(f"RPC chainId is {chain}, expected 0x38 (56)")
        block = rpc.call("eth_getBlockByNumber", ["latest", False])
        block_tag = block["number"]
        out["chain_id"] = 56
        out["block_number"] = int(block_tag, 16)
        out["block_hash"] = block["hash"]
        code = rpc.call("eth_getCode", [LOCKER, block_tag])
        if not isinstance(code, str) or code in ("0x", "0x0"):
            raise RuntimeError("UNCX locker has no runtime bytecode")
        out["locker"] = LOCKER
        out["locker_runtime_bytes"] = (len(code) - 2) // 2
        out["locker_runtime_keccak"] = rpc.call("web3_sha3", [code])
        out["locker_factory"] = read_address(rpc, LOCKER, "uniswapFactory()", block_tag)
        out["locker_factory_matches_pancakeswap_v2"] = out["locker_factory"].lower() == PANCAKE_FACTORY

        signatures = [
            "getNumLocksForToken(address)",
            "tokenLocks(address,uint256)",
            "getUserNumLocksForToken(address,address)",
            "getUserLockForTokenAtIndex(address,address,uint256)",
        ]
        out["selectors"] = {sig: selector(rpc, sig) for sig in signatures}
        out["event_topics"] = {
            "legacy_onDeposit(address,address,uint256,uint256,uint256)": topic(
                rpc, "onDeposit(address,address,uint256,uint256,uint256)"
            ),
            "v2_1_onNewLock(uint256,address,address,uint256,uint256,uint256,uint16)": topic(
                rpc, "onNewLock(uint256,address,address,uint256,uint256,uint256,uint16)"
            ),
            "v2_1_onRelock(uint256,address,address,uint256,uint256,uint256)": topic(
                rpc, "onRelock(uint256,address,address,uint256,uint256,uint256)"
            ),
        }
        count_data = eth_call(
            rpc, LOCKER, out["selectors"]["getNumLocksForToken(address)"] + addr_arg(args.pool[0])
            if args.pool else out["selectors"]["getNumLocksForToken(address)"] + addr_arg("0x" + "0" * 40),
            block_tag,
        )
        out["getNumLocksForToken_probe_raw"] = count_data
        out["getNumLocksForToken_probe_count"] = int(count_data, 16) if count_data else None
        if args.pool:
            out["pools"] = [diagnose_pool(rpc, pool, block_tag) for pool in args.pool]
        out["warning"] = (
            "Diagnostic only. A runtime hash and successful selector probes do not authenticate "
            "the deployment, prove the ABI, or establish that any LP is safely locked."
        )
    except Exception as exc:
        out["blocked"] = str(exc)[:500]
        print(json.dumps(out, indent=2))
        return 1
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
