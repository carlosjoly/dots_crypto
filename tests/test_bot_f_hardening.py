import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.new_launches import (
    _load_seen,
    _prune_api_metrics,
    scan_new_launches,
)


class BotFAdapterScopeTests(unittest.TestCase):
    def _pool(self, network, dex):
        now = datetime.now(timezone.utc)
        return {
            "pool_id": f"{network}_0x" + "f" * 40,
            "pool_address": "0x" + "f" * 40,
            "created_at": (now - timedelta(minutes=30)).isoformat(),
            "liquidity_usd": "50000",
            "volume_24h_usd": "25000",
            "txns_24h": {"buys": 40, "sells": 35},
            "base": {"symbol": "NEW", "name": "New Token", "address": "0x" + "2" * 40},
            "quote": {"symbol": "WBNB" if network == "bsc" else "USDC",
                      "address": "0x" + "1" * 40},
            "base_price_usd": "0.25",
            "quote_price_usd": "1",
            "dex": dex,
            "network": network,
        }

    def _cfg(self, root, network):
        return {
            "_root": str(root),
            "_env": {"goplus_enabled": True},
            "launches": {
                "networks": [network],
                "alert_eligible_dexes": ["Pancakeswap V2 (BSC)"],
                "quote_symbols": ["USDC", "USDT", "WBNB"],
                "min_pool_age_minutes": 5,
                "max_pool_age_hours": 48,
                "min_liquidity_usd": 25000,
                "min_volume_24h_usd": 10000,
                "min_txns_24h": 50,
                "min_holder_count": 10,
                "max_security_checks_per_cycle": 5,
                "max_buy_sell_tax": 0.05,
                "seen_state_file": "data/seen.json",
                "review_state_file": "data/review.json",
                "review_recheck_minutes": 30,
            },
        }

    def _assert_candidate_is_not_consumed(self, network, dex):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = self._cfg(root, network)
            pool = self._pool(network, dex)
            with (
                patch("app.new_launches.fetch_new_pools", return_value=[pool]),
                patch("app.new_launches.goplus_token_security") as goplus,
                patch("app.new_launches.time.sleep"),
                patch("app.new_launches.POOL_DIAGNOSTICS_PATH", root / "diagnostics.json"),
                patch("app.new_launches.API_METRICS_PATH", root / "metrics.jsonl"),
            ):
                alerts = scan_new_launches(cfg)
            self.assertEqual(alerts, [])
            goplus.assert_not_called()
            self.assertEqual(_load_seen(root / "data" / "seen.json"), set())
            diagnostic = json.loads((root / "diagnostics.json").read_text(encoding="utf-8"))
            record = next(item for item in diagnostic["pools"] if item["pool_id"] == pool["pool_id"])
            self.assertEqual(record["market_filter_result"], "unsupported_adapter")
            self.assertIn("fora da deduplicação", record["market_filter_reason"])

    def test_non_bsc_candidate_is_not_sent_to_goplus_or_marked_seen(self):
        self._assert_candidate_is_not_consumed("eth", "Uniswap V2")

    def test_non_pancakeswap_v2_candidate_on_bsc_is_not_consumed(self):
        self._assert_candidate_is_not_consumed("bsc", "Pancakeswap V3 (BSC)")


class BotFTelemetryRetentionTests(unittest.TestCase):
    def test_prune_removes_expired_malformed_and_excess_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.jsonl"
            now = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
            rows = [
                {"timestamp_utc": (now - timedelta(days=9)).isoformat(), "status": 200},
                {"timestamp_utc": (now - timedelta(hours=1)).isoformat(), "status": 200},
                {"timestamp_utc": (now - timedelta(minutes=30)).isoformat(), "status": 429},
                {"timestamp_utc": (now - timedelta(minutes=5)).isoformat(), "status": 200},
            ]
            path.write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n" + "not-json\n",
                encoding="utf-8",
            )
            with (
                patch("app.new_launches.API_METRICS_PATH", path),
                patch("app.new_launches.API_METRICS_RETENTION_DAYS", 7),
                patch("app.new_launches.API_METRICS_MAX_LINES", 2),
                patch("app.new_launches.API_METRICS_MAX_BYTES", 10000),
            ):
                removed = _prune_api_metrics(now)
            retained = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(removed, 3)
            self.assertEqual([row["status"] for row in retained], [429, 200])
            self.assertLessEqual(len(retained), 2)

    def test_prune_enforces_byte_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.jsonl"
            now = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
            rows = [
                {"timestamp_utc": (now - timedelta(minutes=i)).isoformat(), "payload": "x" * 180}
                for i in range(5)
            ]
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            with (
                patch("app.new_launches.API_METRICS_PATH", path),
                patch("app.new_launches.API_METRICS_RETENTION_DAYS", 7),
                patch("app.new_launches.API_METRICS_MAX_LINES", 5000),
                patch("app.new_launches.API_METRICS_MAX_BYTES", 600),
            ):
                _prune_api_metrics(now)
            self.assertLessEqual(path.stat().st_size, 600)
            retained = path.read_text(encoding="utf-8").splitlines()
            self.assertTrue(retained)


if __name__ == "__main__":
    unittest.main()
