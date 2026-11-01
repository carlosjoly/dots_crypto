import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.new_launches import _market_eligibility, _select_token, _load_seen, _save_seen
from app.security import evaluate_launch_security


class BotFScreeningTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "launches": {
                "min_pool_age_minutes": 5,
                "max_pool_age_hours": 48,
                "min_liquidity_usd": 25000,
                "min_volume_24h_usd": 10000,
                "min_txns_24h": 50,
                "quote_symbols": ["USDC", "USDT", "WETH"],
            }
        }
        self.now = datetime(2026, 10, 31, 18, 0, tzinfo=timezone.utc)
        self.pool = {
            "created_at": (self.now - timedelta(minutes=30)).isoformat(),
            "liquidity_usd": "50000",
            "volume_24h_usd": "25000",
            "txns_24h": {"buys": 40, "sells": 35},
        }

    def test_selects_non_quote_token_and_valid_evm_address(self):
        pool = {
            "base": {"symbol": "USDC", "address": "0x" + "1" * 40},
            "quote": {"symbol": "NEW", "name": "New Token", "address": "0x" + "2" * 40},
            "quote_price_usd": "0.25",
        }
        token = _select_token(pool, ["USDC"])
        self.assertEqual(token["symbol"], "NEW")
        self.assertEqual(token["address"], "0x" + "2" * 40)
        self.assertEqual(token["price_usd"], 0.25)

    def test_market_filters_require_age_liquidity_volume_and_transactions(self):
        ok, _ = _market_eligibility(self.pool, self.cfg, self.now)
        self.assertTrue(ok)
        poor = dict(self.pool, liquidity_usd="100")
        ok, _ = _market_eligibility(poor, self.cfg, self.now)
        self.assertFalse(ok)
        missing = dict(self.pool, txns_24h={})
        ok, _ = _market_eligibility(missing, self.cfg, self.now)
        self.assertFalse(ok)

    def test_market_filter_rejects_too_new_or_stale_pools(self):
        new = dict(self.pool, created_at=(self.now - timedelta(minutes=1)).isoformat())
        stale = dict(self.pool, created_at=(self.now - timedelta(days=3)).isoformat())
        self.assertFalse(_market_eligibility(new, self.cfg, self.now)[0])
        self.assertFalse(_market_eligibility(stale, self.cfg, self.now)[0])

    def test_security_fails_closed_when_required_fields_are_unknown(self):
        result = evaluate_launch_security({})
        self.assertEqual(result["status"], "review_required")
        self.assertTrue(result["flags"])

    def test_security_rejects_honeypot_and_excessive_tax(self):
        data = {
            "is_honeypot": "1", "cannot_buy": "0", "cannot_sell_all": "0",
            "is_blacklisted": "0", "malicious_address": "0", "is_open_source": "1",
            "is_in_dex": "1", "buy_tax": "0.01", "sell_tax": "0.01",
        }
        self.assertEqual(evaluate_launch_security(data)["status"], "rejected")
        data["is_honeypot"] = "0"
        data["sell_tax"] = "0.08"
        self.assertEqual(evaluate_launch_security(data)["status"], "rejected")

    def test_security_approves_only_complete_clean_required_fields(self):
        data = {
            "is_honeypot": "0", "cannot_buy": "0", "cannot_sell_all": "0",
            "is_blacklisted": "0", "malicious_address": "0", "is_open_source": "1",
            "is_in_dex": "1", "buy_tax": "0.01", "sell_tax": "0.02",
        }
        self.assertEqual(evaluate_launch_security(data)["status"], "eligible_for_watchlist")

    def test_seen_state_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "seen.json"
            _save_seen(path, {"1:0xabc", "56:0xdef"})
            self.assertEqual(_load_seen(path), {"1:0xabc", "56:0xdef"})


if __name__ == "__main__":
    unittest.main()
