import json
import tempfile
from datetime import datetime, timezone, timedelta
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from app.new_launches import fetch_new_pools, gecko_network_id, _get_json
from app import validate_bot_f_avalanche as avalanche_validation


def response(status, payload=None, headers=None):
    item = requests.Response()
    item.status_code = status
    item.headers.update(headers or {})
    item._content = json.dumps(payload or {}).encode("utf-8")
    item.url = "https://api.geckoterminal.com/api/v2/test"
    return item


class BotFGeckoTerminalTests(unittest.TestCase):
    def test_avalanche_uses_geckoterminal_network_id_avax(self):
        self.assertEqual(gecko_network_id("avalanche"), "avax")
        with patch("app.new_launches._get_json", return_value={"data": []}) as get_json:
            self.assertEqual(fetch_new_pools("avalanche"), [])
        self.assertEqual(
            get_json.call_args.args[0],
            "https://api.geckoterminal.com/api/v2/networks/avax/new_pools",
        )

    @patch("app.new_launches.time.sleep")
    @patch("app.new_launches.requests.get")
    def test_429_respects_retry_after_and_retries(self, get, sleep):
        get.side_effect = [
            response(429, headers={"Retry-After": "17"}),
            response(200, {"data": []}),
        ]
        result = _get_json("https://api.geckoterminal.com/api/v2/networks/avax/new_pools")
        self.assertEqual(result, {"data": []})
        self.assertEqual(get.call_count, 2)
        sleep.assert_called_once_with(17.0)
        headers = get.call_args.kwargs["headers"]
        self.assertEqual(headers["Accept"], "application/json;version=20230203")

    @patch("app.new_launches.time.sleep")
    @patch("app.new_launches.requests.get")
    def test_429_telemetry_records_one_event_per_http_response(self, get, sleep):
        with tempfile.TemporaryDirectory() as directory:
            metrics = Path(directory) / "metrics.jsonl"
            throttled = response(429, headers={"Retry-After": "12"})
            success = response(200, {"data": []})
            get.side_effect = [throttled, success]
            with patch("app.new_launches.API_METRICS_PATH", metrics):
                self.assertEqual(
                    _get_json("https://api.geckoterminal.com/api/v2/networks/avax/new_pools"),
                    {"data": []},
                )
            records = [json.loads(line) for line in metrics.read_text().splitlines()]
            self.assertEqual([item["status"] for item in records], [429, 200])
            self.assertEqual(records[0]["network_slug"], "avax")
            self.assertEqual(records[0]["wait_seconds"], 12.0)
            self.assertEqual(records[0]["page"], 1)
            self.assertIn("/networks/avax/new_pools", records[0]["endpoint"])
            self.assertIsNotNone(records[0]["latency_ms"])

    @patch("app.new_launches.time.sleep")
    @patch("app.new_launches.requests.get")
    def test_404_is_not_retried(self, get, sleep):
        get.return_value = response(404)
        with self.assertRaises(requests.exceptions.HTTPError):
            _get_json("https://api.geckoterminal.com/api/v2/networks/invalid/new_pools")
        get.assert_called_once()
        sleep.assert_not_called()

    @patch("app.new_launches.time.sleep")
    @patch("app.new_launches.requests.get")
    def test_transient_503_uses_backoff_and_recovers(self, get, sleep):
        get.side_effect = [response(503), response(200, {"data": [1]})]
        result = _get_json("https://api.geckoterminal.com/api/v2/networks/eth/new_pools")
        self.assertEqual(result, {"data": [1]})
        self.assertEqual(get.call_count, 2)
        sleep.assert_called_once()


    @patch("app.new_launches.time.sleep")
    @patch("app.new_launches.requests.get")
    def test_connection_timeout_is_recorded_and_retried(self, get, sleep):
        with tempfile.TemporaryDirectory() as directory:
            metrics = Path(directory) / "metrics.jsonl"
            get.side_effect = [
                requests.exceptions.Timeout("timeout"),
                response(200, {"data": []}),
            ]
            with patch("app.new_launches.API_METRICS_PATH", metrics), patch(
                "app.new_launches.random.uniform", return_value=0
            ):
                result = _get_json(
                    "https://api.geckoterminal.com/api/v2/networks/avax/new_pools",
                    params={"page": 2},
                )
            self.assertEqual(result, {"data": []})
            records = [json.loads(line) for line in metrics.read_text().splitlines()]
            self.assertEqual([item["status"] for item in records], [None, 200])
            self.assertEqual(records[0]["page"], 2)
            self.assertEqual(records[0]["error_type"], "Timeout")
            sleep.assert_called_once()

    def test_avalanche_validator_checks_pagination_duplicates_and_quality(self):
        now = datetime.now(timezone.utc)
        pool = {
            "pool_id": "avax_0xpool1",
            "pool_address": "0x" + "1" * 40,
            "created_at": (now - timedelta(minutes=8)).isoformat(),
            "liquidity_usd": "25000",
            "volume_24h_usd": "12000",
            "txns_24h": {"buys": 40, "sells": 35},
            "base": {"address": "0x" + "2" * 40},
            "quote": {"address": "0x" + "3" * 40},
        }
        second = dict(pool, pool_id="avax_0xpool2", pool_address="0x" + "4" * 40)
        with patch.object(
            avalanche_validation, "fetch_new_pools",
            side_effect=[[pool], [pool, second], []],
        ) as fetch, patch.object(avalanche_validation.time, "sleep") as sleep:
            result = avalanche_validation.validate()

        self.assertEqual(fetch.call_args_list[0].kwargs, {"page": 1})
        self.assertEqual(fetch.call_args_list[1].kwargs, {"page": 2})
        self.assertEqual(fetch.call_args_list[2].kwargs, {"page": 3})
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["pagination_status"], "empty_page_reached")
        self.assertTrue(result["pagination_complete"])
        self.assertEqual(result["pools_received"], 3)
        self.assertEqual(result["unique_pool_count"], 2)
        self.assertEqual(result["duplicate_pool_count"], 1)
        self.assertEqual(result["complete_record_count"], 2)
        self.assertEqual(result["field_quality"]["pool_address"]["coverage_pct"], 100.0)
        self.assertFalse(result["spread_data_available"])

    def test_avalanche_validator_reports_missing_quality_fields(self):
        incomplete = {"pool_id": "avax_incomplete", "pool_address": "not-an-address"}
        with patch.object(avalanche_validation, "fetch_new_pools", return_value=[incomplete]):
            result = avalanche_validation.validate()
        self.assertEqual(result["quality_status"], "measured")
        self.assertEqual(result["complete_record_count"], 0)
        self.assertEqual(result["field_quality"]["pool_address"]["invalid_or_missing"], 1)
        self.assertEqual(result["field_quality"]["liquidity_usd"]["coverage_pct"], 0.0)


if __name__ == "__main__":
    unittest.main()
