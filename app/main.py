import logging
from .config import load_config
from .market_data import get_klines, get_24h_tickers, get_book_ticker
from .agents import run_market_agents, disabled_agent_status
from .alerts import emit_alert
from .logging_setup import setup_logging

log = logging.getLogger("dot_cripto")

def main():
    cfg = load_config()
    setup_logging(cfg["_root"])
    base = cfg["exchange"]["base_url"]
    try:
        tickers = get_24h_tickers(base)
    except Exception:
        log.exception("Falha na coleta global de tickers; abortando coleta de mercado deste ciclo.")
        return 2

    candidates = []
    for symbol in cfg["exchange"]["symbols"]:
        t = tickers.get(symbol)
        if not t:
            log.warning("Símbolo sem ticker 24h: %s", symbol)
            continue
        try:
            qv = float(t.get("quoteVolume", 0))
            if qv < float(cfg["exchange"]["min_quote_volume_24h_usdt"]):
                log.info("Ignorado por volume 24h baixo: %s (%s USDT)", symbol, qv)
                continue
            spread = get_book_ticker(base, symbol)
            if spread > float(cfg["exchange"]["max_spread_pct"]):
                log.info("Ignorado por spread: %s (%.4f%%)", symbol, spread)
                continue
            candidates.append(symbol)
        except Exception:
            log.exception("Falha nos filtros de mercado para %s", symbol)

    sent = 0
    for symbol in candidates:
        for tf in cfg["exchange"]["timeframes"]:
            try:
                candles = get_klines(base, symbol, tf, int(cfg["exchange"]["candle_limit"]))
                alerts = run_market_agents(symbol, tf, candles, cfg)
                for alert in alerts:
                    alert["spread_pct"] = None
                    emit_alert(cfg, alert)
                    sent += 1
                    log.info("Alerta: %s %s %s", alert["agent"], symbol, tf)
            except Exception:
                # Isolate errors per symbol/timeframe so other modules continue.
                log.exception("Falha no ciclo do símbolo/timeframe %s %s", symbol, tf)

    for status in disabled_agent_status():
        log.info("%s: %s — %s", status["agent"], status["status"], status["reason"])
    log.info("Ciclo concluído. Símbolos elegíveis=%d, alertas=%d", len(candidates), sent)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
