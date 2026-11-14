import unittest

from scripts.diagnose_uncx_bsc_v21 import (
    LOCKER,
    ON_DEPOSIT_LEGACY,
    decode_legacy_deposit,
    decode_legacy_lock,
    legacy_event_matches,
    DiagnosticError,
)


def word(value):
    return format(int(value), "064x")


def address_word(value):
    return word(int(value, 16))


class UncxBscLegacyAbiTests(unittest.TestCase):
    def test_decodes_six_field_token_locks_return(self):
        owner = "0x" + "b" * 40
        raw = "0x" + "".join([
            word(1700000000),
            word(900),
            word(1000),
            word(1900000000),
            word(42),
            address_word(owner),
        ])
        lock = decode_legacy_lock(raw)
        self.assertEqual(lock["lock_date"], 1700000000)
        self.assertEqual(lock["amount_raw"], "900")
        self.assertEqual(lock["initial_amount_raw"], "1000")
        self.assertEqual(lock["unlock_date"], 1900000000)
        self.assertEqual(lock["lock_id"], 42)
        self.assertEqual(lock["owner"], owner)

    def test_rejects_eight_field_v21_tuple_as_legacy_abi(self):
        raw = "0x" + "".join(word(i) for i in range(8))
        with self.assertRaises(DiagnosticError):
            decode_legacy_lock(raw)

    def test_decodes_legacy_deposit_event_without_indexed_arguments(self):
        pool = "0x" + "a" * 40
        user = "0x" + "b" * 40
        log = {
            "address": LOCKER,
            "topics": [ON_DEPOSIT_LEGACY],
            "data": "0x" + "".join([
                address_word(pool), address_word(user), word(1000),
                word(1700000000), word(1900000000),
            ]),
            "transactionHash": "0x" + "c" * 64,
            "blockNumber": "0x123",
            "logIndex": "0x2",
        }
        event = decode_legacy_deposit(log)
        self.assertEqual(event["lp_token"], pool)
        self.assertEqual(event["user"], user)
        self.assertEqual(event["amount_raw"], "1000")
        self.assertEqual(event["lock_date"], 1700000000)
        self.assertEqual(event["unlock_date"], 1900000000)

    def test_legacy_event_matching_requires_exact_pool_and_dates(self):
        pool = "0x" + "a" * 40
        record = {
            "lock_date": 1700000000,
            "unlock_date": 1900000000,
            "amount_raw": "900",
            "initial_amount_raw": "1000",
        }
        event = {
            "lp_token": pool,
            "lock_date": 1700000000,
            "unlock_date": 1900000000,
            "amount_raw": "1000",
        }
        self.assertTrue(legacy_event_matches(record, event, pool))
        event["lp_token"] = "0x" + "d" * 40
        self.assertFalse(legacy_event_matches(record, event, pool))
        event["lp_token"] = pool
        event["unlock_date"] += 1
        self.assertFalse(legacy_event_matches(record, event, pool))


if __name__ == "__main__":
    unittest.main()
