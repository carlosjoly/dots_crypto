import unittest
from unittest.mock import patch
from app.run_bot_f import main

class BotFStandaloneRunnerTests(unittest.TestCase):
    def setUp(self):
        self.cfg={"_root":"/tmp/dot-cripto-test","_env":{"telegram_bot_token":"test-token","telegram_chat_id":"123"},"launches":{"enabled":True}}
        self.alert={"symbol":"TEST","token_address":"0x"+"1"*40,"network":"bsc","chain_id":"56","pool_address":"0x"+"a"*40,"discovery_status":"ELEGÍVEL PARA WATCHLIST — NÃO É RECOMENDAÇÃO","goplus_status":"eligible_for_watchlist"}
        self.proof={"status":"verified","pool_address":self.alert["pool_address"]}

    @patch("app.run_bot_f.is_valid_bsc_pancakeswap_v2_lp_proof",return_value=False)
    @patch("app.run_bot_f.build_bsc_pancakeswap_v2_lp_proof",return_value={"status":"blocked","reason":"no active lock"})
    @patch("app.run_bot_f.fetch_current_pool_liquidity",return_value={"classification":"passed","liquidity_usd":30000})
    @patch("app.run_bot_f.emit_alert")
    @patch("app.run_bot_f.scan_new_launches")
    @patch("app.run_bot_f.setup_logging")
    @patch("app.run_bot_f.load_config")
    def test_blocks_candidate_without_live_lp_proof(self,load_config,setup_logging,scan,emit,fresh,build,valid):
        load_config.return_value=self.cfg; scan.return_value=[dict(self.alert)]
        with patch.dict("os.environ",{"BOT_F_RPC_URL_BSC":"https://rpc.example"}): self.assertEqual(main(),0)
        emit.assert_not_called(); fresh.assert_called_once(); build.assert_called_once(); valid.assert_called_once()

    @patch("app.run_bot_f.build_bsc_pancakeswap_v2_lp_proof")
    @patch("app.run_bot_f.fetch_current_pool_liquidity",return_value={"classification":"passed","liquidity_usd":30000})
    @patch("app.run_bot_f.emit_alert")
    @patch("app.run_bot_f.scan_new_launches")
    @patch("app.run_bot_f.setup_logging")
    @patch("app.run_bot_f.load_config")
    def test_valid_live_proof_is_required_before_telegram(self,load_config,setup_logging,scan,emit,fresh,build):
        load_config.return_value=self.cfg; scan.return_value=[dict(self.alert)]; build.return_value=self.proof
        with patch.dict("os.environ",{"BOT_F_RPC_URL_BSC":"https://rpc.example"}), patch("app.run_bot_f.is_valid_bsc_pancakeswap_v2_lp_proof",return_value=True):
            self.assertEqual(main(),0)
        emit.assert_called_once(); self.assertEqual(emit.call_args.args[1]["independent_lp_proof"],self.proof)

    @patch("app.run_bot_f.release_seen_token")
    @patch("app.run_bot_f.is_valid_bsc_pancakeswap_v2_lp_proof",return_value=True)
    @patch("app.run_bot_f.build_bsc_pancakeswap_v2_lp_proof",return_value={"status":"verified"})
    @patch("app.run_bot_f.fetch_current_pool_liquidity",return_value={"classification":"passed","liquidity_usd":30000})
    @patch("app.run_bot_f.emit_alert",side_effect=RuntimeError("Telegram unavailable"))
    @patch("app.run_bot_f.scan_new_launches")
    @patch("app.run_bot_f.setup_logging")
    @patch("app.run_bot_f.load_config")
    def test_failed_delivery_releases_deduplication_for_retry(self,load_config,setup_logging,scan,emit,fresh,build,valid,release):
        load_config.return_value=self.cfg; scan.return_value=[dict(self.alert)]
        with patch.dict("os.environ",{"BOT_F_RPC_URL_BSC":"https://rpc.example"}): self.assertEqual(main(),1)
        release.assert_called_once()

    @patch("app.run_bot_f.build_bsc_pancakeswap_v2_lp_proof")
    @patch("app.run_bot_f.fetch_current_pool_liquidity")
    @patch("app.run_bot_f.emit_alert")
    @patch("app.run_bot_f.scan_new_launches")
    @patch("app.run_bot_f.setup_logging")
    @patch("app.run_bot_f.load_config")
    def test_non_bsc_network_is_blocked_without_a_lock_adapter(self,load_config,setup_logging,scan,emit,fresh,build):
        load_config.return_value=self.cfg; scan.return_value=[dict(self.alert,network="base",chain_id="8453")]
        self.assertEqual(main(),0); fresh.assert_not_called(); build.assert_not_called(); emit.assert_not_called()

    @patch("app.run_bot_f.scan_new_launches",side_effect=RuntimeError("provider unavailable"))
    @patch("app.run_bot_f.setup_logging")
    @patch("app.run_bot_f.load_config")
    def test_critical_scan_error_returns_failure(self,load_config,setup_logging,scan):
        load_config.return_value=self.cfg; self.assertEqual(main(),1)

    @patch("app.run_bot_f.scan_new_launches")
    @patch("app.run_bot_f.setup_logging")
    @patch("app.run_bot_f.load_config")
    def test_disabled_module_does_not_start_scan(self,load_config,setup_logging,scan):
        cfg=dict(self.cfg,launches={"enabled":False}); load_config.return_value=cfg
        self.assertEqual(main(),2); scan.assert_not_called()

if __name__=="__main__": unittest.main()
