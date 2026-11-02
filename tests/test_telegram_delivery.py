import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.alerts import emit_alert


class TelegramDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cfg = {
            "_root": self.temp.name,
            "_env": {
                "telegram_bot_token": "test-token",
                "telegram_chat_id": "-1001234567890",
            },
            "alerts": {"telegram_enabled": True, "log_file": "data/alerts.jsonl"},
        }
        self.alert = {
            "agent": "BOT G — TESTE",
            "symbol": "BTCUSDT",
            "timeframe": "validação",
            "signal": "TESTE — NÃO É SINAL",
            "reason": "Teste controlado.",
        }

    def tearDown(self):
        self.temp.cleanup()

    @patch("app.alerts.requests.post")
    def test_confirms_telegram_ok_message_id_and_expected_chat(self, post):
        response = Mock()
        response.json.return_value = {
            "ok": True,
            "result": {"message_id": 987, "chat": {"id": -1001234567890}},
        }
        post.return_value = response

        result = emit_alert(self.cfg, dict(self.alert))

        response.raise_for_status.assert_called_once()
        self.assertEqual(result, {
            "ok": True, "message_id": 987, "chat_id_matches": True
        })

    @patch("app.alerts.requests.post")
    def test_rejects_telegram_api_ok_false_even_when_http_status_is_success(self, post):
        response = Mock()
        response.json.return_value = {
            "ok": False, "error_code": 403, "description": "Forbidden"
        }
        post.return_value = response

        with self.assertRaisesRegex(RuntimeError, "Telegram rejeitou sendMessage"):
            emit_alert(self.cfg, dict(self.alert))

    @patch("app.alerts.requests.post")
    def test_rejects_response_delivered_to_a_different_chat(self, post):
        response = Mock()
        response.json.return_value = {
            "ok": True,
            "result": {"message_id": 988, "chat": {"id": -1009999999999}},
        }
        post.return_value = response

        with self.assertRaisesRegex(RuntimeError, "chat_id retornado diverge"):
            emit_alert(self.cfg, dict(self.alert))


if __name__ == "__main__":
    unittest.main()
