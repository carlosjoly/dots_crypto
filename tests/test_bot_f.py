import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from app.new_launches import _get_json, _market_eligibility, _select_token, _pool_url, _load_seen, _save_seen, _load_review_state, _save_review_state, _review_recheck_due, release_seen_token, _holder_count_review_reason, _classify_liquidity, scan_new_launches
from app.security import evaluate_launch_security, get_goplus_access_token
import hashlib
from app.alerts import format_message


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

    def test_pool_url_requires_a_valid_evm_pool_address(self):
        valid_address = "0x" + "a" * 40
        self.assertEqual(
            _pool_url("avalanche", {"pool_address": valid_address}),
            f"https://www.geckoterminal.com/avax/pools/{valid_address}",
        )
        self.assertIsNone(_pool_url("avalanche", {"pool_address": None}))
        self.assertIsNone(_pool_url("avalanche", {"pool_address": "0x" + "b" * 64}))

    def test_liquidity_classification_separates_all_provider_data_cases(self):
        minimum = 25000
        self.assertEqual(_classify_liquidity("24999.99", minimum), ("below_minimum", 24999.99))
        self.assertEqual(_classify_liquidity(None, minimum), ("missing", None))
        self.assertEqual(_classify_liquidity("   ", minimum), ("missing", None))
        self.assertEqual(_classify_liquidity("not-a-number", minimum), ("invalid", None))
        self.assertEqual(_classify_liquidity("NaN", minimum), ("invalid", None))
        self.assertEqual(_classify_liquidity("0", minimum), ("zero", 0.0))
        self.assertEqual(_classify_liquidity("25000", minimum), ("passed", 25000.0))

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

    def test_holder_count_gate_requires_minimum_distribution(self):
        self.assertIsNone(_holder_count_review_reason({"holder_count": "39"}, 10))
        low = _holder_count_review_reason({"holder_count": "1"}, 10)
        self.assertIn("abaixo do mínimo", low)
        missing = _holder_count_review_reason({}, 10)
        self.assertIn("não informado", missing)

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
            "lp_holder_count": "1",
            "lp_holders": [{"address": "0xlocker", "percent": "0.95", "is_locked": "1", "balance": "100", "locked_detail": [{"amount": "100", "end_time": str(time.time() + 86400)}]}],
        }
        self.assertEqual(evaluate_launch_security(data, require_lp=True)["status"], "eligible_for_watchlist")

    def test_security_requires_lp_holder_data_before_watchlist_approval(self):
        data = {
            "is_honeypot": "0", "cannot_buy": "0", "cannot_sell_all": "0",
            "is_blacklisted": "0", "malicious_address": "0", "is_open_source": "1",
            "is_in_dex": "1", "buy_tax": "0.01", "sell_tax": "0.02",
        }
        result = evaluate_launch_security(data, require_lp=True)
        self.assertEqual(result["status"], "review_required")
        self.assertTrue(any("lp_holders" in flag for flag in result["flags"]))

    def test_security_requires_complete_lp_holder_coverage(self):
        data = {
            "is_honeypot": "0", "cannot_buy": "0", "cannot_sell_all": "0",
            "is_blacklisted": "0", "malicious_address": "0", "is_open_source": "1",
            "is_in_dex": "1", "buy_tax": "0.01", "sell_tax": "0.02",
            "lp_holder_count": "18",
            "lp_holders": [{
                "address": "0xlocker", "percent": "0.10", "is_locked": "1",
                "balance": "100",
                "locked_detail": [{"amount": "100", "end_time": str(time.time() + 86400)}],
            }],
        }
        result = evaluate_launch_security(data, require_lp=True)
        self.assertEqual(result["status"], "review_required")
        self.assertTrue(any("cobertura incompleta" in flag or "18 holders" in flag for flag in result["flags"]))

    def test_security_does_not_approve_expired_lp_lock(self):
        data = {
            "is_honeypot": "0", "cannot_buy": "0", "cannot_sell_all": "0",
            "is_blacklisted": "0", "malicious_address": "0", "is_open_source": "1",
            "is_in_dex": "1", "buy_tax": "0.01", "sell_tax": "0.02",
            "lp_holder_count": "1",
            "lp_holders": [{
                "address": "0xlocker", "percent": "1.0", "is_locked": "1",
                "balance": "100",
                "locked_detail": [{"amount": "100", "end_time": str(time.time() - 60)}],
            }],
        }
        result = evaluate_launch_security(data, require_lp=True)
        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("bloqueio expirado" in flag for flag in result["flags"]))

    def test_security_rejects_unlocked_lp_holder_with_50pct_concentration(self):
        data = {
            "is_honeypot": "0", "cannot_buy": "0", "cannot_sell_all": "0",
            "is_blacklisted": "0", "malicious_address": "0", "is_open_source": "1",
            "is_in_dex": "1", "buy_tax": "0.01", "sell_tax": "0.02",
            "lp_holder_count": "1",
            "lp_holders": [{"address": "0xowner", "percent": "0.50", "is_locked": "0"}],
        }
        result = evaluate_launch_security(data, require_lp=True)
        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("LP não protegida agregada concentra" in flag for flag in result["flags"]))

    def test_security_rejects_non_finite_tax_and_lp_percent(self):
        base = {
            "is_honeypot": "0", "cannot_buy": "0", "cannot_sell_all": "0",
            "is_blacklisted": "0", "malicious_address": "0", "is_open_source": "1",
            "is_in_dex": "1", "buy_tax": "nan", "sell_tax": "0.02",
            "lp_holder_count": "1",
            "lp_holders": [{"address": "0xlocker", "percent": "nan", "is_locked": "1"}],
        }
        result = evaluate_launch_security(base, require_lp=True)
        self.assertEqual(result["status"], "review_required")
        self.assertTrue(any("taxa de compra" in flag for flag in result["flags"]))
        self.assertTrue(any("concentração percentual" in flag for flag in result["flags"]))

    def test_security_rejects_aggregate_unlocked_lp_concentration_at_50pct(self):
        data = {
            "is_honeypot": "0", "cannot_buy": "0", "cannot_sell_all": "0",
            "is_blacklisted": "0", "malicious_address": "0", "is_open_source": "1",
            "is_in_dex": "1", "buy_tax": "0.01", "sell_tax": "0.02",
            "lp_holder_count": "2",
            "lp_holders": [
                {"address": "0xowner1", "percent": "0.30", "is_locked": "0"},
                {"address": "0xowner2", "percent": "0.20", "is_locked": "0"},
            ],
        }
        result = evaluate_launch_security(data, require_lp=True)
        self.assertEqual(result["status"], "rejected")
        self.assertTrue(any("agregada concentra 50.0%" in flag for flag in result["flags"]))

    def test_single_lp_holder_is_not_rejected_when_explicitly_locked(self):
        data = {
            "is_honeypot": "0", "cannot_buy": "0", "cannot_sell_all": "0",
            "is_blacklisted": "0", "malicious_address": "0", "is_open_source": "1",
            "is_in_dex": "1", "buy_tax": "0.01", "sell_tax": "0.02",
            "lp_holder_count": "1",
            "lp_holders": [{"address": "0xlocker", "percent": "1.0", "is_locked": "1", "balance": "100", "locked_detail": [{"amount": "100", "end_time": str(time.time() + 86400)}]}],
        }
        self.assertEqual(evaluate_launch_security(data, require_lp=True)["status"], "eligible_for_watchlist")

    def test_bot_f_alert_layout_includes_contract_and_pool_without_fake_technical_levels(self):
        alert = {
            "agent": "Bot F — Novos lançamentos",
            "symbol": "NEW",
            "timeframe": "Lançamento DEX",
            "price": 0.25,
            "signal": "CANDIDATO A ANÁLISE — NÃO É RECOMENDAÇÃO",
            "reason": "Pool recente detectado; liquidez observada e GoPlus sem alertas críticos.",
            "confidence": "não calibrada",
            "risk_note": "Triagem preliminar; exige análise manual.",
            "candle_time_utc": "2026-10-31T18:00:00+00:00",
            "chain_id": "8453",
            "network": "base",
            "token_address": "0x" + "a" * 40,
            "pool_url": "https://www.geckoterminal.com/base/pools/0xpool",
            "liquidity_usd": 50000,
            "volume_24h_usd": 25000,
            "goplus_status": "eligible_for_watchlist",
        }
        message = format_message(alert)
        self.assertIn("CONTRATO", message.upper())
        self.assertIn("0x" + "a" * 40, message)
        self.assertIn("geckoterminal.com/base/pools/0xpool", message)
        self.assertIn("Liquidez observada: US$ 50.000,00", message)
        self.assertIn("Triagem GoPlus: eligible_for_watchlist", message)
        self.assertNotIn("Invalidação: US$ N/D", message)
        self.assertNotIn("Objetivo ilustrativo: US$ N/D", message)
        self.assertNotIn("\\\\n", message)

    def test_bot_f_alert_shows_pool_id_when_direct_pool_url_is_unavailable(self):
        pool_id = "avax_0x" + "a" * 64
        message = format_message({
            "agent": "Bot F — Novos lançamentos",
            "symbol": "NEW",
            "timeframe": "Lançamento DEX",
            "price": 0.25,
            "signal": "CANDIDATO A ANÁLISE",
            "pool_id": pool_id,
            "pool_url": None,
            "token_address": "0x" + "b" * 40,
            "chain_id": "43114",
            "network": "avalanche",
            "candle_time_utc": "2026-11-07T18:00:00+00:00",
        })
        self.assertIn(pool_id, message)
        self.assertIn("endereço não disponível", message)
        self.assertNotIn("/pools/None", message)

    def test_failed_delivery_releases_token_from_seen_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data"
            path.mkdir()
            state_file = path / "bot_f_seen.json"
            identity = "8453:" + "0x" + "a" * 40
            _save_seen(state_file, {identity})
            cfg = {
                "_root": directory,
                "launches": {"seen_state_file": "data/bot_f_seen.json"},
            }
            release_seen_token(cfg, {
                "chain_id": "8453", "token_address": "0x" + "a" * 40
            })
            self.assertEqual(_load_seen(state_file), set())

    @patch("app.new_launches.time.sleep")
    @patch("app.new_launches.goplus_token_security")
    @patch("app.new_launches.fetch_new_pools")
    def test_goplus_cycle_limit_stops_before_querying_next_network(self, fetch_pools, goplus, _sleep):
        with tempfile.TemporaryDirectory() as directory:
            now = datetime.now(timezone.utc)
            def pool(address):
                return {
                    "pool_id": "pool-" + address[-4:],
                    "pool_address": "0x" + "f" * 40,
                    "created_at": (now - timedelta(minutes=30)).isoformat(),
                    "liquidity_usd": "50000", "volume_24h_usd": "25000",
                    "txns_24h": {"buys": 40, "sells": 35},
                    "base": {"symbol": "NEW", "name": "New Token", "address": address},
                    "quote": {"symbol": "USDC", "address": "0x" + "1" * 40},
                    "base_price_usd": "0.25", "quote_price_usd": "1",
                    "dex": "Test DEX", "network": "eth",
                }
            fetch_pools.return_value = [pool("0x" + "2" * 40)]
            goplus.return_value = {
                "is_honeypot": "0", "cannot_buy": "0", "cannot_sell_all": "0",
                "is_blacklisted": "0", "malicious_address": "0", "is_open_source": "1",
                "is_in_dex": "1", "buy_tax": "0.01", "sell_tax": "0.01",
                "holder_count": "100", "lp_holder_count": "1",
                "lp_holders": [{"address": "0xlocker", "percent": "1.0", "is_locked": "1", "balance": "100", "locked_detail": [{"amount": "100", "end_time": str(time.time() + 86400)}]}],
            }
            cfg = {
                "_root": directory,
                "_env": {"goplus_enabled": True},
                "launches": {
                    "networks": ["eth", "base"], "quote_symbols": ["USDC"],
                    "min_pool_age_minutes": 5, "max_pool_age_hours": 48,
                    "min_liquidity_usd": 25000, "min_volume_24h_usd": 10000,
                    "min_txns_24h": 50, "min_holder_count": 10,
                    "max_security_checks_per_cycle": 1, "max_buy_sell_tax": 0.05,
                    "seen_state_file": "data/seen.json",
                    "review_state_file": "data/review.json",
                    "review_recheck_minutes": 30,
                },
            }
            alerts = scan_new_launches(cfg)
            self.assertEqual(len(alerts), 1)
            self.assertEqual(
                {alert["discovery_status"] for alert in alerts},
                {"ELEGÍVEL PARA WATCHLIST — NÃO É RECOMENDAÇÃO"},
            )
            # Discovery continues across all networks even after the GoPlus cap is reached.
            self.assertEqual(fetch_pools.call_count, 2)
            self.assertEqual(goplus.call_count, 1)


    @patch("app.new_launches.time.sleep")
    @patch("app.new_launches.goplus_token_security")
    @patch("app.new_launches.fetch_new_pools")
    def test_rejected_honeypot_and_unlocked_lp_never_generate_alert(self, fetch_pools, goplus, _sleep):
        with tempfile.TemporaryDirectory() as directory:
            now = datetime.now(timezone.utc)
            address = "0x" + "2" * 40
            pool = {
                "pool_id": "eth_0x" + "f" * 40, "pool_address": "0x" + "f" * 40,
                "created_at": (now - timedelta(minutes=30)).isoformat(),
                "liquidity_usd": "52141.89", "volume_24h_usd": "33290.40",
                "txns_24h": {"buys": 30, "sells": 22},
                "base": {"symbol": "POOF", "name": "Poof", "address": address},
                "quote": {"symbol": "USDC", "address": "0x" + "1" * 40},
                "base_price_usd": "0.00048", "quote_price_usd": "1",
                "dex": "Uniswap V2", "network": "eth",
            }
            fetch_pools.return_value = [pool]
            goplus.return_value = {
                "is_honeypot": "1", "cannot_buy": "0", "cannot_sell_all": "0",
                "is_blacklisted": "0", "malicious_address": "0", "is_open_source": "1",
                "is_in_dex": "1", "buy_tax": "0.01", "sell_tax": "0.01",
                "holder_count": "1", "lp_holder_count": "2",
                "lp_holders": [
                    {"address": "0xowner", "percent": "1.0", "is_locked": "0"},
                    {"address": "0xlocker", "percent": "0.0", "is_locked": "1"},
                ],
            }
            cfg = {
                "_root": directory, "_env": {"goplus_enabled": True},
                "launches": {
                    "networks": ["eth"], "quote_symbols": ["USDC"],
                    "min_pool_age_minutes": 5, "max_pool_age_hours": 48,
                    "min_liquidity_usd": 25000, "min_volume_24h_usd": 10000,
                    "min_txns_24h": 50, "min_holder_count": 10,
                    "max_security_checks_per_cycle": 15, "max_buy_sell_tax": 0.05,
                    "seen_state_file": "data/seen.json",
                    "review_state_file": "data/review.json",
                    "review_recheck_minutes": 30,
                },
            }
            alerts = scan_new_launches(cfg)
            self.assertEqual(alerts, [])
            self.assertEqual(goplus.call_count, 1)

    @patch("app.security.time.time", return_value=1700000000)
    @patch("app.security.requests.post")
    def test_goplus_auth_uses_official_signed_access_token_flow(self, mock_post, _mock_time):
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status.return_value = None
        mock_post.return_value.json.return_value = {
            "code": 1, "result": {"access_token": "temporary-token"}
        }
        token = get_goplus_access_token("app-key", "app-secret")
        expected = hashlib.sha1(b"app-key1700000000app-secret").hexdigest()
        self.assertEqual(token, "temporary-token")
        self.assertEqual(mock_post.call_args.kwargs["json"], {
            "app_key": "app-key", "sign": expected, "time": 1700000000
        })

    @patch("app.new_launches.time.sleep")
    @patch("app.new_launches.requests.get")
    def test_gecko_429_honors_retry_after_seconds(self, mock_get, mock_sleep):
        throttled = Mock(status_code=429, headers={"Retry-After": "7"})
        error = requests.exceptions.HTTPError("rate limited", response=throttled)
        success = Mock()
        success.raise_for_status.return_value = None
        success.json.return_value = {"data": []}
        mock_get.side_effect = [error, success]

        self.assertEqual(_get_json("https://example.test"), {"data": []})
        mock_sleep.assert_called_once_with(12)
        self.assertEqual(mock_get.call_count, 2)

    @patch("app.new_launches.time.sleep")
    @patch("app.new_launches.requests.get")
    def test_gecko_429_zero_retry_after_uses_minimum_ten_second_wait(
        self, mock_get, mock_sleep
    ):
        throttled = Mock(status_code=429, headers={"Retry-After": "0"})
        error = requests.exceptions.HTTPError("rate limited", response=throttled)
        success = Mock()
        success.raise_for_status.return_value = None
        success.json.return_value = {"data": []}
        mock_get.side_effect = [error, success]

        self.assertEqual(_get_json("https://example.test"), {"data": []})
        mock_sleep.assert_called_once_with(12)
        self.assertEqual(mock_get.call_count, 2)

    @patch("app.new_launches.time.sleep")
    @patch("app.new_launches.requests.get")
    def test_gecko_429_near_zero_retry_after_uses_minimum_ten_second_wait(
        self, mock_get, mock_sleep
    ):
        throttled = Mock(status_code=429, headers={"Retry-After": "0.5"})
        error = requests.exceptions.HTTPError("rate limited", response=throttled)
        success = Mock()
        success.raise_for_status.return_value = None
        success.json.return_value = {"data": []}
        mock_get.side_effect = [error, success]

        self.assertEqual(_get_json("https://example.test"), {"data": []})
        mock_sleep.assert_called_once_with(12)
        self.assertEqual(mock_get.call_count, 2)

    @patch("app.new_launches.random.uniform", return_value=0.5)
    @patch("app.new_launches.time.sleep")
    @patch("app.new_launches.requests.get")
    def test_gecko_429_uses_exponential_backoff_and_jitter_without_header(
        self, mock_get, mock_sleep, _mock_jitter
    ):
        throttled = Mock(status_code=429, headers={})
        error = requests.exceptions.HTTPError("rate limited", response=throttled)
        success = Mock()
        success.raise_for_status.return_value = None
        success.json.return_value = {"data": []}
        mock_get.side_effect = [error, success]

        self.assertEqual(_get_json("https://example.test"), {"data": []})
        mock_sleep.assert_called_once_with(12.5)
        self.assertEqual(mock_get.call_count, 2)

    @patch("app.new_launches.random.uniform", return_value=0.25)
    @patch("app.new_launches.time.sleep")
    @patch("app.new_launches.requests.get")
    def test_gecko_429_stops_after_three_retries(
        self, mock_get, mock_sleep, _mock_jitter
    ):
        throttled = Mock(status_code=429, headers={})
        error = requests.exceptions.HTTPError("rate limited", response=throttled)
        mock_get.side_effect = [error, error, error, error]

        with self.assertRaises(requests.exceptions.HTTPError):
            _get_json("https://example.test")

        self.assertEqual(mock_get.call_count, 4)
        self.assertEqual(
            [call.args[0] for call in mock_sleep.call_args_list],
            [12.25, 24.25, 48.25],
        )

    def test_seen_state_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "seen.json"
            _save_seen(path, {"1:0xabc", "56:0xdef"})
            self.assertEqual(_load_seen(path), {"1:0xabc", "56:0xdef"})

    def test_review_state_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "review.json"
            state = {"56:0xabc": {"checked_at": self.now.isoformat(), "flags": ["holder_count baixo"]}}
            _save_review_state(path, state)
            self.assertEqual(_load_review_state(path), state)

    def test_review_candidate_waits_thirty_minutes_before_goplus_recheck(self):
        identity = "56:0xabc"
        state = {identity: {"checked_at": self.now.isoformat()}}
        self.assertFalse(_review_recheck_due(identity, state, self.now + timedelta(minutes=5), 30))
        self.assertFalse(_review_recheck_due(identity, state, self.now + timedelta(minutes=29), 30))
        self.assertTrue(_review_recheck_due(identity, state, self.now + timedelta(minutes=30), 30))

    def test_invalid_review_timestamp_is_rechecked_instead_of_permanently_skipped(self):
        self.assertTrue(_review_recheck_due("56:0xabc", {"56:0xabc": {"checked_at": "invalid"}}, self.now, 30))
        self.assertTrue(_review_recheck_due("56:0xabc", {}, self.now, 30))


    @patch("app.new_launches.time.sleep")
    @patch("app.new_launches.goplus_token_security")
    @patch("app.new_launches.fetch_new_pools")
    def test_market_qualified_but_incomplete_security_emits_pending_discovery_alert(
        self, fetch_pools, goplus, _sleep
    ):
        with tempfile.TemporaryDirectory() as directory:
            now = datetime.now(timezone.utc)
            address = "0x" + "3" * 40
            pool = {
                "pool_id": "eth_0x" + "f" * 40, "pool_address": "0x" + "f" * 40,
                "created_at": (now - timedelta(minutes=30)).isoformat(),
                "liquidity_usd": "50000", "volume_24h_usd": "25000",
                "txns_24h": {"buys": 40, "sells": 35},
                "base": {"symbol": "NEW", "name": "New Token", "address": address},
                "quote": {"symbol": "USDC", "address": "0x" + "1" * 40},
                "base_price_usd": "0.25", "quote_price_usd": "1",
                "dex": "Test DEX", "network": "eth",
            }
            fetch_pools.return_value = [pool]
            goplus.return_value = {
                "is_honeypot": "0", "cannot_buy": "0", "cannot_sell_all": "0",
                "is_blacklisted": "0", "is_open_source": "1", "is_in_dex": "1",
                "buy_tax": "0.01", "sell_tax": "0.01", "holder_count": "100",
                # Deliberately missing LP evidence: must remain pending, never approved.
            }
            cfg = {
                "_root": directory, "_env": {"goplus_enabled": True},
                "launches": {
                    "networks": ["eth"], "quote_symbols": ["USDC"],
                    "min_pool_age_minutes": 5, "max_pool_age_hours": 48,
                    "min_liquidity_usd": 25000, "min_volume_24h_usd": 10000,
                    "min_txns_24h": 50, "min_holder_count": 10,
                    "max_security_checks_per_cycle": 15, "max_buy_sell_tax": 0.05,
                    "seen_state_file": "data/seen.json",
                    "review_state_file": "data/review.json", "review_recheck_minutes": 30,
                },
            }
            alerts = scan_new_launches(cfg)
            self.assertEqual(alerts, [])
            self.assertEqual(_load_seen(Path(directory) / "data/seen.json"), set())
            identity = "1:" + address
            state = _load_review_state(Path(directory) / "data/review.json")
            self.assertFalse(state[identity]["discovery_alert_sent"])

            # Same token is not spammed every 15-minute scan while in review cooldown.
            self.assertEqual(scan_new_launches(cfg), [])
            self.assertEqual(goplus.call_count, 1)

    @patch("app.new_launches.time.sleep")
    @patch("app.new_launches.fetch_new_pools")
    def test_scan_persists_each_pool_and_grouped_liquidity_diagnostics(
        self, fetch_pools, _sleep
    ):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "bot_f_pool_diagnostics.json"
            now = datetime.now(timezone.utc)
            base = {
                "pool_address": "0x" + "f" * 40,
                "created_at": (now - timedelta(minutes=30)).isoformat(),
                "volume_24h_usd": "25000",
                "txns_24h": {"buys": 40, "sells": 35},
                "base": {"symbol": "NEW", "address": "0x" + "2" * 40},
                "quote": {"symbol": "USDC", "address": "0x" + "1" * 40},
                "dex": "Test DEX", "network": "eth",
            }
            fetch_pools.return_value = [
                dict(base, pool_id="eth_missing", liquidity_usd=None),
                dict(base, pool_id="eth_zero", liquidity_usd="0"),
                dict(base, pool_id="eth_invalid", liquidity_usd="unknown"),
                dict(base, pool_id="eth_low", liquidity_usd="100"),
                dict(base, pool_id="eth_pass", liquidity_usd="50000"),
            ]
            cfg = {
                "_root": directory, "_env": {"goplus_enabled": True},
                "launches": {
                    "networks": ["eth"], "quote_symbols": ["USDC"],
                    "min_pool_age_minutes": 5, "max_pool_age_hours": 48,
                    "min_liquidity_usd": 25000, "min_volume_24h_usd": 10000,
                    "min_txns_24h": 50, "min_holder_count": 10,
                    "max_security_checks_per_cycle": 0, "max_buy_sell_tax": 0.05,
                    "seen_state_file": "data/seen.json", "review_state_file": "data/review.json",
                },
            }
            with patch("app.new_launches.POOL_DIAGNOSTICS_PATH", target):
                scan_new_launches(cfg)
            payload = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["pools"]), 5)
            self.assertEqual(payload["liquidity_classification_counts"], {
                "below_minimum": 1, "missing": 1, "invalid": 1, "zero": 1, "passed": 1,
            })
            group = payload["network_dex_summary"]["eth|Test DEX"]
            self.assertEqual(group["pools"], 5)
            self.assertEqual(group["missing"], 1)
            self.assertEqual(group["zero"], 1)
            self.assertEqual(group["invalid"], 1)
            self.assertEqual(group["below_minimum"], 1)
            self.assertEqual(group["passed"], 1)
            self.assertEqual(payload["pools"][0]["liquidity_raw_usd"], None)
            self.assertEqual(payload["pools"][0]["market_filter_reason"], "liquidez ausente no fornecedor")

    @patch("app.new_launches.time.sleep")
    @patch("app.new_launches.fetch_new_pools")
    def test_diagnostics_count_each_market_filter_and_security_limit(self, fetch_pools, _sleep):
        with tempfile.TemporaryDirectory() as directory:
            now = datetime.now(timezone.utc)
            def make_pool(index, age=30, liquidity=50000, volume=25000, txns=75):
                return {
                    "pool_id": f"eth_pool_{index}", "pool_address": "0x" + "f" * 40,
                    "created_at": (now - timedelta(minutes=age)).isoformat(),
                    "liquidity_usd": str(liquidity), "volume_24h_usd": str(volume),
                    "txns_24h": {"buys": txns // 2, "sells": txns - txns // 2},
                    "base": {"symbol": "NEW", "name": "New Token", "address": "0x" + str(index) * 40},
                    "quote": {"symbol": "USDC", "address": "0x" + "1" * 40},
                    "base_price_usd": "0.25", "quote_price_usd": "1",
                    "dex": "Test DEX", "network": "eth",
                }
            fetch_pools.return_value = [
                make_pool(2, age=1), make_pool(3, liquidity=1),
                make_pool(4, volume=1), make_pool(5, txns=1), make_pool(6),
            ]
            cfg = {
                "_root": directory, "_env": {"goplus_enabled": True},
                "launches": {
                    "networks": ["eth"], "quote_symbols": ["USDC"],
                    "min_pool_age_minutes": 5, "max_pool_age_hours": 48,
                    "min_liquidity_usd": 25000, "min_volume_24h_usd": 10000,
                    "min_txns_24h": 50, "min_holder_count": 10,
                    "max_security_checks_per_cycle": 0, "max_buy_sell_tax": 0.05,
                    "seen_state_file": "data/seen.json", "review_state_file": "data/review.json",
                },
            }
            with self.assertLogs("dot_cripto.bot_f", level="INFO") as captured:
                alerts = scan_new_launches(cfg)
            self.assertEqual(alerts, [])
            diagnostic = next(line for line in captured.output if "Bot F diagnóstico ciclo:" in line)
            self.assertIn('"age": 1', diagnostic)
            self.assertIn('"liquidity": 1', diagnostic)
            self.assertIn('"volume": 1', diagnostic)
            self.assertIn('"transactions": 1', diagnostic)
            self.assertIn('"security_limit_skipped": 1', diagnostic)
            self.assertIn('"market_eligible": 1', diagnostic)


if __name__ == "__main__":
    unittest.main()
