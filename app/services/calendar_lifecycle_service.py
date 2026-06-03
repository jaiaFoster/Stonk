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
from app.services.calendar_hold_through_service import build_hold_through_score
from app.services.calendar_verdict_service import classify_trade_type

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
    pricing_quality = calendar.get("pricing_quality") if isinstance(calendar.get("pricing_quality"), dict) else {}

    underlying_price, underlying_price_source = _best_underlying_price(ticker, calendar, tradier_snapshot)

    # Manual trade memory is intentionally not used for Algo Stock Advisor.
    # Entry debit should come from broker-detected option leg average prices or
    # broker cost basis only.
    entry_debit_estimate = _float_or_none(calendar.get("entry_mid_debit_estimate"))
    entry_debit_source = str(calendar.get("entry_source") or "broker_detected")
    if entry_debit_estimate is None and cost_basis_estimate is not None and quantity > 0:
        entry_debit_estimate = _normalize_entry_debit_from_total_cost(cost_basis_estimate, quantity)
        entry_debit_source = "broker_total_cost_basis_fallback"

    pnl_pct = _float_or_none(calendar.get("pnl_pct_estimate"))
    pnl_per_spread = _float_or_none(calendar.get("pnl_per_spread_estimate"))
    pnl_total = _float_or_none(calendar.get("pnl_total_estimate"))
    if pnl_pct is None and entry_debit_estimate is not None and entry_debit_estimate > 0 and current_mid_debit is not None:
        pnl_pct = ((current_mid_debit - entry_debit_estimate) / abs(entry_debit_estimate)) * 100.0
    if pnl_per_spread is None and entry_debit_estimate is not None and current_mid_debit is not None:
        pnl_per_spread = (current_mid_debit - entry_debit_estimate) * 100.0
    if pnl_total is None and pnl_per_spread is not None and quantity:
        pnl_total = pnl_per_spread * quantity

    current_value_per_spread = current_mid_debit * 100.0 if current_mid_debit is not None else None
    entry_value_per_spread = entry_debit_estimate * 100.0 if entry_debit_estimate is not None else None

    short_moneyness_pct = _short_leg_moneyness_pct(option_type, strike, underlying_price)
    short_itm = _short_leg_is_itm(option_type, strike, underlying_price)
    near_money = short_moneyness_pct is not None and abs(short_moneyness_pct) <= float(config.CALENDAR_LIFECYCLE_NEAR_MONEY_PCT)
    distance_to_strike = None
    distance_to_strike_pct = None
    if strike is not None and underlying_price is not None:
        distance_to_strike = underlying_price - strike if option_type != "put" else strike - underlying_price
        if strike > 0:
            distance_to_strike_pct = (distance_to_strike / strike) * 100.0
    assignment_risk_level = _assignment_risk_level(short_itm, near_money, front_dte)
    assignment_risk_reasons: list[str] = []
    short_leg_mid = _float_or_none((calendar.get("short_front_leg") or {}).get("mid"))
    long_leg_mid = _float_or_none((calendar.get("long_back_leg") or {}).get("mid"))
    short_intrinsic = _short_leg_intrinsic_value(option_type, strike, underlying_price)
    short_extrinsic = None
    if short_leg_mid is not None and short_intrinsic is not None:
        short_extrinsic = max(0.0, short_leg_mid - short_intrinsic)
    net_delta = _net_greek(calendar, "delta")
    net_theta = _net_greek(calendar, "theta")
    net_iv = _net_greek(calendar, "iv")

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

    target_pct = float(getattr(config, "CALENDAR_LIFECYCLE_TAKE_PROFIT_PCT", config.CALENDAR_LIFECYCLE_PROFIT_TARGET_PCT))
    max_loss_pct = float(getattr(config, "CALENDAR_LIFECYCLE_STOP_LOSS_PCT", config.CALENDAR_LIFECYCLE_MAX_LOSS_PCT))
    target_debit = entry_debit_estimate * (1.0 + target_pct / 100.0) if entry_debit_estimate is not None else None
    stop_debit = entry_debit_estimate * (1.0 + max_loss_pct / 100.0) if entry_debit_estimate is not None else None

    if entry_debit_estimate is not None and pnl_pct is not None:
        reasons.append(f"Entry debit estimated from {entry_debit_source}: {entry_debit_estimate:.2f}.")
        confidence = "Medium-High" if (pricing_quality.get("confidence") == "high") else "Medium"
        if pnl_pct >= target_pct:
            action = "TAKE PROFIT / REVIEW EXIT"
            reasons.append("Estimated gain has reached or exceeded the configured profit target.")
        elif pnl_pct <= max_loss_pct:
            action = "CUT / REVIEW EXIT"
            risks.append("Estimated loss has exceeded the configured max-loss threshold.")
    else:
        risks.append("Entry debit is unknown or only partially available; exact % P/L cannot be calculated yet.")

    for warning in pricing_quality.get("warnings", []) or []:
        risks.append(f"Pricing quality warning: {warning}.")

    if front_dte is not None:
        if front_dte <= int(config.CALENDAR_LIFECYCLE_URGENT_DTE):
            action = _more_urgent(action, "URGENT REVIEW / EXIT CHECK")
            risks.append("Short front leg is very close to expiration; gamma and assignment risk are elevated.")
        elif front_dte <= int(config.CALENDAR_LIFECYCLE_REVIEW_DTE):
            action = _more_urgent(action, "RECHECK BEFORE CLOSE")
            risks.append("Short front leg is inside the review window.")
        else:
            reasons.append("Short front leg is not yet inside the urgent DTE window.")

    if underlying_price is not None and strike is not None:
        reasons.append(
            f"Underlying {underlying_price:.2f} vs short strike {strike:.2f}; "
            f"short-leg moneyness {short_moneyness_pct:+.1f}% from {underlying_price_source}."
        )
        assignment_risk_reasons.append(
            f"Underlying is {abs(distance_to_strike or 0):.2f} from short strike; "
            f"short leg is {'ITM' if short_itm else 'OTM' if short_itm is False else 'unknown'}."
        )
    else:
        risks.append("Underlying price unavailable; short-leg moneyness and assignment risk are lower confidence.")
        assignment_risk_reasons.append("Underlying price unavailable; moneyness could not be calculated.")

    if assignment_risk_level in {"High", "Elevated", "Moderate"}:
        risks.append(f"Assignment/pin risk level: {assignment_risk_level}.")
        assignment_risk_reasons.append(f"Assignment/pin risk is {assignment_risk_level} based on DTE and moneyness.")
    elif assignment_risk_level:
        assignment_risk_reasons.append(f"Assignment/pin risk is {assignment_risk_level} based on DTE and moneyness.")

    if short_extrinsic is not None and short_extrinsic < 0.10 and front_dte is not None and front_dte <= int(config.CALENDAR_LIFECYCLE_ASSIGNMENT_DTE):
        risks.append("Short leg has very little estimated extrinsic value while close to expiration; assignment risk may rise if it moves ITM.")

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

    itm_risk = _deep_itm_short_leg_risk(short_moneyness_pct, front_dte, days_until_earnings)
    if itm_risk["deep_itm_short_leg"]:
        action = _more_urgent(action, "URGENT REVIEW / EXIT CHECK")
        risks.append(str(itm_risk["urgent_short_leg_label"]))
        risks.append(str(itm_risk["thesis_warning"]))

    lifecycle_priority_score = _priority_score(action, pnl_pct, assignment_risk_level, front_dte, short_itm)
    decision_summary = _decision_summary(action, pnl_pct, short_itm, assignment_risk_level, front_dte)
    next_check = _next_check(action, front_dte, short_itm, earnings_known)

    output = {
        "strategy": "Calendar Lifecycle Check v1",
        "ticker": ticker,
        "underlying": ticker,
        "option_type": option_type,
        "strike": strike,
        "short_strike": strike,
        "quantity": quantity,
        "front_expiration": calendar.get("front_expiration"),
        "back_expiration": calendar.get("back_expiration"),
        "front_dte": front_dte,
        "back_dte": back_dte,
        "short_front_leg": calendar.get("short_front_leg") or {},
        "long_back_leg": calendar.get("long_back_leg") or {},
        "underlying_price": underlying_price,
        "underlying_price_source": underlying_price_source,
        "short_leg_moneyness_pct": short_moneyness_pct,
        "short_moneyness_pct": short_moneyness_pct,
        "distance_to_strike_pct": distance_to_strike_pct,
        "distance_to_short_strike_dollars": distance_to_strike,
        "distance_to_short_strike_pct": distance_to_strike_pct,
        "short_leg_itm": short_itm,
        "short_itm": short_itm,
        "short_leg_intrinsic_value": short_intrinsic,
        "short_leg_extrinsic_value": short_extrinsic,
        "short_leg_mid": short_leg_mid,
        "long_leg_mid": long_leg_mid,
        "net_delta_estimate": net_delta,
        "net_theta_estimate": net_theta,
        "net_iv_estimate": net_iv,
        "current_mid_debit": current_mid_debit,
        "current_value_estimate": current_value,
        "entry_debit_estimate": entry_debit_estimate,
        "cost_basis_estimate": cost_basis_estimate,
        "estimated_pnl_pct": pnl_pct,
        "pnl_per_spread_estimate": pnl_per_spread,
        "pnl_total_estimate": pnl_total,
        "current_value_per_spread": current_value_per_spread,
        "entry_value_per_spread": entry_value_per_spread,
        "entry_debit_source": entry_debit_source,
        "target_profit_pct": target_pct,
        "max_loss_pct": max_loss_pct,
        "target_debit": target_debit,
        "stop_debit": stop_debit,
        "pricing_quality": pricing_quality,
        "assignment_risk_level": assignment_risk_level,
        "assignment_risk_reasons": assignment_risk_reasons,
        "distance_to_strike": distance_to_strike,
        "lifecycle_priority_score": lifecycle_priority_score,
        "decision_summary": decision_summary,
        "short_leg_quote": calendar.get("short_leg_quote") or {},
        "long_leg_quote": calendar.get("long_leg_quote") or {},
        "earnings_date": earnings_date,
        "earnings_session": earnings_session,
        "days_until_earnings": days_until_earnings,
        "earnings_known": earnings_known,
        "action": action,
        "confidence": confidence,
        "deep_itm_short_leg": itm_risk["deep_itm_short_leg"],
        "emergency_short_leg_risk": itm_risk["emergency_short_leg_risk"],
        "urgent_short_leg_label": itm_risk["urgent_short_leg_label"],
        "thesis_warning": itm_risk["thesis_warning"],
        "reasons": reasons,
        "risks": risks,
        "next_check": next_check,
    }
    output.update(build_hold_through_score(output))
    output.update(classify_trade_type(output))
    return output



def _best_underlying_price(
    ticker: str,
    calendar: dict[str, Any],
    tradier_snapshot: dict[str, dict[str, Any]],
) -> tuple[float | None, str]:
    # Highest priority: price explicitly attached by analysis_service from
    # Robinhood stock positions. This is crucial in dev mode because Tradier
    # snapshot coverage may be limited to a small ticker subset.
    price = _float_or_none(calendar.get("underlying_price"))
    if price is not None and price > 0:
        return price, str(calendar.get("underlying_price_source") or "calendar")

    for leg_key in ("short_front_leg", "long_back_leg"):
        leg = calendar.get(leg_key) if isinstance(calendar.get(leg_key), dict) else {}
        price = _float_or_none(leg.get("underlying_price"))
        if price is not None and price > 0:
            return price, str(leg.get("underlying_price_source") or leg_key)

    price = _underlying_price_for(ticker, tradier_snapshot)
    if price is not None and price > 0:
        return price, "tradier_snapshot"

    return None, "unavailable"


def _short_leg_intrinsic_value(option_type: str, strike: float | None, underlying_price: float | None) -> float | None:
    if strike is None or underlying_price is None:
        return None
    if option_type == "put":
        return max(0.0, strike - underlying_price)
    return max(0.0, underlying_price - strike)


def _net_greek(calendar: dict[str, Any], greek: str) -> float | None:
    short_quote = calendar.get("short_leg_quote") if isinstance(calendar.get("short_leg_quote"), dict) else {}
    long_quote = calendar.get("long_leg_quote") if isinstance(calendar.get("long_leg_quote"), dict) else {}
    short_val = _float_or_none(short_quote.get(greek))
    long_val = _float_or_none(long_quote.get(greek))
    if short_val is None and long_val is None:
        return None
    # Long calendar net exposure = long back leg minus short front leg.
    return (long_val or 0.0) - (short_val or 0.0)


def _priority_score(
    action: str,
    pnl_pct: float | None,
    assignment_risk_level: str,
    front_dte: int | None,
    short_itm: bool | None,
) -> float:
    score = 65.0
    action_upper = str(action or "").upper()
    if "URGENT" in action_upper:
        score = 95.0
    elif "CUT" in action_upper or "EXIT" in action_upper:
        score = 90.0
    elif "TAKE PROFIT" in action_upper:
        score = 88.0
    elif "RECHECK" in action_upper or "EVENT" in action_upper:
        score = 78.0
    if assignment_risk_level == "High":
        score = max(score, 96.0)
    elif assignment_risk_level == "Elevated":
        score = max(score, 88.0)
    elif assignment_risk_level == "Moderate":
        score = max(score, 76.0)
    if short_itm is True:
        score = max(score, 94.0)
    if front_dte is not None and front_dte <= 1:
        score = max(score, 92.0)
    if pnl_pct is not None and pnl_pct <= float(getattr(config, "CALENDAR_LIFECYCLE_STOP_LOSS_PCT", -35)):
        score = max(score, 90.0)
    return round(min(score, 100.0), 1)


def _decision_summary(
    action: str,
    pnl_pct: float | None,
    short_itm: bool | None,
    assignment_risk_level: str,
    front_dte: int | None,
) -> str:
    parts = [str(action or "Review")]
    if pnl_pct is not None:
        parts.append(f"P/L {pnl_pct:+.1f}%")
    if front_dte is not None:
        parts.append(f"short leg {front_dte} DTE")
    if short_itm is True:
        parts.append("short leg ITM")
    elif short_itm is False:
        parts.append("short leg OTM")
    parts.append(f"assignment risk {assignment_risk_level}")
    return " | ".join(parts)



def _normalize_entry_debit_from_total_cost(cost_basis_estimate: float, quantity: float) -> float | None:
    if quantity <= 0:
        return None
    debit = abs(cost_basis_estimate) / (quantity * 100.0)
    # Protect against broker payloads that expose cents in the underlying cost
    # basis. A $1.72 spread can otherwise display as $172.00.
    if debit >= 25.0:
        debit = debit / 100.0
    return debit


def _assignment_risk_level(short_itm: bool | None, near_money: bool, front_dte: int | None) -> str:
    urgent_dte = int(getattr(config, "CALENDAR_LIFECYCLE_ASSIGNMENT_DTE", config.CALENDAR_LIFECYCLE_URGENT_DTE))
    if short_itm is True and front_dte is not None and front_dte <= urgent_dte:
        return "High"
    if short_itm is True:
        return "Elevated"
    if near_money and front_dte is not None and front_dte <= urgent_dte:
        return "Elevated"
    if near_money:
        return "Moderate"
    return "Low"

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


def _deep_itm_short_leg_risk(
    short_moneyness_pct: float | None,
    front_dte: int | None,
    days_until_earnings: int | None,
) -> dict[str, Any]:
    """Return display-only urgency fields for a deeply ITM short leg."""
    base = {
        "deep_itm_short_leg": False,
        "emergency_short_leg_risk": False,
        "urgent_short_leg_label": "",
        "thesis_warning": "",
    }
    if short_moneyness_pct is None or front_dte is None:
        return base
    if short_moneyness_pct <= 5.0 or front_dte > 3:
        return base

    base["deep_itm_short_leg"] = True
    base["urgent_short_leg_label"] = "SHORT LEG ITM - CLOSE / ROLL REVIEW"
    base["thesis_warning"] = "Original calendar thesis may be broken because underlying moved far beyond the short strike."
    if short_moneyness_pct > 10.0 and days_until_earnings is not None and 0 <= days_until_earnings <= 1:
        base["emergency_short_leg_risk"] = True
        base["urgent_short_leg_label"] = "SHORT LEG DEEP ITM - CLOSE / ROLL REVIEW REQUIRED"
    return base


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
