"""Persistent alert cooldown shared by the live monitor and tests."""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _utc(value=None):
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def cooldown_key(alert, timeframe):
    parts = (
        str(alert.get("agent") or ""),
        str(alert.get("symbol") or ""),
        str(timeframe or ""),
        str(alert.get("signal") or ""),
    )
    if not all(parts):
        raise ValueError("Cooldown requires agent, symbol, timeframe and signal.")
    return "|".join(parts)


def is_within_cooldown(last_sent, now, minutes):
    minutes = float(minutes)
    if minutes < 0:
        raise ValueError("cooldown minutes cannot be negative")
    if minutes == 0 or last_sent is None:
        return False
    previous = _utc(datetime.fromisoformat(last_sent) if isinstance(last_sent, str) else last_sent)
    current = _utc(now)
    return current < previous + timedelta(minutes=minutes)


class CooldownStore:
    """JSON-backed state; callers record only after alert delivery succeeds."""

    def __init__(self, path, minutes=60):
        self.path = Path(path)
        self.minutes = float(minutes)
        if self.minutes < 0:
            raise ValueError("cooldown minutes cannot be negative")
        self.last_sent = {}
        if self.path.exists():
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("last_sent", {}), dict):
                raise ValueError("Invalid alert cooldown state format.")
            self.last_sent = payload.get("last_sent", {})

    def allows(self, alert, timeframe, now=None):
        if self.minutes == 0:
            return True
        key = cooldown_key(alert, timeframe)
        return not is_within_cooldown(self.last_sent.get(key), _utc(now), self.minutes)

    def record(self, alert, timeframe, now=None):
        if self.minutes == 0:
            return
        key = cooldown_key(alert, timeframe)
        self.last_sent[key] = _utc(now).isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(
            json.dumps({"version": 1, "last_sent": self.last_sent}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
