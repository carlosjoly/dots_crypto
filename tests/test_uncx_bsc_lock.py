import json
import tempfile
import unittest
from pathlib import Path

from app.uncx_bsc_lock import (
    PANCAKE_V2_FACTORY,
    UNCX_BSC_PANCAKE_V2_LOCKER,
    build_bsc_pancakeswap_v2_lp_proof,
    verify_uncx_bsc_pancakeswap_v2_lock,
)

POOL = "0x" + "a" * 40
TOKEN0 = "0x" + "c" * 40
TOKEN1 = "0x" + "d" * 40
OWNER = "0x" + "e" * 40
CODE_HASH = "0x" + "9" * 64
LOCKER_CODE = "0x60016000"
SELECTORS = {
    "uniswapFactory()": "11111111",
    "getNumLocksForToken(address)": "22222222",
    "tokenLocks(address,uint256)": "33333333",
    "totalSupply()": "18160ddd",
    "balanceOf(address)": "70a08231",
}


def word(n):
    return format(n, "064x")


def address_word(value):
    return "0x" + value[2:].lower().rjust(64, "0")


class FakeRpc:
    def __init__(self, chain="0x38", pair_factory=PANCAKE_V2_FACTORY,
                 locker_factory=PANCAKE_V2_FACTORY, amount=1000000,
                 unlock=1900000000, locker_code=LOCKER_CODE,
                 receipt_status="0x1", omit_deposit=False):
        self.chain = chain
        self.pair_factory = pair_factory
        self.locker_factory = locker_factory
        self.amount = amount
        self.unlock = unlock
        self.locker_code = locker_code
        self.receipt_status = receipt_status
        self.omit_deposit = omit_deposit
        self.calls = []

    def call(self, method, params):
        self.calls.append((method, params))
        if method == "eth_chainId":
            return self.chain
        if method == "eth_getBlockByNumber":
            return {"number": "0x123", "hash": "0x" + "1" * 64,
                    "timestamp": "0x67200000"}
        if method == "eth_getCode":
            return self.locker_code if params[0].lower() == UNCX_BSC_PANCAKE_V2_LOCKER else "0x60016000"
        if method == "web3_sha3":
            raw = params[0]
            if raw == self.locker_code:
                return CODE_HASH
            signature = bytes.fromhex(raw[2:]).decode("ascii")
            selector = SELECTORS.get(signature, "abcdef01")
            return "0x" + selector + "0" * 56
        if method == "eth_getLogs":
            if self.omit_deposit:
                return []
            event_topic = "0xabcdef01" + "0" * 56
            event_data = "0x" + address_word(POOL)[2:] + address_word(OWNER)[2:] + word(1000000) + word(1700000000) + word(1900000000)
            from_block = int(params[0]["fromBlock"], 16)
            to_block = int(params[0]["toBlock"], 16)
            if from_block <= 0x120 <= to_block and params[0]["topics"] == [event_topic]:
                return [{
                    "address": UNCX_BSC_PANCAKE_V2_LOCKER,
                    "topics": [event_topic],
                    "data": event_data,
                    "transactionHash": "0x" + "2" * 64,
                    "blockNumber": "0x120",
                }]
            return []
        if method == "eth_getTransactionReceipt":
            event_topic = "0xabcdef01" + "0" * 56
            event_data = "0x" + address_word(POOL)[2:] + address_word(OWNER)[2:] + word(1000000) + word(1700000000) + word(1900000000)
            return {
                "status": self.receipt_status,
                "blockNumber": "0x120",
                "logs": [{
                    "address": UNCX_BSC_PANCAKE_V2_LOCKER,
                    "topics": [event_topic],
                    "data": event_data,
                }],
            }
        if method == "eth_call":
            to = params[0]["to"].lower()
            data = params[0]["data"]
            if data == "0xc45a0155":
                return address_word(self.pair_factory)
            if data == "0x0dfe1681":
                return address_word(TOKEN0)
            if data == "0xd21220a7":
                return address_word(TOKEN1)
            if data.startswith("0xe6a43905"):
                return address_word(POOL)
            if data == "0x0902f1ac":
                return "0x" + word(5000000) + word(7000000) + word(1700000000)
            if data == "0x18160ddd":
                return "0x" + word(1000000)
            if to == UNCX_BSC_PANCAKE_V2_LOCKER and data.startswith("0x11111111"):
                return address_word(self.locker_factory)
            if to == UNCX_BSC_PANCAKE_V2_LOCKER and data.startswith("0x22222222"):
                return "0x" + word(1)
            if to == UNCX_BSC_PANCAKE_V2_LOCKER and data.startswith("0x33333333"):
                values = [1700000000, self.amount, 1000000, self.unlock, 7]
                return "0x" + "".join(word(x) for x in values) + word(int(OWNER, 16))
            if to == POOL and data.startswith("0x70a08231"):
                return "0x" + word(self.amount)
        raise AssertionError((method, params))


class UncxBscLockTests(unittest.TestCase):
    def test_lock_storage_and_transaction_event_require_pinned_runtime_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = verify_uncx_bsc_pancakeswap_v2_lock(
                POOL, FakeRpc(), expected_locker_code_hash=CODE_HASH,
                audit_path=Path(tmp) / "audit.jsonl", block_number=0x123,
            )
            self.assertEqual(result["status"], "lock_verified_transaction_linked")
            self.assertEqual(result["protected_fraction"], 1.0)
            self.assertEqual(result["lock_records_active"], 1)
            self.assertEqual(result["reserve_block_number"], 0x123)
            saved = json.loads((Path(tmp) / "audit.jsonl").read_text().splitlines()[0])
            self.assertEqual(saved["status"], "lock_verified_transaction_linked")

    def test_missing_runtime_hash_pin_fails_closed(self):
        result = verify_uncx_bsc_pancakeswap_v2_lock(POOL, FakeRpc())
        self.assertEqual(result["status"], "blocked")
        self.assertIn("hash de runtime", result["reason"])

    def test_wrong_runtime_hash_fails_closed(self):
        result = verify_uncx_bsc_pancakeswap_v2_lock(
            POOL, FakeRpc(), expected_locker_code_hash="0x" + "8" * 64,
        )
        self.assertEqual(result["status"], "blocked")
        self.assertIn("code hash", result["reason"])

    def test_wrong_chain_fails_closed(self):
        result = verify_uncx_bsc_pancakeswap_v2_lock(
            POOL, FakeRpc(chain="0x1"), expected_locker_code_hash=CODE_HASH,
        )
        self.assertEqual(result["status"], "blocked")
        self.assertIn("chainId 56", result["reason"])

    def test_wrong_locker_factory_fails_closed(self):
        result = verify_uncx_bsc_pancakeswap_v2_lock(
            POOL, FakeRpc(locker_factory="0x" + "f" * 40),
            expected_locker_code_hash=CODE_HASH,
        )
        self.assertEqual(result["status"], "blocked")
        self.assertIn("factory configurada", result["reason"])

    def test_expired_lock_not_counted(self):
        result = verify_uncx_bsc_pancakeswap_v2_lock(
            POOL, FakeRpc(unlock=1), expected_locker_code_hash=CODE_HASH,
        )
        self.assertEqual(result["status"], "blocked")
        self.assertIn("inferior a 99,9%", result["reason"])

    def test_underlocked_lp_fails_closed(self):
        result = verify_uncx_bsc_pancakeswap_v2_lock(
            POOL, FakeRpc(amount=998000), expected_locker_code_hash=CODE_HASH,
        )
        self.assertEqual(result["status"], "blocked")
        self.assertIn("inferior a 99,9%", result["reason"])

    def test_combined_proof_approves_only_with_transaction_receipt_and_fresh_liquidity(self):
        with tempfile.TemporaryDirectory() as tmp:
            rpc = FakeRpc()
            result = build_bsc_pancakeswap_v2_lp_proof(
                POOL, rpc, expected_locker_code_hash=CODE_HASH,
                audit_path=Path(tmp) / "audit.jsonl", liquidity_usd=30000,
            )
            self.assertEqual(result["status"], "verified")
            self.assertEqual(result["liquidity_usd"], 30000)
            self.assertTrue(result["lock_transaction"])
            self.assertEqual(result["lock_evidence_type"], "UNCX_V2_storage_balance_event_and_receipt")


    def test_missing_deposit_event_fails_closed(self):
        result = verify_uncx_bsc_pancakeswap_v2_lock(
            POOL, FakeRpc(omit_deposit=True), expected_locker_code_hash=CODE_HASH,
        )
        self.assertEqual(result["status"], "blocked")
        self.assertIn("evento onDeposit único", result["reason"])

    def test_failed_transaction_receipt_fails_closed(self):
        result = verify_uncx_bsc_pancakeswap_v2_lock(
            POOL, FakeRpc(receipt_status="0x0"), expected_locker_code_hash=CODE_HASH,
        )
        self.assertEqual(result["status"], "blocked")
        self.assertIn("recibo", result["reason"])

    def test_missing_fresh_liquidity_keeps_combined_proof_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = build_bsc_pancakeswap_v2_lp_proof(
                POOL, FakeRpc(), expected_locker_code_hash=CODE_HASH,
                audit_path=Path(tmp) / "audit.jsonl",
            )
        self.assertEqual(result["status"], "reserve_verified_lock_unverified")
        self.assertIn("liquidez fresca", result["reason"])

    def test_lock_reads_are_pinned_to_pool_snapshot_block(self):
        rpc = FakeRpc()
        with tempfile.TemporaryDirectory() as tmp:
            build_bsc_pancakeswap_v2_lp_proof(
                POOL, rpc, expected_locker_code_hash=CODE_HASH,
                audit_path=Path(tmp) / "audit.jsonl",
            )
        locker_calls = [p for method, p in rpc.calls if method == "eth_call"
                        and p[0]["to"].lower() == UNCX_BSC_PANCAKE_V2_LOCKER]
        self.assertTrue(locker_calls)
        self.assertTrue(all(p[1] == "0x123" for p in locker_calls))


if __name__ == "__main__":
    unittest.main()
