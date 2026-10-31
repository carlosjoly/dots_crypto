import unittest

from app.alerts import format_message


class AlertFormattingTests(unittest.TestCase):
    def setUp(self):
        self.alert = {
            "agent": "Bot E — Swing trade",
            "symbol": "ETHUSDT",
            "timeframe": "15m",
            "price": 2251.94,
            "signal": "configuração potencial de alta",
            "reason": (
                "Tendência SMA20>SMA50, RSI=56.3; referência técnica, não recomendação. "
                "Invalidação indicativa: 2246.9495; objetivo ilustrativo: 2259.4258."
            ),
            "change_pct": 0.047,
            "relative_volume": 1.19,
            "rsi": 56.3,
            "atr": 3.32701348,
            "confidence": "não calibrada",
            "risk_note": "Estratégia experimental, ainda sem validação por backtest. Spread, custos, liquidez e contexto de mercado precisam ser verificados.",
            "candle_time_utc": "2026-10-31 16:14:59.999000+00:00",
        }

    def test_uses_real_line_breaks_and_brazilian_numbers(self):
        message = format_message(self.alert)
        self.assertIn("\n\nAtivo: ETH/USDT", message)
        self.assertNotIn("\\n", message)
        self.assertIn("Preço de referência: US$ 2.251,94", message)
        self.assertIn("- Variação: +0,047%.", message)
        self.assertIn("- RSI: 56,3.", message)
        self.assertIn("- Volume relativo: 1,19x.", message)
        self.assertIn("- ATR: 3,33.", message)
        self.assertIn("- Invalidação: US$ 2.246,95.", message)
        self.assertIn("- Objetivo ilustrativo: US$ 2.259,43.", message)
        self.assertIn("Período: 15 minutos", message)

    def test_reasons_and_technical_references_are_separate(self):
        message = format_message(self.alert)
        reasons, references = message.split("Referências técnicas:", 1)
        self.assertIn("- Tendência SMA 20 acima da SMA 50.", reasons)
        self.assertIn("- RSI: 56,3.", reasons)
        self.assertIn("- Volume relativo: 1,19x.", reasons)
        self.assertNotIn("Invalidação indicativa", reasons)
        self.assertNotIn("objetivo ilustrativo", reasons.lower())
        self.assertIn("- Invalidação: US$ 2.246,95.", references)
        self.assertIn("- Objetivo ilustrativo: US$ 2.259,43.", references)

    def test_converts_utc_to_brasilia_and_removes_microseconds(self):
        message = format_message(self.alert)
        self.assertIn("31/10/2026 às 13:14:59 (UTC-3)", message)
        self.assertNotIn("999000", message)

    def test_does_not_mutate_original_alert(self):
        original = dict(self.alert)
        format_message(self.alert)
        self.assertEqual(self.alert, original)


if __name__ == "__main__":
    unittest.main()
