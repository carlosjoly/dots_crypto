import json
import unittest
from unittest.mock import patch

import requests

from app.new_launches import fetch_new_pools, gecko_network_id, _get_json


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


if __name__ == "__main__":
    unittest.main()
