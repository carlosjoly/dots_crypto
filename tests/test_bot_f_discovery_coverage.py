"""Regression tests for Bot F discovery coverage and pagination."""
import unittest
from unittest.mock import patch

from app.new_launches import fetch_discovery_pools


class DiscoveryCoverageTests(unittest.TestCase):
    @patch("app.new_launches.time.sleep")
    @patch("app.new_launches.fetch_new_pools")
    def test_fetches_bounded_pages_and_deduplicates_exact_pool_ids(self, fetch, _sleep):
        first = {"pool_id": "bsc_0x" + "a" * 40, "pool_address": "0x" + "a" * 40}
        duplicate = dict(first)
        second = {"pool_id": "bsc_0x" + "b" * 40, "pool_address": "0x" + "b" * 40}
        fetch.side_effect = [[first, duplicate], [duplicate, second]]
        result = fetch_discovery_pools("bsc", max_pages=2)
        self.assertEqual([pool["pool_id"] for pool in result], [first["pool_id"], second["pool_id"]])
        self.assertEqual([call.kwargs["page"] for call in fetch.call_args_list], [1, 2])
        self.assertEqual(_sleep.call_count, 1)

    @patch("app.new_launches.time.sleep")
    @patch("app.new_launches.fetch_new_pools")
    def test_stops_at_empty_page_and_caps_requested_pages(self, fetch, _sleep):
        fetch.side_effect = [[{"pool_id": "eth_pool"}], []]
        result = fetch_discovery_pools("eth", max_pages=99)
        self.assertEqual(len(result), 1)
        self.assertEqual([call.kwargs["page"] for call in fetch.call_args_list], [1, 2])

    @patch("app.new_launches.time.sleep")
    @patch("app.new_launches.fetch_new_pools")
    def test_uses_pool_address_fallback_for_deduplication(self, fetch, _sleep):
        pool_a = {"pool_address": "0x" + "c" * 40}
        pool_b = {"pool_address": "0x" + "c" * 40}
        fetch.side_effect = [[pool_a], [pool_b]]
        result = fetch_discovery_pools("bsc", max_pages=2)
        self.assertEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()
