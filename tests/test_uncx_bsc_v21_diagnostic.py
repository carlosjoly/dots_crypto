import unittest

from scripts.diagnose_uncx_bsc_v21 import (
    ON_NEW_LOCK,
    decode_v21_new_lock,
    decode_words,
    address_arg,
    DiagnosticError,
)


def word(value):
    return format(int(value), "064x")


def topic_address(value):
    return "0x" + address_arg(value)


class UncxV21DiagnosticTests(unittest.TestCase):
    def test_decodes_indexed_v21_new_lock_event(self):
        pool = "0x" + "a" * 40
        owner = "0x" + "b" * 40
        lock_id = 73
        log = {
            "topics": [
                ON_NEW_LOCK,
                "0x" + word(lock_id),
                topic_address(pool),
                topic_address(owner),
            ],
            "data": "0x" + word(123456) + word(1700000000) + word(1900000000) + word(840),
            "transactionHash": "0x" + "c" * 64,
            "blockNumber": "0x1234",
            "logIndex": "0x2",
        }
        decoded = decode_v21_new_lock(log)
        self.assertEqual(decoded["lock_id"], lock_id)
        self.assertEqual(decoded["pool"], pool)
        self.assertEqual(decoded["owner"], owner)
        self.assertEqual(decoded["amount_raw"], "123456")
        self.assertEqual(decoded["lock_date"], 1700000000)
        self.assertEqual(decoded["unlock_date"], 1900000000)
        self.assertEqual(decoded["country_code"], 840)
        self.assertEqual(decoded["block_number"], 0x1234)

    def test_rejects_wrong_event_topic_or_topic_count(self):
        with self.assertRaises(DiagnosticError):
            decode_v21_new_lock({"topics": ["0x" + "0" * 64], "data": "0x"})

    def test_rejects_malformed_event_data(self):
        log = {
            "topics": [ON_NEW_LOCK, "0x" + word(1), topic_address("0x" + "a" * 40),
                       topic_address("0x" + "b" * 40)],
            "data": "0x" + word(1),
        }
        with self.assertRaises(DiagnosticError):
            decode_v21_new_lock(log)

    def test_decode_words_requires_exact_abi_word_count(self):
        with self.assertRaises(DiagnosticError):
            decode_words("0x" + word(1) + word(2), 1)

    def test_address_argument_is_strict(self):
        with self.assertRaises(DiagnosticError):
            address_arg("0x1234")


if __name__ == "__main__":
    unittest.main()
