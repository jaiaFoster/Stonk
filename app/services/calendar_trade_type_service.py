"""Pure earnings-calendar trade-type facts.

This module intentionally returns facts only. It does not assign final
verdicts, entry permission, lifecycle state, or Daily Opportunity eligibility.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app import config


def classify_calendar_trade_type(candidate: dict[str, Any], ranking: dict[str, Any] | None = None) -> dict[str, Any]:
    earnings = _first_dict(
        candidate.get("earnings_event"),
        candidate.get("earnings"),
        ((ranking or {}).get("strategy") or {}).get("earnings") if isinstance((ranking or {}).get("strategy"), dict) else None,
    )
    event_date = _parse_date(earnings.get("earnings_date") or earnings.get("date"))
    front = _parse_date(candidate.get("front_expiration") or candidate.get("short_expiration"))
    back = _parse_date(candidate.get("back_expiration") or candidate.get("long_expiration"))
    session = _normalize_session(earnings.get("session_label") or earnings.get("session") or earnings.get("earnings_session"))
    confirmed = bool(earnings.get("is_timestamp_confirmed")) and session != "unknown"
    max_front_days = int(getattr(config, "CALENDAR_TRUE_IV_FRONT_MAX_DAYS_AFTER_EVENT", 7) or 7)

    if not event_date or not front or not back or (not confirmed and not bool(getattr(config, "CALENDAR_UNKNOWN_TIMESTAMP_CAN_PASS", False))):
        key = "unknown_event_timing"
        detail = "Earnings date/session is missing or unconfirmed."
    elif session == "unknown":
        key = "unknown_event_timing"
        detail = "Earnings session is unknown, so event inclusion cannot be trusted."
    elif not _expiration_includes_event(front, event_date, session):
        if _expiration_includes_event(back, event_date, session):
            key = "pre_earnings_financing_or_directional_long_vol"
            detail = "Short leg expires before the earnings release; long leg carries the event."
        else:
            key = "not_an_earnings_calendar"
            detail = "Neither expiration cleanly carries the earnings event."
    elif (front - event_date).days > max_front_days:
        key = "invalid_for_earnings_strategy"
        detail = f"Front short expiration is {(front - event_date).days} days after earnings, beyond the {max_front_days}-day event-IV window."
    elif back <= front:
        key = "invalid_for_earnings_strategy"
        detail = "Long expiration must be after the short expiration."
    elif front < event_date:
        key = "pre_earnings_financing_or_directional_long_vol"
        detail = "Short leg expires before earnings; this is not a true event-IV short calendar."
    else:
        key = "true_earnings_iv_crush_calendar"
        detail = "Short/front leg includes the earnings event and long leg remains open after it."

    labels = {
        "true_earnings_iv_crush_calendar": "TRUE EARNINGS IV-CRUSH CALENDAR",
        "pre_earnings_financing_or_directional_long_vol": "PRE-EARNINGS FINANCING / LONG-VOL TRADE",
        "not_an_earnings_calendar": "NOT AN EARNINGS CALENDAR",
        "invalid_for_earnings_strategy": "INVALID FOR STRATEGY",
        "unknown_event_timing": "TIMESTAMP UNKNOWN",
    }
    return {
        "trade_type": key,
        "trade_type_label": labels.get(key, "TIMESTAMP UNKNOWN"),
        "trade_type_detail": detail,
        "event_session": session,
        "front_days_after_event": None if not event_date or not front else (front - event_date).days,
    }


def classify_trade_type(candidate: dict[str, Any], ranking: dict[str, Any] | None = None) -> dict[str, Any]:
    return classify_calendar_trade_type(candidate, ranking)


def _first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict) and value:
            return value
    return {}


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _normalize_session(value: Any) -> str:
    text = str(value or "").lower()
    if text in {"bmo", "before_open", "before market open", "before"}:
        return "bmo"
    if text in {"amc", "after_close", "after market close", "after"}:
        return "amc"
    return "unknown"


def _expiration_includes_event(expiration: date, event_date: date, session: str) -> bool:
    if session == "bmo":
        return expiration >= event_date
    if session == "amc":
        return expiration > event_date
    return expiration >= event_date
