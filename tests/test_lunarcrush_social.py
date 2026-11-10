import unittest
from datetime import datetime, timezone
from unittest.mock import Mock

from app.lunarcrush_social import (
    LunarCrushAccessError,
    fetch_social_snapshots,
)


class LunarCrushFreeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 2, 17, 30, tzinfo=timezone.utc)
        self.session = Mock()
        self.response = Mock(status_code=200)
        self.response.json.return_value = {
            "config": {},
            "data": [
                {
                    "symbol": "BTC",
                    "social_volume_24h": 145953,
                    "sentiment": 73,
                    "last_updated_price": 1790000000,
                },
                {
                    "symbol": "LINK",
                    "social_volume_24h": 4658,
                    "sentiment": 81,
                },
            ],
        }
        self.session.get.return_value = self.response

    def test_fetches_all_symbols_in_one_bulk_request(self):
        result = fetch_social_snapshots(
            ["BTCUSDT", "LINKUSDT"], "test-secret",
            session=self.session, now=self.now,
        )
        self.session.get.assert_called_once()
        self.assertEqual(result["BTCUSDT"]["social_volume_24h"], 145953)
        self.assertEqual(result["LINKUSDT"]["sentiment"], 81)

    def test_does_not_invent_freshness_or_bot_metrics(self):
        result = fetch_social_snapshots(
            ["BTCUSDT"], "test-secret", session=self.session, now=self.now,
        )["BTCUSDT"]
        self.assertIsNone(result["source_updated_at"])
        self.assertFalse(result["source_freshness_verified"])
        self.assertIsNone(result["social_contributors"])
        self.assertIsNone(result["spam"])
        self.assertIsNone(result["bot_ratio_pct"])
        self.assertEqual(result["price_updated_at"], 1790000000)

    def test_uses_provider_generated_timestamp_when_available(self):
        self.response.json.return_value["config"]["generated"] = int(self.now.timestamp())
        result = fetch_social_snapshots(
            ["BTCUSDT"], "test-secret", session=self.session, now=self.now,
        )["BTCUSDT"]
        self.assertEqual(result["source_updated_at"], self.now.isoformat())
        self.assertTrue(result["source_freshness_verified"])
        self.assertIn("/coins/list/v2", self.session.get.call_args.args[0])

    def test_missing_key_is_blocked(self):
        with self.assertRaises(LunarCrushAccessError):
            fetch_social_snapshots(["BTCUSDT"], "", session=self.session)

    def test_403_is_classified_without_exposing_body(self):
        self.response.status_code = 403
        with self.assertRaisesRegex(LunarCrushAccessError, "HTTP 403"):
            fetch_social_snapshots(["BTCUSDT"], "test-secret", session=self.session)

    def test_rate_limit_is_classified(self):
        self.response.status_code = 429
        with self.assertRaisesRegex(LunarCrushAccessError, "HTTP 429"):
            fetch_social_snapshots(["BTCUSDT"], "test-secret", session=self.session)

    def test_invalid_social_metrics_are_rejected(self):
        self.response.json.return_value["data"][0]["sentiment"] = 120
        with self.assertRaisesRegex(ValueError, "sentiment above maximum"):
            fetch_social_snapshots(["BTCUSDT"], "test-secret", session=self.session)

    def test_duplicate_symbols_are_rejected(self):
        self.response.json.return_value["data"].append(
            {"symbol": "BTC", "social_volume_24h": 1, "sentiment": 50}
        )
        with self.assertRaisesRegex(ValueError, "duplicate symbol BTC"):
            fetch_social_snapshots(["BTCUSDT"], "test-secret", session=self.session)


if __name__ == "__main__":
    unittest.main()
