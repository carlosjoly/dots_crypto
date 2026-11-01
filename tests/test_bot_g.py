import unittest
from datetime import datetime, timezone
from app.social_sentiment import evaluate_social_metrics


class BotGMetricTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {"social": {
            "max_data_age_minutes": 90, "min_source_count": 2,
            "min_unique_authors": 25, "max_bot_ratio_pct": 25,
            "volume_surge_pct": 50, "sentiment_shift_points": 8,
        }}
        self.data = {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "social_volume": 1800, "previous_social_volume": 1000,
            "sentiment_score": 68, "previous_sentiment_score": 55,
            "unique_authors": 120, "bot_ratio": 8,
            "source_count": 3, "sources": ["network_a", "network_b", "experts"],
        }

    def test_detects_volume_surge_and_positive_sentiment_shift(self):
        result = evaluate_social_metrics(self.data, self.cfg)
        self.assertEqual(result["direction"], "positiva")
        self.assertEqual(result["volume_change_pct"], 80.0)
        self.assertEqual(result["sentiment_change"], 13.0)

    def test_no_alert_when_only_volume_surges(self):
        self.data["sentiment_score"] = 58
        self.assertIsNone(evaluate_social_metrics(self.data, self.cfg))

    def test_blocks_high_bot_ratio(self):
        self.data["bot_ratio"] = 40
        with self.assertRaisesRegex(ValueError, "bots/spam"):
            evaluate_social_metrics(self.data, self.cfg)

    def test_blocks_insufficient_sources(self):
        self.data["sources"] = ["one"]
        with self.assertRaisesRegex(ValueError, "diversidade"):
            evaluate_social_metrics(self.data, self.cfg)

    def test_blocks_stale_data(self):
        self.data["observed_at"] = "2020-01-01T00:00:00+00:00"
        with self.assertRaisesRegex(ValueError, "desatualizados"):
            evaluate_social_metrics(self.data, self.cfg)

    def test_blocks_missing_metrics(self):
        del self.data["previous_sentiment_score"]
        with self.assertRaisesRegex(ValueError, "previous_sentiment_score"):
            evaluate_social_metrics(self.data, self.cfg)


if __name__ == "__main__":
    unittest.main()
