import unittest
from unittest.mock import patch

from app.market_data import get_book_ticker_details


class MarketDataTests(unittest.TestCase):
    @patch("app.market_data.requests.get")
    def test_book_ticker_returns_bid_ask_and_spread_snapshot(self, get):
        class Response:
            def raise_for_status(self):
                return None
            def json(self):
                return {"bidPrice": "99.9", "askPrice": "100.1"}
        get.return_value = Response()
        result = get_book_ticker_details("https://example.test", "BTCUSDT")
        self.assertEqual(result["bid"], 99.9)
        self.assertEqual(result["ask"], 100.1)
        self.assertAlmostEqual(result["spread_pct"], 0.2)
        self.assertTrue(result["collected_at_utc"].endswith("+00:00"))

    @patch("app.market_data.requests.get")
    def test_invalid_order_book_is_rejected(self, get):
        class Response:
            def raise_for_status(self):
                return None
            def json(self):
                return {"bidPrice": "101", "askPrice": "100"}
        get.return_value = Response()
        with self.assertRaises(ValueError):
            get_book_ticker_details("https://example.test", "BTCUSDT")


if __name__ == "__main__":
    unittest.main()
