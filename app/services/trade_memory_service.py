"""
app/services/trade_memory_service.py — legacy disabled module.

Manual trade tracking/input is intentionally out of scope for Algo Stock Advisor.
The app should create value as a read-only viewing/discovery tool by detecting
broker positions automatically. This module remains only to prevent stale imports
from crashing older routes or scripts.
"""

from __future__ import annotations

from typing import Any, Callable

LogFn = Callable[[str], None]

DISABLED_MESSAGE = (
    "Manual trade memory is disabled. Open calendars must be auto-detected "
    "from broker option positions."
)


def ensure_db() -> None:
    return None


def list_calendar_trades(status: str | None = None) -> list[dict[str, Any]]:
    return []


def add_calendar_trade(data: dict[str, Any]) -> dict[str, Any]:
    raise RuntimeError(DISABLED_MESSAGE)


def close_calendar_trade(trade_id: int, close_value: Any = None, notes: str | None = None) -> dict[str, Any]:
    raise RuntimeError(DISABLED_MESSAGE)


def delete_trade(trade_id: int) -> bool:
    raise RuntimeError(DISABLED_MESSAGE)


def build_trade_memory_snapshot(open_options: dict[str, Any] | None = None, log_print: LogFn | None = None) -> dict[str, Any]:
    logger = log_print or (lambda msg: print(msg, flush=True))
    logger("Trade Memory disabled: using auto-detected broker option positions only.")
    return {
        "source": "manual_trade_memory_disabled",
        "enabled": False,
        "has_data": False,
        "open_trades": [],
        "watch_trades": [],
        "closed_trades": [],
        "matches": [],
        "errors": [DISABLED_MESSAGE],
        "summary": {"open_count": 0, "watch_count": 0, "closed_count": 0, "match_count": 0},
    }
