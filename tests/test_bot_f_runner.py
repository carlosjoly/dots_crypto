import unittest
from unittest.mock import patch

from app.run_bot_f import main


class BotFStandaloneRunnerTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "_root": "/tmp/dot-cripto-test",
            "_env": {"telegram_bot_token": "test-token", "telegram_chat_id": "123"},
            "launches": {
                "enabled": True,
                "seen_state_file": "data/bot_f_seen.json",
                "review_state_file": "data/bot_f_review_state.json",
                "review_recheck_minutes": 30,
            },
        }
        self.alert = {
            "symbol": "TEST",
            "token_address": "0x" + "1" * 40,
            "network": "bsc",
            "chain_id": "56",
            "pool_address": "0x" + "a" * 40,
            "discovery_status": "ELEGÍVEL PARA WATCHLIST — NÃO É RECOMENDAÇÃO",
            "goplus_status": "eligible_for_watchlist",
        }
        self.proof = {"status": "verified", "pool_address": self.alert["pool_address"]}

    def test_pending_review_candidate_is_blocked_before_liquidity_rpc_or_telegram(self):
        pending = dict(
            self.alert,
            discovery_status="PENDENTE DE REVISÃO — NÃO APROVADO",
            goplus_status="review_required",
            independent_lp_proof={"status": "verified", "lock_transaction": "0x" + "d" * 64},
        )
        with (
            patch("app.run_bot_f.load_config", return_value=self.cfg),
            patch("app.run_bot_f.setup_logging"),
            patch("app.run_bot_f.scan_new_launches", return_value=[pending]),
            patch("app.run_bot_f.fetch_current_pool_liquidity") as fresh,
            patch("app.run_bot_f.build_bsc_pancakeswap_v2_lp_proof") as build,
            patch("app.run_bot_f.emit_alert") as emit,
        ):
            with patch.dict("os.environ", {"BOT_F_RPC_URL_BSC": "https://rpc.example"}):
                self.assertEqual(main(), 0)
            fresh.assert_not_called()
            build.assert_not_called()
            emit.assert_not_called()

    def test_blocks_candidate_without_live_lp_proof_even_if_discovery_supplies_metadata(self):
        candidate = dict(
            self.alert,
            independent_lp_proof={
                "status": "verified",
                "chain_id": "56",
                "pool_address": self.alert["pool_address"],
                "lock_transaction": "0x" + "d" * 64,
            },
        )
        with (
            patch("app.run_bot_f.load_config", return_value=self.cfg),
            patch("app.run_bot_f.setup_logging"),
            patch("app.run_bot_f.scan_new_launches", return_value=[candidate]),
            patch("app.run_bot_f.fetch_current_pool_liquidity", return_value={
                "classification": "passed", "liquidity_usd": 30000,
            }) as fresh,
            patch("app.run_bot_f.build_bsc_pancakeswap_v2_lp_proof", return_value={
                "status": "blocked", "reason": "no active lock",
            }) as build,
            patch("app.run_bot_f.is_valid_bsc_pancakeswap_v2_lp_proof", return_value=False) as valid,
            patch("app.run_bot_f.defer_lp_proof_review") as defer,
            patch("app.run_bot_f.emit_alert") as emit,
        ):
            with patch.dict("os.environ", {"BOT_F_RPC_URL_BSC": "https://rpc.example"}):
                self.assertEqual(main(), 0)
            fresh.assert_called_once()
            build.assert_called_once()
            valid.assert_called_once()
            defer.assert_called_once()
            emit.assert_not_called()

    def test_missing_invalid_zero_and_below_minimum_fresh_liquidity_block_proof_and_telegram(self):
        blocked_classes = ("missing", "invalid", "zero", "below_minimum")
        for classification in blocked_classes:
            with self.subTest(classification=classification):
                with (
                    patch("app.run_bot_f.load_config", return_value=self.cfg),
                    patch("app.run_bot_f.setup_logging"),
                    patch("app.run_bot_f.scan_new_launches", return_value=[dict(self.alert)]),
                    patch("app.run_bot_f.fetch_current_pool_liquidity", return_value={
                        "classification": classification, "liquidity_usd": None,
                    }) as fresh,
                    patch("app.run_bot_f.build_bsc_pancakeswap_v2_lp_proof") as build,
                    patch("app.run_bot_f.defer_lp_proof_review") as defer,
                    patch("app.run_bot_f.emit_alert") as emit,
                ):
                    with patch.dict("os.environ", {"BOT_F_RPC_URL_BSC": "https://rpc.example"}):
                        self.assertEqual(main(), 0)
                    fresh.assert_called_once_with("bsc", self.alert["pool_address"])
                    build.assert_not_called()
                    defer.assert_called_once()
                    emit.assert_not_called()

    def test_valid_live_proof_is_required_before_telegram(self):
        with (
            patch("app.run_bot_f.load_config", return_value=self.cfg),
            patch("app.run_bot_f.setup_logging"),
            patch("app.run_bot_f.scan_new_launches", return_value=[dict(self.alert)]),
            patch("app.run_bot_f.fetch_current_pool_liquidity", return_value={
                "classification": "passed", "liquidity_usd": 30000,
            }) as fresh,
            patch("app.run_bot_f.build_bsc_pancakeswap_v2_lp_proof", return_value=self.proof) as build,
            patch("app.run_bot_f.is_valid_bsc_pancakeswap_v2_lp_proof", return_value=True) as valid,
            patch("app.run_bot_f.emit_alert") as emit,
        ):
            with patch.dict("os.environ", {"BOT_F_RPC_URL_BSC": "https://rpc.example"}):
                self.assertEqual(main(), 0)
            fresh.assert_called_once()
            build.assert_called_once()
            valid.assert_called_once()
            emit.assert_called_once()
            self.assertEqual(emit.call_args.args[1]["independent_lp_proof"], self.proof)

    def test_non_bsc_network_is_blocked_without_a_lock_adapter(self):
        with (
            patch("app.run_bot_f.load_config", return_value=self.cfg),
            patch("app.run_bot_f.setup_logging"),
            patch("app.run_bot_f.scan_new_launches", return_value=[
                dict(self.alert, network="base", chain_id="8453"),
            ]),
            patch("app.run_bot_f.fetch_current_pool_liquidity") as fresh,
            patch("app.run_bot_f.build_bsc_pancakeswap_v2_lp_proof") as build,
            patch("app.run_bot_f.emit_alert") as emit,
        ):
            self.assertEqual(main(), 0)
            fresh.assert_not_called()
            build.assert_not_called()
            emit.assert_not_called()

    def test_missing_rpc_blocks_before_liquidity_or_telegram(self):
        with (
            patch("app.run_bot_f.load_config", return_value=self.cfg),
            patch("app.run_bot_f.setup_logging"),
            patch("app.run_bot_f.scan_new_launches", return_value=[dict(self.alert)]),
            patch("app.run_bot_f.fetch_current_pool_liquidity") as fresh,
            patch("app.run_bot_f.build_bsc_pancakeswap_v2_lp_proof") as build,
            patch("app.run_bot_f.defer_lp_proof_review") as defer,
            patch("app.run_bot_f.emit_alert") as emit,
            patch.dict("os.environ", {}, clear=True),
        ):
            self.assertEqual(main(), 0)
            fresh.assert_not_called()
            build.assert_not_called()
            defer.assert_called_once()
            emit.assert_not_called()

    def test_failed_delivery_releases_deduplication_for_retry(self):
        with (
            patch("app.run_bot_f.load_config", return_value=self.cfg),
            patch("app.run_bot_f.setup_logging"),
            patch("app.run_bot_f.scan_new_launches", return_value=[dict(self.alert)]),
            patch("app.run_bot_f.fetch_current_pool_liquidity", return_value={
                "classification": "passed", "liquidity_usd": 30000,
            }),
            patch("app.run_bot_f.build_bsc_pancakeswap_v2_lp_proof", return_value=self.proof),
            patch("app.run_bot_f.is_valid_bsc_pancakeswap_v2_lp_proof", return_value=True),
            patch("app.run_bot_f.emit_alert", side_effect=RuntimeError("Telegram unavailable")),
            patch("app.run_bot_f.release_seen_token") as release,
        ):
            with patch.dict("os.environ", {"BOT_F_RPC_URL_BSC": "https://rpc.example"}):
                self.assertEqual(main(), 1)
            release.assert_called_once()

    def test_critical_scan_error_returns_failure(self):
        with (
            patch("app.run_bot_f.load_config", return_value=self.cfg),
            patch("app.run_bot_f.setup_logging"),
            patch("app.run_bot_f.scan_new_launches", side_effect=RuntimeError("provider unavailable")),
        ):
            self.assertEqual(main(), 1)

    def test_disabled_module_does_not_start_scan(self):
        disabled = dict(self.cfg, launches={"enabled": False})
        with (
            patch("app.run_bot_f.load_config", return_value=disabled),
            patch("app.run_bot_f.setup_logging"),
            patch("app.run_bot_f.scan_new_launches") as scan,
        ):
            self.assertEqual(main(), 2)
            scan.assert_not_called()


if __name__ == "__main__":
    unittest.main()
