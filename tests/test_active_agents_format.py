import unittest

from app.alerts import format_message


class ActiveAgentsFormattingTests(unittest.TestCase):
    BASE = {
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "price": 77644.01,
        "change_pct": 0.023,
        "relative_volume": 1.13,
        "rsi": 60.2,
        "atr": 81.31051256,
        "confidence": "não calibrada",
        "risk_note": "Estratégia experimental; ainda sem backtest validado.",
        "candle_time_utc": "2026-10-31T17:14:59.999000+00:00",
    }

    CASES = [
        ("Bot A — Altas expressivas", "movimento de alta",
         "Variação positiva de 1.80% com volume relativo 1.80x. Não implica compra."),
        ("Bot B — Quedas expressivas", "movimento de baixa",
         "Variação negativa de 1.80% com volume relativo 1.80x. Não implica venda."),
        ("Bot C — Reversões", "possível estabilização após sobrevenda",
         "RSI baixo e candle recente positivo com volume superior ao candle anterior; reversão não confirmada."),
        ("Bot D — Rompimentos", "possível rompimento de resistência",
         "Fecho acima da máxima das 20 velas anteriores com volume relativo 1.80x; confirmar risco de falso rompimento."),
        ("Bot E — Swing trade", "configuração potencial de alta",
         "Tendência SMA20>SMA50, RSI=60.2; referência técnica, não recomendação. "
         "Invalidação indicativa: 77522.044; objetivo ilustrativo: 77826.959."),
    ]

    def test_all_five_active_agents_use_the_same_complete_layout(self):
        for agent, signal, reason in self.CASES:
            with self.subTest(agent=agent):
                alert = dict(self.BASE, agent=agent, signal=signal, reason=reason)
                message = format_message(alert)
                self.assertIn(f"DOT CRIPTO | {agent.upper()}", message)
                self.assertIn("Ativo: BTC/USDT", message)
                self.assertIn("Período: 15 minutos", message)
                self.assertIn("Preço de referência: US$ 77.644,01", message)
                self.assertIn(f"SINAL: {signal.upper()}", message)
                self.assertIn("Motivos:", message)
                self.assertIn("Referências técnicas:", message)
                self.assertIn("CONFIANÇA: não calibrada.", message)
                self.assertIn("RISCO: Estratégia experimental", message)
                self.assertIn("31/10/2026 às 14:14:59 (UTC-3)", message)
                self.assertIn("- Variação: +0,023%.", message)
                self.assertIn("- RSI: 60,2.", message)
                self.assertIn("- Volume relativo: 1,13x.", message)
                self.assertIn("- ATR: 81,31.", message)
                self.assertEqual(message.count("Volume relativo:"), 1)
                self.assertNotIn("\\n", message)

    def test_only_bot_e_shows_stop_and_target_when_provided(self):
        for agent, signal, reason in self.CASES:
            with self.subTest(agent=agent):
                alert = dict(self.BASE, agent=agent, signal=signal, reason=reason)
                message = format_message(alert)
                if agent.startswith("Bot E"):
                    self.assertIn("- Invalidação: US$ 77.522,04.", message)
                    self.assertIn("- Objetivo ilustrativo: US$ 77.826,96.", message)
                else:
                    self.assertNotIn("Invalidação:", message)
                    self.assertNotIn("Objetivo ilustrativo:", message)
                    self.assertNotIn("US$ N/D", message)

    def test_volume_cleanup_does_not_leave_dangling_conjunctions(self):
        for agent, signal, reason in self.CASES:
            with self.subTest(agent=agent):
                alert = dict(self.BASE, agent=agent, signal=signal, reason=reason)
                message = format_message(alert)
                self.assertNotIn("com .", message.lower())
                self.assertNotIn("com;", message.lower())


if __name__ == "__main__":
    unittest.main()
