import unittest
from unittest.mock import patch

import pandas as pd

from app.market_data import get_book_ticker_details, validate_candle_sequence


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

    def test_regular_candle_sequence_passes(self):
        times = pd.date_range("2026-11-05T00:00:00Z", periods=4, freq="15min")
        frame = pd.DataFrame({"open_time": times})
        self.assertIs(validate_candle_sequence(frame, "15m", "BTCUSDT"), frame)

    def test_missing_interval_is_rejected_instead_of_filled(self):
        times = pd.to_datetime([
            "2026-11-05T00:00:00Z",
            "2026-11-05T00:15:00Z",
            "2026-11-05T00:45:00Z",
        ])
        frame = pd.DataFrame({"open_time": times})
        with self.assertRaisesRegex(ValueError, "Candle gap"):
            validate_candle_sequence(frame, "15m", "BTCUSDT")

    def test_duplicate_candles_are_rejected(self):
        times = pd.to_datetime([
            "2026-11-05T00:00:00Z",
            "2026-11-05T00:15:00Z",
            "2026-11-05T00:15:00Z",
        ])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            validate_candle_sequence(pd.DataFrame({"open_time": times}), "15m")

    def test_unordered_candles_are_rejected(self):
        times = pd.to_datetime([
            "2026-11-05T00:15:00Z",
            "2026-11-05T00:00:00Z",
        ])
        with self.assertRaisesRegex(ValueError, "Unordered"):
            validate_candle_sequence(pd.DataFrame({"open_time": times}), "15m")


if __name__ == "__main__":
    unittest.main()
