import json
import tempfile
import unittest
from pathlib import Path
from app.onchain_lp import build_independent_lp_proof, verify_v2_pool_reserves

POOL, FACTORY = "0x" + "a" * 40, "0x" + "b" * 40
TOKEN0, TOKEN1 = "0x" + "c" * 40, "0x" + "d" * 40
LOCK = "0x" + "e" * 40

def word(n): return format(n, "064x")
def address_word(value): return "0x" + value[2:].lower().rjust(64, "0")

class FakeRpc:
    def __init__(self, chain="0x2105", factory=FACTORY, pair=POOL, reserves=(1000000, 2000000)):
        self.chain, self.factory, self.pair, self.reserves = chain, factory, pair, reserves
        self.calls = []
    def call(self, method, params):
        self.calls.append((method, params))
        if method == "eth_chainId": return self.chain
        if method == "eth_getBlockByNumber":
            return {"number": "0x123", "hash": "0x" + "1" * 64, "timestamp": "0x67200000"}
        if method == "eth_getCode": return "0x60016000"
        if method == "eth_call":
            data = params[0]["data"]
            if data == "0xc45a0155": return address_word(self.factory)
            if data == "0x0dfe1681": return address_word(TOKEN0)
            if data == "0xd21220a7": return address_word(TOKEN1)
            if data.startswith("0xe6a43905"): return address_word(self.pair)
            if data == "0x0902f1ac":
                return "0x" + word(self.reserves[0]) + word(self.reserves[1]) + word(1700000000)
            if data == "0x18160ddd": return "0x" + word(1000000)
        raise AssertionError((method, params))

class OnchainLpTests(unittest.TestCase):
    def test_v2_identity_and_reserves_use_same_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            rpc = FakeRpc()
            result = verify_v2_pool_reserves("base", POOL, [FACTORY],
                audit_path=Path(tmp) / "audit.jsonl", rpc=rpc)
            self.assertEqual(result["status"], "reserve_verified_lock_unverified")
            self.assertEqual(result["chain_id"], "8453")
            self.assertEqual(result["factory"], FACTORY)
            self.assertEqual(result["reserve0_raw"], "1000000")
            self.assertEqual(result["reserve_block_number"], 0x123)
            self.assertTrue(all(c[1][-1] == "0x123" for c in rpc.calls if c[0] == "eth_call"))
            audit = json.loads((Path(tmp) / "audit.jsonl").read_text().splitlines()[0])
            self.assertEqual(audit["status"], "reserve_verified_lock_unverified")
    def test_wrong_chain_fails_closed(self):
        result = verify_v2_pool_reserves("base", POOL, [FACTORY], rpc=FakeRpc(chain="0x1"))
        self.assertEqual(result["status"], "blocked")
        self.assertIn("chainId", result["reason"])
    def test_unallowlisted_factory_fails_closed(self):
        result = verify_v2_pool_reserves("base", POOL, ["0x" + "f" * 40], rpc=FakeRpc())
        self.assertEqual(result["status"], "blocked")
        self.assertIn("allowlist", result["reason"])
    def test_factory_pair_mismatch_fails_closed(self):
        result = verify_v2_pool_reserves("base", POOL, [FACTORY],
            rpc=FakeRpc(pair="0x" + "f" * 40))
        self.assertEqual(result["status"], "blocked")
        self.assertIn("pool exato", result["reason"])
    def test_zero_reserves_fail_closed(self):
        result = verify_v2_pool_reserves("base", POOL, [FACTORY],
            rpc=FakeRpc(reserves=(0, 1)))
        self.assertEqual(result["status"], "blocked")
        self.assertIn("nulas", result["reason"])
    def test_reserves_never_imply_lp_lock(self):
        result = build_independent_lp_proof("base", POOL, [FACTORY], rpc=FakeRpc())
        self.assertEqual(result["status"], "reserve_verified_lock_unverified")
        self.assertNotIn("protected_fraction", result)
    def test_incomplete_lock_adapter_fails_closed(self):
        result = build_independent_lp_proof("base", POOL, [FACTORY],
            lock_adapter=lambda snapshot, rpc: {"lock_contract": LOCK}, rpc=FakeRpc())
        self.assertEqual(result["status"], "reserve_verified_lock_unverified")
        self.assertIn("todos os campos", result["reason"])
    def test_less_than_999_percent_locked_fails_closed(self):
        evidence = {"lock_contract": LOCK, "lock_transaction": "0x" + "2" * 64,
            "lock_block_number": 100, "unlock_timestamp": 1900000000,
            "locked_lp_raw": "998999", "lock_record_id": "fixture", "lock_record_verified": True}
        result = build_independent_lp_proof("base", POOL, [FACTORY],
            lock_adapter=lambda snapshot, rpc: evidence, rpc=FakeRpc())
        self.assertEqual(result["status"], "reserve_verified_lock_unverified")
        self.assertIn("99,9%", result["reason"])
    def test_expired_lock_fails_closed(self):
        evidence = {"lock_contract": LOCK, "lock_transaction": "0x" + "2" * 64,
            "lock_block_number": 100, "unlock_timestamp": 1,
            "locked_lp_raw": "1000000", "lock_record_id": "fixture", "lock_record_verified": True}
        result = build_independent_lp_proof("base", POOL, [FACTORY],
            lock_adapter=lambda snapshot, rpc: evidence, rpc=FakeRpc())
        self.assertEqual(result["status"], "reserve_verified_lock_unverified")
        self.assertIn("expirado", result["reason"])

if __name__ == "__main__": unittest.main()
