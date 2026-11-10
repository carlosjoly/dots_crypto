"""Read-only LunarCrush Hobby adapter.

This module only collects fields empirically verified on the free endpoint.
It does not generate signals, call GoPlus, persist snapshots, or send alerts.
"""
from datetime import datetime, timezone
import math

import requests

LUNARCRUSH_COINS_URL = "https://lunarcrush.com/api4/public/coins/list/v2"


class LunarCrushAccessError(RuntimeError):
    """Sanitized provider error; never includes credentials or response body."""


def _number(value, field, minimum=None, maximum=None):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"LunarCrush field {field} missing or invalid") from None
    if not math.isfinite(result):
        raise ValueError(f"LunarCrush field {field} is not finite")
    if minimum is not None and result < minimum:
        raise ValueError(f"LunarCrush field {field} below minimum")
    if maximum is not None and result > maximum:
        raise ValueError(f"LunarCrush field {field} above maximum")
    return result


def _rows(payload):
    if not isinstance(payload, dict):
        raise ValueError("LunarCrush response must be a JSON object")
    data = payload.get("data")
    if isinstance(data, list):
        return data
    raise ValueError("LunarCrush response has no data list")


def _generated_timestamp(payload):
    """Return the provider's documented config.generated timestamp, if valid."""
    config = payload.get("config") if isinstance(payload, dict) else None
    generated = config.get("generated") if isinstance(config, dict) else None
    try:
        seconds = float(generated)
        if not math.isfinite(seconds) or seconds <= 0:
            return None
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _fallback_coin_generated(symbol, api_key, session):
    """Use the documented per-coin v1 snapshot only when bulk metadata omits generated.

    This is a bounded fallback (one request per monitored asset, at most hourly).
    It never substitutes local collection time or a price-only row timestamp.
    """
    try:
        response = session.get(
            f"https://lunarcrush.com/api4/public/coins/{symbol}/v1",
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=20,
        )
        if response.status_code != 200:
            return None
        return _generated_timestamp(response.json())
    except (requests.RequestException, ValueError, TypeError):
        return None


def fetch_social_snapshots(symbols, api_key, session=requests, now=None):
    """Fetch bulk social metrics and verify freshness from provider generation metadata.

    The v2 bulk endpoint is preferred. If it omits the documented config.generated
    timestamp, use a bounded per-coin v1 metadata fallback. Collection time and
    last-updated price time are never treated as social-source freshness.
    """
    key = str(api_key or "").strip()
    if not key:
        raise LunarCrushAccessError("SOCIAL_API_KEY is not configured")

    requested = {str(item).upper().removesuffix("USDT") for item in symbols}
    if not requested:
        return {}

    try:
        response = session.get(
            LUNARCRUSH_COINS_URL,
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            timeout=20,
        )
    except requests.RequestException as exc:
        raise LunarCrushAccessError(
            f"LunarCrush network error: {type(exc).__name__}"
        ) from None

    if response.status_code in (401, 403):
        raise LunarCrushAccessError(
            f"LunarCrush access denied (HTTP {response.status_code}); check key and free-tier entitlement"
        )
    if response.status_code == 429:
        raise LunarCrushAccessError("LunarCrush rate limit reached (HTTP 429)")
    if response.status_code != 200:
        raise LunarCrushAccessError(
            f"LunarCrush request failed (HTTP {response.status_code})"
        )

    try:
        payload = response.json()
    except ValueError:
        raise ValueError("LunarCrush returned invalid JSON") from None

    collected = (now or datetime.now(timezone.utc))
    if collected.tzinfo is None:
        raise ValueError("Collection timestamp must include timezone")
    collected_iso = collected.astimezone(timezone.utc).isoformat()
    source_updated_iso = _generated_timestamp(payload)

    output = {}
    for row in _rows(payload):
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol", "")).upper()
        if symbol not in requested:
            continue
        output_symbol = symbol + "USDT"
        if output_symbol in output:
            raise ValueError(f"LunarCrush returned duplicate symbol {symbol}")

        volume = _number(row.get("social_volume_24h"), "social_volume_24h", 0)
        sentiment = _number(row.get("sentiment"), "sentiment", 0, 100)
        output[output_symbol] = {
            "symbol": output_symbol,
            "social_volume_24h": int(volume) if volume.is_integer() else volume,
            "sentiment": sentiment,
            "collected_at": collected_iso,
            "source_updated_at": source_updated_iso,
            "source_freshness_verified": source_updated_iso is not None,
            "social_contributors": None,
            "spam": None,
            "bot_ratio_pct": None,
            # This is a price timestamp, not a social-metric timestamp.
            "price_updated_at": row.get("last_updated_price"),
        }

    # The bulk list response sometimes omits config.generated. Resolve freshness
    # from the provider's documented per-coin snapshot endpoint, never from local
    # collection time or last_updated_price.
    if source_updated_iso is None:
        for output_symbol, snapshot in output.items():
            coin = output_symbol.removesuffix("USDT")
            snapshot["source_updated_at"] = _fallback_coin_generated(coin, key, session)
            snapshot["source_freshness_verified"] = snapshot["source_updated_at"] is not None

    return output
