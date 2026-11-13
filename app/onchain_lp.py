"""Read-only EVM verifier for V2-compatible pool identity and reserves.

A reserve snapshot is NOT an LP-lock proof. A network/DEX-specific lock adapter
must be independently implemented before this module can return status=verified.
"""
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import time
import requests

log = logging.getLogger("dot_cripto.bot_f.onchain")
ADDRESS = re.compile(r"^0x[a-fA-F0-9]{40}$")
CHAINS = {
    "eth": ("1", "BOT_F_RPC_URL_ETH"), "bsc": ("56", "BOT_F_RPC_URL_BSC"),
    "base": ("8453", "BOT_F_RPC_URL_BASE"), "arbitrum": ("42161", "BOT_F_RPC_URL_ARBITRUM"),
    "polygon_pos": ("137", "BOT_F_RPC_URL_POLYGON_POS"),
    "optimism": ("10", "BOT_F_RPC_URL_OPTIMISM"),
    "avalanche": ("43114", "BOT_F_RPC_URL_AVALANCHE"),
}
AUDIT_PATH = Path(__file__).resolve().parents[1] / "data" / "bot_f_onchain_audit.jsonl"


class VerificationError(ValueError):
    pass


class JsonRpc:
    """Read-only JSON-RPC transport; deliberately has no signing/send methods."""
    def __init__(self, url, timeout=12):
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            raise VerificationError("URL RPC ausente ou inválida")
        self.url, self.timeout, self.request_id = url, timeout, 0

    def call(self, method, params):
        self.request_id += 1
        response = requests.post(self.url, json={
            "jsonrpc": "2.0", "id": self.request_id, "method": method, "params": params,
        }, headers={"Content-Type": "application/json"}, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("error") or "result" not in payload:
            raise VerificationError(f"RPC {method}: resposta inválida ou erro")
        return payload["result"]


def _word(data, index=0):
    body = str(data or "")
    if not body.startswith("0x"):
        raise VerificationError("eth_call sem hexadecimal")
    chunk = body[2 + index * 64:2 + (index + 1) * 64]
    if not re.fullmatch(r"[a-fA-F0-9]{64}", chunk):
        raise VerificationError("palavra ABI inválida")
    return int(chunk, 16)


def _address(data):
    body = str(data or "")
    if not body.startswith("0x") or len(body) < 42:
        raise VerificationError("endereço ABI inválido")
    value = "0x" + body[-40:]
    if not ADDRESS.fullmatch(value):
        raise VerificationError("endereço ABI inválido")
    return value.lower()


def _arg(address):
    if not ADDRESS.fullmatch(str(address or "")):
        raise VerificationError("endereço EVM inválido")
    return str(address)[2:].lower().rjust(64, "0")


def _eth_call(rpc, to, data, block_tag):
    value = rpc.call("eth_call", [{"to": to, "data": data}, block_tag])
    if not isinstance(value, str) or not value.startswith("0x"):
        raise VerificationError("eth_call sem resultado hexadecimal")
    return value


def _audit(record, path=None):
    target = Path(path) if path else AUDIT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")


def verify_v2_pool_reserves(network, pool_address, allowed_factories=(),
                            rpc_url=None, audit_path=None, rpc=None):
    """Verify chainId, bytecode, V2 factory/pair identity and reserves at one block."""
    started = time.monotonic()
    chain_expected, env_key = CHAINS.get(network, (None, None))
    result = {"timestamp_utc": datetime.now(timezone.utc).isoformat(),
              "source": "EVM_JSON_RPC", "network": network,
              "pool_address": str(pool_address or "").lower(),
              "status": "blocked", "reason": None}
    try:
        if chain_expected is None:
            raise VerificationError("rede não suportada")
        if not ADDRESS.fullmatch(str(pool_address or "")):
            raise VerificationError("endereço do pool inválido")
        factories = [str(x).lower() for x in allowed_factories if ADDRESS.fullmatch(str(x or ""))]
        if not factories:
            raise VerificationError("nenhuma factory V2 explicitamente allowlisted")
        endpoint = rpc_url or os.getenv(env_key, "")
        client = rpc or JsonRpc(endpoint)
        chain_id = int(client.call("eth_chainId", []), 16)
        if chain_id != int(chain_expected):
            raise VerificationError("chainId do RPC não corresponde à rede")
        block = client.call("eth_getBlockByNumber", ["latest", False])
        if not isinstance(block, dict) or not block.get("number") or not block.get("hash"):
            raise VerificationError("bloco sem número/hash")
        number, tag = int(block["number"], 16), hex(int(block["number"], 16))
        pool = str(pool_address).lower()
        code = client.call("eth_getCode", [pool, tag])
        if not isinstance(code, str) or code in ("0x", "0x0"):
            raise VerificationError("pool sem bytecode no bloco consultado")
        factory = _address(_eth_call(client, pool, "0xc45a0155", tag))
        token0 = _address(_eth_call(client, pool, "0x0dfe1681", tag))
        token1 = _address(_eth_call(client, pool, "0xd21220a7", tag))
        if token0 == token1 or token0.endswith("0" * 40) or token1.endswith("0" * 40):
            raise VerificationError("tokens do par inválidos")
        if factory not in factories:
            raise VerificationError("factory do pool não está na allowlist")
        pair = _address(_eth_call(client, factory,
                                  "0xe6a43905" + _arg(token0) + _arg(token1), tag))
        if pair != pool:
            raise VerificationError("factory.getPair não confirma o pool exato")
        reserves = _eth_call(client, pool, "0x0902f1ac", tag)
        reserve0, reserve1, reserve_ts = _word(reserves, 0), _word(reserves, 1), _word(reserves, 2)
        supply = _word(_eth_call(client, pool, "0x18160ddd", tag))
        if min(reserve0, reserve1, supply) <= 0:
            raise VerificationError("reservas ou oferta LP nulas")
        result.update({
            "status": "reserve_verified_lock_unverified",
            "reason": "snapshot on-chain confirmado; lock LP não verificado",
            "chain_id": str(chain_id), "factory": factory, "token0": token0, "token1": token1,
            "reserve0_raw": str(reserve0), "reserve1_raw": str(reserve1),
            "reserve_timestamp": reserve_ts, "lp_total_supply_raw": str(supply),
            "reserve_block_number": number, "reserve_block_hash": block["hash"].lower(),
            "reserve_block_timestamp": int(block.get("timestamp", "0x0"), 16),
            "rpc_endpoint_host": endpoint.split("/")[2] if endpoint and "://" in endpoint else "injected-test-rpc",
        })
    except Exception as exc:
        result["reason"] = str(exc)[:500]
        log.warning("Bot F on-chain bloqueado network=%s pool=%s reason=%s",
                    network, result["pool_address"], result["reason"])
    result["duration_ms"] = round((time.monotonic() - started) * 1000, 2)
    try:
        _audit(result, audit_path)
    except OSError:
        result["status"], result["reason"] = "blocked", "falha ao persistir auditoria on-chain"
        log.exception(result["reason"])
    return result


def build_independent_lp_proof(network, pool_address, allowed_factories=(),
                               rpc_url=None, audit_path=None, rpc=None):
    """Return reserve evidence only; no trusted LP-lock adapter is registered yet.

    This function intentionally cannot emit status=verified. Add a concrete,
    reviewed network/DEX lock adapter before changing this behavior.
    """
    snapshot = verify_v2_pool_reserves(
        network, pool_address, allowed_factories, rpc_url, audit_path, rpc
    )
    if snapshot["status"] == "reserve_verified_lock_unverified":
        snapshot["reason"] = "sem adaptador de lock auditado/allowlisted para esta rede/DEX"
        _audit(snapshot, audit_path)
    return snapshot
