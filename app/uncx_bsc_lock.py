"""Read-only UNCX V2 LP-lock verifier for PancakeSwap V2 on BNB Smart Chain.

The deployment address is documented by UNCX and has verified source on BscScan,
but UNCX currently flags deployment addresses as pending independent verification.
Therefore this adapter REQUIRES an operator-pinned runtime code hash and never
enables the Bot F delivery gate by itself.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time

from .onchain_lp import (
    ADDRESS, VerificationError, _address, _arg, _audit, _eth_call, _word,
    verify_v2_pool_reserves,
)

BSC_CHAIN_ID = 56
PANCAKE_V2_FACTORY = "0xca143ce32fe78f1f7019d7d551a6402fc5350c73"
UNCX_BSC_PANCAKE_V2_LOCKER = "0xc765bddb93b0d1c1a88282ba0fa6b2d00e3e0c83"
DEFAULT_MAX_LOCK_RECORDS = 500


def _selector(rpc, signature):
    """Derive a selector using the node's Ethereum Keccak implementation."""
    result = rpc.call("web3_sha3", ["0x" + signature.encode("ascii").hex()])
    if not isinstance(result, str) or not re.fullmatch(r"0x[a-fA-F0-9]{64}", result):
        raise VerificationError("RPC web3_sha3 indisponível ou resposta inválida")
    return result[:10]


def _uint_call(rpc, target, signature, args, block_tag):
    data = _selector(rpc, signature) + "".join(_arg(x) for x in args if isinstance(x, str))
    data += "".join(format(int(x), "064x") for x in args if not isinstance(x, str))
    return _word(_eth_call(rpc, target, data, block_tag))


def _tuple_call(rpc, target, signature, args, block_tag, words=6):
    data = _selector(rpc, signature) + "".join(_arg(x) for x in args if isinstance(x, str))
    data += "".join(format(int(x), "064x") for x in args if not isinstance(x, str))
    raw = _eth_call(rpc, target, data, block_tag)
    return [_word(raw, i) for i in range(words - 1)] + [_address("0x" + raw[2 + (words - 1) * 64:2 + words * 64])]



def _deposit_event_topic(rpc):
    signature = "onDeposit(address,address,uint256,uint256,uint256)"
    topic = rpc.call("web3_sha3", ["0x" + signature.encode("ascii").hex()])
    if not isinstance(topic, str) or not re.fullmatch(r"0x[a-fA-F0-9]{64}", topic):
        raise VerificationError("não foi possível derivar o tópico do evento onDeposit")
    return topic.lower()


def _decode_deposit_log(log):
    data = str(log.get("data") or "")
    if not data.startswith("0x") or len(data) != 2 + 5 * 64:
        raise VerificationError("dados do evento onDeposit inválidos")
    return {
        "lp_token": _address("0x" + data[2:66]),
        "owner": _address("0x" + data[66:130]),
        "amount": int(data[130:194], 16),
        "lock_date": int(data[194:258], 16),
        "unlock_date": int(data[258:322], 16),
    }


def _find_deposit_transactions(rpc, pool_address, records, block_number, lookback_blocks):
    """Link every active storage record to a successful onDeposit receipt."""
    if int(lookback_blocks) < 1 or int(lookback_blocks) > 100000:
        raise VerificationError("lookback de eventos fora do intervalo permitido")
    topic = _deposit_event_topic(rpc)
    start = max(0, int(block_number) - int(lookback_blocks))
    end = int(block_number)
    logs = []
    chunk_size = 5000
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + chunk_size - 1)
        batch = rpc.call("eth_getLogs", [{
            "address": UNCX_BSC_PANCAKE_V2_LOCKER,
            "fromBlock": hex(cursor),
            "toBlock": hex(chunk_end),
            "topics": [topic],
        }])
        if not isinstance(batch, list):
            raise VerificationError("eth_getLogs retornou formato inválido")
        logs.extend(batch)
        cursor = chunk_end + 1

    linked = []
    used = set()
    for record in records:
        matches = []
        for log in logs:
            if str(log.get("address", "")).lower() != UNCX_BSC_PANCAKE_V2_LOCKER:
                continue
            topics = log.get("topics") or []
            if not topics or str(topics[0]).lower() != topic:
                continue
            decoded = _decode_deposit_log(log)
            if (
                decoded["lp_token"] == pool_address.lower()
                and decoded["owner"] == record["owner"].lower()
                and decoded["amount"] == int(record["initial_amount_raw"])
                and decoded["lock_date"] == int(record["lock_date"])
            ):
                matches.append((log, decoded))
        if len(matches) != 1:
            raise VerificationError("registro LP não possui evento onDeposit único correspondente")
        log, decoded = matches[0]
        tx_hash = str(log.get("transactionHash") or "").lower()
        if not re.fullmatch(r"0x[a-f0-9]{64}", tx_hash) or tx_hash in used:
            raise VerificationError("hash de transação ausente, inválido ou ambíguo")
        receipt = rpc.call("eth_getTransactionReceipt", [tx_hash])
        if not isinstance(receipt, dict) or receipt.get("status") != "0x1":
            raise VerificationError("recibo da transação de lock ausente ou malsucedido")
        if str(receipt.get("blockNumber", "")).lower() != str(log.get("blockNumber", "")).lower():
            raise VerificationError("bloco do recibo não corresponde ao evento de lock")
        receipt_logs = receipt.get("logs") or []
        exact = any(
            str(item.get("address", "")).lower() == UNCX_BSC_PANCAKE_V2_LOCKER
            and (item.get("topics") or [""])[0].lower() == topic
            and str(item.get("data", "")).lower() == str(log.get("data", "")).lower()
            for item in receipt_logs
        )
        if not exact:
            raise VerificationError("recibo não contém o evento onDeposit correspondente")
        used.add(tx_hash)
        linked.append({
            "transaction_hash": tx_hash,
            "event_block_number": int(str(log["blockNumber"]), 16),
            "lp_token": decoded["lp_token"],
            "owner": decoded["owner"],
            "amount_raw": str(decoded["amount"]),
            "lock_date": decoded["lock_date"],
            "event_unlock_date": decoded["unlock_date"],
            "lock_id": record["lock_id"],
        })
    return linked

def verify_uncx_bsc_pancakeswap_v2_lock(
    pool_address,
    rpc,
    expected_locker_code_hash=None,
    audit_path=None,
    max_lock_records=DEFAULT_MAX_LOCK_RECORDS,
    block_number=None,
    lookback_blocks=50000,
):
    """Verify current UNCX lock records and actual LP-token custody at one pinned block.

    The caller must have independently established that this exact pool is a
    PancakeSwap V2 pair. This function re-checks chain, factory and locker.
    """
    started = time.monotonic()
    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "network": "bsc",
        "dex": "pancakeswap_v2",
        "pool_address": str(pool_address or "").lower(),
        "lock_contract": UNCX_BSC_PANCAKE_V2_LOCKER,
        "status": "blocked",
        "reason": None,
        "source": "BNB_CHAIN_JSON_RPC",
    }
    try:
        if not ADDRESS.fullmatch(str(pool_address or "")):
            raise VerificationError("endereço do pool inválido")
        expected_hash = expected_locker_code_hash or os.getenv("BOT_F_UNCX_BSC_LOCKER_CODE_HASH", "")
        if not re.fullmatch(r"0x[a-fA-F0-9]{64}", str(expected_hash or "")):
            raise VerificationError("hash de runtime do locker não foi fixado pelo operador")
        chain_id = int(rpc.call("eth_chainId", []), 16)
        if chain_id != BSC_CHAIN_ID:
            raise VerificationError("RPC não está na BNB Smart Chain (chainId 56)")
        requested_tag = hex(int(block_number)) if block_number is not None else "latest"
        block = rpc.call("eth_getBlockByNumber", [requested_tag, False])
        if not isinstance(block, dict) or not block.get("number") or not block.get("hash"):
            raise VerificationError("bloco latest sem número/hash")
        block_number = int(block["number"], 16)
        block_tag = hex(block_number)
        locker_code = rpc.call("eth_getCode", [UNCX_BSC_PANCAKE_V2_LOCKER, block_tag])
        if not isinstance(locker_code, str) or locker_code in ("0x", "0x0"):
            raise VerificationError("contrato UNCX sem bytecode")
        actual_hash = rpc.call("web3_sha3", [locker_code])
        if not isinstance(actual_hash, str) or actual_hash.lower() != expected_hash.lower():
            raise VerificationError("runtime code hash do locker não corresponde ao pin aprovado")
        pool_factory = _address(_eth_call(rpc, pool_address, "0xc45a0155", block_tag))
        if pool_factory != PANCAKE_V2_FACTORY:
            raise VerificationError("pool não pertence à factory oficial PancakeSwap V2")
        locker_factory = _address(_eth_call(rpc, UNCX_BSC_PANCAKE_V2_LOCKER,
                                            _selector(rpc, "uniswapFactory()"), block_tag))
        if locker_factory != PANCAKE_V2_FACTORY:
            raise VerificationError("factory configurada no locker não é PancakeSwap V2")
        block_data = rpc.call("eth_getBlockByNumber", [block_tag, False])
        if not isinstance(block_data, dict) or str(block_data.get("hash", "")).lower() != str(block["hash"]).lower():
            raise VerificationError("hash do bloco mudou durante a leitura")
        block_timestamp = int(block_data.get("timestamp", "0x0"), 16)
        count = _uint_call(rpc, UNCX_BSC_PANCAKE_V2_LOCKER,
                           "getNumLocksForToken(address)", [pool_address], block_tag)
        if count < 1:
            raise VerificationError("locker não registra locks para este LP")
        if count > int(max_lock_records) or count > DEFAULT_MAX_LOCK_RECORDS:
            raise VerificationError("quantidade de locks excede limite de leitura segura")
        total_supply = _uint_call(rpc, pool_address, "totalSupply()", [], block_tag)
        locker_balance = _uint_call(rpc, pool_address, "balanceOf(address)",
                                    [UNCX_BSC_PANCAKE_V2_LOCKER], block_tag)
        if total_supply <= 0:
            raise VerificationError("oferta total LP nula")
        locked_active = 0
        active_records = []
        all_records = []
        for index in range(count):
            values = _tuple_call(
                rpc, UNCX_BSC_PANCAKE_V2_LOCKER, "tokenLocks(address,uint256)",
                [pool_address, index], block_tag, words=6,
            )
            lock_date, amount, initial_amount, unlock_date, lock_id, owner = values
            if amount > initial_amount or (amount > 0 and lock_date <= 0):
                raise VerificationError("registro de lock inconsistente")
            entry = {
                "index": index, "lock_date": lock_date, "amount_raw": str(amount),
                "initial_amount_raw": str(initial_amount), "unlock_timestamp": unlock_date,
                "lock_id": str(lock_id), "owner": owner,
            }
            all_records.append(entry)
            if amount > 0 and unlock_date > block_timestamp:
                locked_active += amount
                active_records.append(entry)
        if locker_balance < locked_active:
            raise VerificationError("saldo LP do locker inferior à soma dos locks ativos")
        protected_fraction = locked_active / total_supply
        if protected_fraction < 0.999:
            raise VerificationError("fração LP com lock ativo inferior a 99,9%")
        if not active_records:
            raise VerificationError("nenhum lock ativo com prazo futuro")
        lock_transactions = _find_deposit_transactions(
            rpc, pool_address, active_records, block_number, lookback_blocks
        )
        record.update({
            "status": "lock_verified_transaction_linked",
            "reason": None,
            "chain_id": str(chain_id),
            "pool_factory": pool_factory,
            "locker_factory": locker_factory,
            "locker_runtime_code_hash": actual_hash.lower(),
            "reserve_block_number": block_number,
            "reserve_block_hash": str(block["hash"]).lower(),
            "reserve_block_timestamp": block_timestamp,
            "lp_total_supply_raw": str(total_supply),
            "locker_lp_balance_raw": str(locker_balance),
            "locked_lp_active_raw": str(locked_active),
            "protected_fraction": protected_fraction,
            "lock_records_total": count,
            "lock_records_active": len(active_records),
            "lock_records": all_records,
            "lock_transactions": lock_transactions,
            "lock_transaction": lock_transactions[0]["transaction_hash"] if len(lock_transactions) == 1 else [x["transaction_hash"] for x in lock_transactions],
            "lock_contract": UNCX_BSC_PANCAKE_V2_LOCKER,
        })
    except Exception as exc:
        record["reason"] = str(exc)[:500]
    record["duration_ms"] = round((time.monotonic() - started) * 1000, 2)
    try:
        _audit(record, audit_path)
    except OSError:
        record["status"] = "blocked"
        record["reason"] = "falha ao persistir auditoria do lock"
    return record


def build_bsc_pancakeswap_v2_lp_proof(pool_address, rpc, allowed_factories=None,
                                      expected_locker_code_hash=None, audit_path=None,
                                      liquidity_usd=None):
    """Combine independent pool/reserve proof with the specific UNCX LP lock."""
    snapshot = verify_v2_pool_reserves(
        "bsc", pool_address,
        allowed_factories=allowed_factories or [PANCAKE_V2_FACTORY],
        audit_path=audit_path, rpc=rpc,
    )
    if snapshot.get("status") != "reserve_verified_lock_unverified":
        return snapshot
    lock = verify_uncx_bsc_pancakeswap_v2_lock(
        pool_address, rpc, expected_locker_code_hash, audit_path,
        block_number=snapshot.get("reserve_block_number"),
    )
    if lock.get("status") != "lock_verified_transaction_linked":
        snapshot["reason"] = lock.get("reason") or "lock UNCX não verificado"
        snapshot["lock_verification"] = lock
        _audit(snapshot, audit_path)
        return snapshot
    if lock.get("reserve_block_number") != snapshot.get("reserve_block_number") or (
        lock.get("reserve_block_hash") != snapshot.get("reserve_block_hash")
    ):
        snapshot["reason"] = "snapshot de reservas e lock não pertencem ao mesmo bloco"
        snapshot["lock_verification"] = lock
        _audit(snapshot, audit_path)
        return snapshot
    try:
        fresh_liquidity = float(liquidity_usd)
    except (TypeError, ValueError, OverflowError):
        fresh_liquidity = 0.0
    if fresh_liquidity < 25000:
        snapshot["reason"] = "liquidez fresca ausente ou inferior a US$ 25.000"
        snapshot["lock_verification"] = lock
        _audit(snapshot, audit_path)
        return snapshot
    snapshot.update({
        "status": "verified",
        "reason": None,
        "source": "EVM_JSON_RPC",
        "network": "bsc",
        "pool_address": _address(pool_address),
        "pool_factory": lock["pool_factory"],
        "lock_contract": lock["lock_contract"],
        "pool_factory": lock["pool_factory"],
        "locker_factory": lock["locker_factory"],
        "lock_records": lock["lock_records"],
        "lock_transactions": lock["lock_transactions"],
        "lock_transaction": lock["lock_transaction"],
        "lock_evidence_type": "UNCX_V2_storage_balance_event_and_receipt",
        "protected_fraction": lock["protected_fraction"],
        "locked_lp_raw": lock["locked_lp_active_raw"],
        "locker_lp_balance_raw": lock["locker_lp_balance_raw"],
        "locker_runtime_code_hash": lock["locker_runtime_code_hash"],
        "liquidity_usd": fresh_liquidity,
    })
    _audit(snapshot, audit_path)
    return snapshot
