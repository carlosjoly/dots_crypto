import unittest
from unittest.mock import patch

from scripts.validate_bot_f_uncx_adapter import validate_pool
from app.uncx_bsc_lock import (
    PANCAKE_V2_FACTORY,
    PINNED_UNCX_LOCKER_RUNTIME_CODE_HASH,
    UNCX_BSC_PANCAKE_V2_LOCKER,
)


POOL = "0x" + "a" * 40
BLOCK_HASH = "0x" + "1" * 64
TX_HASH = "0x" + "d" * 64


class BotFBscLiveValidationTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {
            "status": "reserve_verified_lock_unverified",
            "chain_id": "56",
            "pool_address": POOL,
            "factory": PANCAKE_V2_FACTORY,
            "token0": "0x" + "2" * 40,
            "token1": "0x" + "3" * 40,
            "reserve0_raw": "123456",
            "reserve1_raw": "654321",
            "lp_total_supply_raw": "777777",
            "reserve_block_number": 133559941,
            "reserve_block_hash": BLOCK_HASH,
        }
        self.lock_identity = {
            "chain_id": "56",
            "pool_factory": PANCAKE_V2_FACTORY,
            "locker_factory": PANCAKE_V2_FACTORY,
            "lock_contract": UNCX_BSC_PANCAKE_V2_LOCKER,
            "locker_runtime_code_hash": PINNED_UNCX_LOCKER_RUNTIME_CODE_HASH,
            "reserve_block_number": 133559941,
            "reserve_block_hash": BLOCK_HASH,
        }

    @patch("scripts.validate_bot_f_uncx_adapter.verify_uncx_bsc_pancakeswap_v2_lock")
    @patch("scripts.validate_bot_f_uncx_adapter.verify_v2_pool_reserves")
    def test_no_active_lock_result_includes_exact_pool_reserves_and_same_block(
        self, verify_reserves, verify_lock
    ):
        verify_reserves.return_value = dict(self.snapshot)
        verify_lock.return_value = dict(
            self.lock_identity,
            status="blocked",
            reason="locker não registra locks para este LP",
        )

        result = validate_pool(object(), POOL)

        self.assertEqual(result["status"], "no_active_locks_confirmed")
        self.assertFalse(result["approval"])
        self.assertEqual(result["pool_identity"]["pool_address"], POOL)
        self.assertEqual(result["pool_identity"]["reserve0_raw"], "123456")
        self.assertEqual(result["pool_identity"]["reserve1_raw"], "654321")
        self.assertTrue(result["pool_identity"]["factory_get_pair_matches_pool"])
        verify_reserves.assert_called_once()
        verify_lock.assert_called_once()
        self.assertEqual(verify_lock.call_args.kwargs["block_number"], 133559941)

    @patch("scripts.validate_bot_f_uncx_adapter.verify_uncx_bsc_pancakeswap_v2_lock")
    @patch("scripts.validate_bot_f_uncx_adapter.verify_v2_pool_reserves")
    def test_reserve_identity_failure_blocks_locker_probe(self, verify_reserves, verify_lock):
        verify_reserves.return_value = {
            "status": "blocked",
            "reason": "factory.getPair não confirma o pool exato",
        }

        result = validate_pool(object(), POOL)

        self.assertEqual(result["status"], "inconclusive")
        self.assertFalse(result["approval"])
        verify_lock.assert_not_called()

    @patch("scripts.validate_bot_f_uncx_adapter.verify_uncx_bsc_pancakeswap_v2_lock")
    @patch("scripts.validate_bot_f_uncx_adapter.verify_v2_pool_reserves")
    def test_lock_from_different_block_is_inconclusive(self, verify_reserves, verify_lock):
        verify_reserves.return_value = dict(self.snapshot)
        verify_lock.return_value = dict(
            self.lock_identity,
            reserve_block_number=133559942,
            status="blocked",
            reason="locker não registra locks para este LP",
        )

        result = validate_pool(object(), POOL)

        self.assertEqual(result["status"], "inconclusive")
        self.assertFalse(result["approval"])

    @patch("scripts.validate_bot_f_uncx_adapter.verify_uncx_bsc_pancakeswap_v2_lock")
    @patch("scripts.validate_bot_f_uncx_adapter.verify_v2_pool_reserves")
    def test_active_lock_diagnostic_requires_protected_fraction_and_transaction(
        self, verify_reserves, verify_lock
    ):
        verify_reserves.return_value = dict(self.snapshot)
        verify_lock.return_value = dict(
            self.lock_identity,
            status="lock_verified_transaction_linked",
            protected_fraction=0.9995,
            locked_lp_active_raw="9995",
            locker_lp_balance_raw="9995",
            lp_total_supply_raw="10000",
            lock_records_total=1,
            lock_records_active=1,
            lock_transactions=[{"transaction_hash": TX_HASH}],
        )

        result = validate_pool(object(), POOL)

        self.assertEqual(result["status"], "active_lock_verified")
        self.assertFalse(result["approval"])
        self.assertEqual(
            result["lock_evidence"]["lock_transactions"][0]["transaction_hash"],
            TX_HASH,
        )

    @patch("scripts.validate_bot_f_uncx_adapter.verify_uncx_bsc_pancakeswap_v2_lock")
    @patch("scripts.validate_bot_f_uncx_adapter.verify_v2_pool_reserves")
    def test_active_lock_without_sufficient_protection_is_inconclusive(
        self, verify_reserves, verify_lock
    ):
        verify_reserves.return_value = dict(self.snapshot)
        verify_lock.return_value = dict(
            self.lock_identity,
            status="lock_verified_transaction_linked",
            protected_fraction=0.99,
            lock_transactions=[{"transaction_hash": TX_HASH}],
        )

        result = validate_pool(object(), POOL)

        self.assertEqual(result["status"], "inconclusive")
        self.assertFalse(result["approval"])


if __name__ == "__main__":
    unittest.main()
