"""
app/services/calendar_lifecycle_service.py — Calendar Lifecycle Check v1.

Evaluates detected open calendar spreads from the Open Options Position Detector.
This is read-only and advisory. It does not place or close trades.

Entry debit may be unavailable or only estimated from broker cost basis. The
project intentionally avoids manual trade entry; lifecycle confidence improves
when the broker exposes reliable option cost basis and current quotes.
"""

from __future__ import annotations

from typing import Any, Callable

from app import config

LogFn = Callable[[str], None]


def evaluate_calendar_lifecycle(
    open_options: dict[str, Any] | None,
    tradier_snapshot: dict[str, dict[str, Any]] | None = None,
    earnings_events: dict[str, dict[str, Any]] | None = None,
    trade_memory: dict[str, Any] | None = None,
    log_print: LogFn | None = None,
) -> dict[str, Any]:
    """Evaluate detected open calendars for hold/exit/check actions."""
    logger = log_print or (lambda msg: print(msg, flush=True))
    open_options = open_options or {}
    tradier_snapshot = tradier_snapshot or {}
    earnings_events = earnings_events or {}
    trade_memory = trade_memory or {}

    result: dict[str, Any] = {
        "source": "calendar_lifecycle_v1",
        "enabled": bool(config.CALENDAR_LIFECYCLE_ENABLED),
        "has_data": False,
        "checks": [],
        "summary": {},
        "errors": [],
    }

    if not config.CALENDAR_LIFECYCLE_ENABLED:
        result["errors"].append("CALENDAR_LIFECYCLE_ENABLED=false")
        logger("Calendar Lifecycle Check v1 disabled by CALENDAR_LIFECYCLE_ENABLED=false.")
        return _finalize(result)

    calendars = open_options.get("calendars", []) if isinstance(open_options, dict) else []
    calendars = [item for item in calendars if isinstance(item, dict)]

    if not calendars:
        logger("Calendar Lifecycle Check v1: no detected open calendars to evaluate.")
        return _finalize(result)

    logger(f"Calendar Lifecycle Check v1 evaluating {len(calendars)} detected open calendar(s).")

    checks: list[dict[str, Any]] = []
    for calendar in calendars:
        check = _evaluate_one_calendar(calendar, tradier_snapshot, earnings_events, trade_memory)
        checks.append(check)
        logger(
            f"Lifecycle {check.get('ticker')}: action={check.get('action')} | "
            f"front_dte={check.get('front_dte')} | moneyness={check.get('short_leg_moneyness_pct')} | "
            f"current_debit={check.get('current_mid_debit')}"
        )

    result["checks"] = checks
    result["has_data"] = bool(checks)
    return _finalize(result)


def _evaluate_one_calendar(
    calendar: dict[str, Any],
    tradier_snapshot: dict[str, dict[str, Any]],
    earnings_events: dict[str, dict[str, Any]],
    trade_memory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ticker = str(calendar.get("underlying") or calendar.get("ticker") or "").upper().strip()
    option_type = str(calendar.get("option_type") or "call").lower()
    strike = _float_or_none(calendar.get("strike"))
    quantity = _float_or_none(calendar.get("quantity")) or 0.0
    front_dte = _int_or_none(calendar.get("front_dte"))
    back_dte = _int_or_none(calendar.get("back_dte"))
    current_mid_debit = _float_or_none(calendar.get("current_mid_debit"))
    current_value = _float_or_none(calendar.get("current_value_estimate"))
    cost_basis_estimate = _float_or_none(calendar.get("cost_basis_estimate"))

    underlying_price = _underlying_price_for(ticker, tradier_snapshot)
    if underlying_price is None:
        underlying_price = _float_or_none((calendar.get("short_front_leg") or {}).get("underlying_price"))

    memory_trade = _matching_memory_trade(calendar, trade_memory or {})

    entry_debit_estimate = None
    pnl_pct = None
    if memory_trade and _float_or_none(memory_trade.get("entry_debit")) is not None:
        entry_debit_estimate = _float_or_none(memory_trade.get("entry_debit"))
        if entry_debit_estimate and entry_debit_estimate > 0 and current_mid_debit is not None:
            pnl_pct = ((current_mid_debit - entry_debit_estimate) / entry_debit_estimate) * 100.0
    elif cost_basis_estimate is not None and quantity > 0:
        # Tradier cost-basis sign conventions can vary. Use absolute value and
        # present this as an estimate only.
        entry_debit_estimate = abs(cost_basis_estimate) / (quantity * 100.0)
        if entry_debit_estimate > 0 and current_mid_debit is not None:
            pnl_pct = ((current_mid_debit - entry_debit_estimate) / entry_debit_estimate) * 100.0

    short_moneyness_pct = _short_leg_moneyness_pct(option_type, strike, underlying_price)
    short_itm = _short_leg_is_itm(option_type, strike, underlying_price)
    near_money = short_moneyness_pct is not None and abs(short_moneyness_pct) <= float(config.CALENDAR_LIFECYCLE_NEAR_MONEY_PCT)

    earnings = earnings_events.get(ticker) or {}
    earnings_date = earnings.get("earnings_date") or earnings.get("date")
    earnings_session = earnings.get("session_label") or "Unknown"
    days_until_earnings = _int_or_none(earnings.get("days_until_earnings"))
    earnings_known = bool(earnings.get("has_data"))

    reasons: list[str] = []
    risks: list[str] = []
    action = "HOLD / MONITOR"
    confidence = "Low-Medium"

    if current_mid_debit is not None:
        reasons.append("Current spread value is available from detected leg quotes.")
    else:
        risks.append("Current spread value unavailable; one or both leg quotes may be missing.")

    if entry_debit_estimate is not None and pnl_pct is not None:
        if memory_trade:
            reasons.append(f"Entry debit loaded from legacy trade memory: {entry_debit_estimate:.2f}.")
        else:
            reasons.append(f"Estimated entry debit from broker cost basis: {entry_debit_estimate:.2f}.")
        confidence = "Medium" if not memory_trade else "Medium-High"
        target_pct = _float_or_none((memory_trade or {}).get("profit_target_pct"))
        max_loss_pct = _float_or_none((memory_trade or {}).get("max_loss_pct"))
        if target_pct is None:
            target_pct = float(config.CALENDAR_LIFECYCLE_PROFIT_TARGET_PCT)
        if max_loss_pct is None:
            max_loss_pct = float(config.CALENDAR_LIFECYCLE_MAX_LOSS_PCT)
        if pnl_pct >= target_pct:
            action = "TAKE PROFIT / REVIEW EXIT"
            reasons.append("Estimated gain has reached or exceeded the configured profit target.")
        elif pnl_pct <= max_loss_pct:
            action = "CUT / REVIEW EXIT"
            risks.append("Estimated loss has exceeded the configured max-loss threshold.")
    else:
        risks.append("Entry debit is unknown or only partially available; exact % P/L cannot be calculated yet.")

    if front_dte is not None:
        if front_dte <= int(config.CALENDAR_LIFECYCLE_URGENT_DTE):
            action = _more_urgent(action, "URGENT REVIEW / EXIT CHECK")
            risks.append("Short front leg is very close to expiration; gamma and assignment risk are elevated.")
        elif front_dte <= int(config.CALENDAR_LIFECYCLE_REVIEW_DTE):
            action = _more_urgent(action, "RECHECK BEFORE CLOSE")
            risks.append("Short front leg is inside the review window.")
        else:
            reasons.append("Short front leg is not yet inside the urgent DTE window.")

    if short_itm is True:
        action = _more_urgent(action, "URGENT REVIEW / EXIT CHECK")
        risks.append("Short leg appears in the money; assignment risk and pin risk require review.")
    elif short_itm is False and near_money:
        action = _more_urgent(action, "RECHECK BEFORE CLOSE")
        risks.append("Short leg is near the money; reprice before market close.")
    elif short_itm is False:
        reasons.append("Short leg appears out of the money based on current underlying quote.")

    if earnings_known:
        reasons.append(f"Upcoming/recent earnings context available: {earnings_date} ({earnings_session}).")
        if days_until_earnings is not None and 0 <= days_until_earnings <= 7:
            action = _more_urgent(action, "EVENT WINDOW REVIEW")
            risks.append("Earnings are within one week; confirm whether this trade should be held through the event.")
    else:
        risks.append("Earnings timestamp unavailable; confirm earnings date/time before holding through an event window.")

    next_check = _next_check(action, front_dte, short_itm, earnings_known)

    return {
        "strategy": "Calendar Lifecycle Check v1",
        "ticker": ticker,
        "underlying": ticker,
        "option_type": option_type,
        "strike": strike,
        "quantity": quantity,
        "front_expiration": calendar.get("front_expiration"),
        "back_expiration": calendar.get("back_expiration"),
        "front_dte": front_dte,
        "back_dte": back_dte,
        "short_front_leg": calendar.get("short_front_leg") or {},
        "long_back_leg": calendar.get("long_back_leg") or {},
        "underlying_price": underlying_price,
        "short_leg_moneyness_pct": short_moneyness_pct,
        "short_leg_itm": short_itm,
        "current_mid_debit": current_mid_debit,
        "current_value_estimate": current_value,
        "entry_debit_estimate": entry_debit_estimate,
        "cost_basis_estimate": cost_basis_estimate,
        "estimated_pnl_pct": pnl_pct,
                "earnings_date": earnings_date,
        "earnings_session": earnings_session,
        "days_until_earnings": days_until_earnings,
        "earnings_known": earnings_known,
        "action": action,
        "confidence": confidence,
        "reasons": reasons,
        "risks": risks,
        "next_check": next_check,
    }



def _matching_memory_trade(calendar: dict[str, Any], trade_memory: dict[str, Any]) -> dict[str, Any] | None:
    ticker = str(calendar.get("underlying") or calendar.get("ticker") or "").upper().strip()
    option_type = str(calendar.get("option_type") or "call").lower().strip()
    strike = _float_or_none(calendar.get("strike"))
    front = str(calendar.get("front_expiration") or "").strip()
    back = str(calendar.get("back_expiration") or "").strip()
    for trade in trade_memory.get("open_trades", []) or []:
        if not isinstance(trade, dict):
            continue
        if str(trade.get("ticker") or "").upper().strip() != ticker:
            continue
        if str(trade.get("option_type") or "call").lower().strip() != option_type:
            continue
        if _float_or_none(trade.get("strike")) != strike:
            continue
        if str(trade.get("short_expiration") or "").strip() != front:
            continue
        if str(trade.get("long_expiration") or "").strip() != back:
            continue
        return trade
    return None

def _underlying_price_for(ticker: str, tradier_snapshot: dict[str, dict[str, Any]]) -> float | None:
    data = tradier_snapshot.get(ticker) or tradier_snapshot.get(str(ticker).upper()) or {}
    quote = data.get("quote", {}) if isinstance(data, dict) else {}
    for key in ["last", "bid", "ask", "close", "prevclose"]:
        val = _float_or_none(quote.get(key))
        if val is not None and val > 0:
            return val
    return None


def _short_leg_moneyness_pct(option_type: str, strike: float | None, underlying_price: float | None) -> float | None:
    if strike is None or underlying_price is None or strike <= 0:
        return None
    if option_type == "put":
        # Positive means the short put is ITM.
        return ((strike - underlying_price) / strike) * 100.0
    # Positive means the short call is ITM.
    return ((underlying_price - strike) / strike) * 100.0


def _short_leg_is_itm(option_type: str, strike: float | None, underlying_price: float | None) -> bool | None:
    if strike is None or underlying_price is None:
        return None
    if option_type == "put":
        return underlying_price < strike
    return underlying_price > strike


def _more_urgent(current: str, candidate: str) -> str:
    rank = {
        "HOLD / MONITOR": 0,
        "RECHECK BEFORE CLOSE": 1,
        "EVENT WINDOW REVIEW": 2,
        "TAKE PROFIT / REVIEW EXIT": 3,
        "CUT / REVIEW EXIT": 4,
        "URGENT REVIEW / EXIT CHECK": 5,
    }
    return candidate if rank.get(candidate, 0) > rank.get(current, 0) else current


def _next_check(action: str, front_dte: int | None, short_itm: bool | None, earnings_known: bool) -> str:
    if action == "URGENT REVIEW / EXIT CHECK":
        return "Reprice immediately; check short-leg moneyness, assignment risk, and close/roll options before market close."
    if action in {"TAKE PROFIT / REVIEW EXIT", "CUT / REVIEW EXIT"}:
        return "Compare live tradable debit/credit against target and review exit before market close."
    if front_dte is not None and front_dte <= int(config.CALENDAR_LIFECYCLE_REVIEW_DTE):
        return "Recheck before market close while the short leg is inside the review window."
    if not earnings_known:
        return "Monitor daily and confirm earnings timestamp before holding through an event window."
    return "Monitor daily; reprice if underlying approaches the short strike or earnings window changes."


def _finalize(result: dict[str, Any]) -> dict[str, Any]:
    checks = result.get("checks") or []
    urgent = [c for c in checks if "URGENT" in str(c.get("action") or "")]
    exits = [c for c in checks if "EXIT" in str(c.get("action") or "") or "CUT" in str(c.get("action") or "")]
    result["summary"] = {
        "calendar_count": len(checks),
        "urgent_count": len(urgent),
        "exit_review_count": len(exits),
        "has_open_calendars": bool(checks),
    }
    return result


def _float_or_none(value: Any) -> float | None:
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        if value in {None, ""}:
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None
