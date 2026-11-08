import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .config import load_config
from .market_data import get_klines, get_24h_tickers, get_book_ticker_details
from .agents import run_market_agents, disabled_agent_status
from .new_launches import scan_new_launches, release_seen_token
from .bot_g import run_bot_g_observer
from .alerts import emit_alert
from .cooldown import CooldownStore
from .logging_setup import setup_logging

log = logging.getLogger("dot_cripto")


def _append_jsonl(path, record):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def process_active_market_agents(cfg, base, candidates, cooldown_store=None):
    """Run Bots A-E; continue after isolated errors but report an incomplete cycle."""
    sent = 0
    failures = []
    for symbol, market_snapshot in candidates.items():
        for tf in cfg["exchange"]["timeframes"]:
            try:
                candles = get_klines(base, symbol, tf, int(cfg["exchange"]["candle_limit"]))
                alerts = run_market_agents(symbol, tf, candles, cfg)
                for alert in alerts:
                    alert["spread_pct"] = market_snapshot["spread_pct"]
                    alert["best_bid"] = market_snapshot["bid"]
                    alert["best_ask"] = market_snapshot["ask"]
                    alert["market_filter_snapshot_utc"] = market_snapshot["collected_at_utc"]
                    alert["market_filter_max_spread_pct"] = float(cfg["exchange"]["max_spread_pct"])
                    if cooldown_store is not None and not cooldown_store.allows(alert, tf):
                        log.info("Cooldown ativo: %s %s %s (%s); alerta suprimido.",
                                 alert["agent"], symbol, tf, alert["signal"])
                        continue
                    emit_alert(cfg, alert)
                    if cooldown_store is not None:
                        cooldown_store.record(alert, tf)
                    sent += 1
                    log.info("Alerta: %s %s %s; spread=%.5f%%", alert["agent"], symbol, tf, alert["spread_pct"])
            except Exception as exc:
                failures.append({"symbol": symbol, "timeframe": tf, "error": type(exc).__name__})
                log.exception("Falha no ciclo do símbolo/timeframe %s %s", symbol, tf)
    return sent, failures


def main():
    cfg = load_config()
    setup_logging(cfg["_root"])
    base = cfg["exchange"]["base_url"]
    diagnostics_path = Path(cfg["_root"]) / "data" / "market_filter_audit.jsonl"
    cycle_failures = []
    try:
        tickers = get_24h_tickers(base)
    except Exception as exc:
        log.exception("Falha na coleta global de tickers; abortando coleta de mercado deste ciclo.")
        tickers = {}
        cycle_failures.append({"stage": "global_tickers", "error": type(exc).__name__})

    candidates = {}
    for symbol in cfg["exchange"]["symbols"]:
        t = tickers.get(symbol)
        if not t:
            log.warning("Filtro de mercado %s: rejeitado — ticker 24h ausente.", symbol)
            _append_jsonl(diagnostics_path, {
                "collected_at_utc": datetime.now(timezone.utc).isoformat(),
                "symbol": symbol, "decision": "rejected", "reason": "ticker_24h_missing",
            })
            cycle_failures.append({"stage": "symbol_ticker", "symbol": symbol, "error": "ticker_24h_missing"})
            continue
        try:
            qv = float(t.get("quoteVolume", 0))
            min_volume = float(cfg["exchange"]["min_quote_volume_24h_usdt"])
            if qv < min_volume:
                reason = "quote_volume_below_minimum"
                log.info("Filtro de mercado %s: rejeitado — volume_24h=%.2f USDT; mínimo=%.2f.", symbol, qv, min_volume)
                _append_jsonl(diagnostics_path, {
                    "collected_at_utc": datetime.now(timezone.utc).isoformat(),
                    "symbol": symbol, "decision": "rejected", "reason": reason,
                    "quote_volume_24h_usdt": qv, "minimum_quote_volume_24h_usdt": min_volume,
                })
                continue
            book = get_book_ticker_details(base, symbol)
            spread = book["spread_pct"]
            max_spread = float(cfg["exchange"]["max_spread_pct"])
            if spread > max_spread:
                log.info("Filtro de mercado %s: rejeitado — spread=%.5f%%; máximo=%.5f%%.", symbol, spread, max_spread)
                _append_jsonl(diagnostics_path, {
                    "collected_at_utc": datetime.now(timezone.utc).isoformat(),
                    "symbol": symbol, "decision": "rejected", "reason": "spread_above_maximum",
                    "quote_volume_24h_usdt": qv, **book, "max_spread_pct": max_spread,
                })
                continue
            candidates[symbol] = book
            log.info("Filtro de mercado %s: aprovado — volume_24h=%.2f USDT; bid=%s; ask=%s; spread=%.5f%%.",
                     symbol, qv, book["bid"], book["ask"], spread)
            _append_jsonl(diagnostics_path, {
                "collected_at_utc": datetime.now(timezone.utc).isoformat(),
                "symbol": symbol, "decision": "approved", "reason": "volume_and_spread_passed",
                "quote_volume_24h_usdt": qv, **book, "max_spread_pct": max_spread,
            })
        except Exception as exc:
            cycle_failures.append({"stage": "market_filter", "symbol": symbol, "error": type(exc).__name__})
            log.exception("Falha nos filtros de mercado para %s", symbol)
            _append_jsonl(diagnostics_path, {
                "collected_at_utc": datetime.now(timezone.utc).isoformat(),
                "symbol": symbol, "decision": "error", "reason": type(exc).__name__,
            })

    cooldown_minutes = float(cfg.get("alerts", {}).get("cooldown_minutes", 0))
    cooldown_store = CooldownStore(
        Path(cfg["_root"]) / "data" / "alert_cooldown.json", cooldown_minutes
    )
    sent, market_failures = process_active_market_agents(cfg, base, candidates, cooldown_store)
    cycle_failures.extend(
        {"stage": "active_agent_cycle", **failure} for failure in market_failures
    )

    if cfg.get("launches", {}).get("enabled", False) and cfg["_env"].get("bot_f_enabled", True):
        try:
            launch_alerts = scan_new_launches(cfg)
            for alert in launch_alerts:
                try:
                    emit_alert(cfg, alert)
                    sent += 1
                    log.info("Alerta: %s %s na rede %s", alert["agent"], alert["symbol"], alert["chain_id"])
                except Exception:
                    release_seen_token(cfg, alert)
                    log.exception("Falha no envio do alerta Bot F; contrato será reavaliado no próximo ciclo: %s", alert.get("token_address"))
            log.info("Bot F concluído. Candidatos aprovados para watchlist=%d", len(launch_alerts))
        except Exception:
            log.exception("Bot F indisponível neste ciclo; nenhum candidato foi aprovado sem validação.")

    elif cfg.get("launches", {}).get("enabled", False):
        log.info("Bot F não executado neste workflow; execução independente agendada separadamente.")

    try:
        bot_g_result = run_bot_g_observer(cfg)
        log.info("Bot G: status=%s; ativos coletados=%d", bot_g_result["status"], bot_g_result["collected"])
    except Exception:
        log.exception("Bot G indisponível neste ciclo; nenhum alerta social foi emitido.")

    for status in disabled_agent_status(cfg):
        log.info("%s: %s — %s", status["agent"], status["status"], status["reason"])
    log.info("Ciclo concluído. Símbolos elegíveis=%d, alertas=%d, falhas críticas A-E=%d",
             len(candidates), sent, len(cycle_failures))
    if cycle_failures:
        log.error("Ciclo A-E incompleto: %s", json.dumps(cycle_failures, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
