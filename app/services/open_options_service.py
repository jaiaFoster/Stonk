"""
app/services/open_options_service.py — Open options position detector.

Open Options Position Detector v1 is read-only. It uses Tradier account
positions to find option legs and detect simple long calendar spreads:

- same underlying
- same option type
- same strike
- short front expiration
- long later expiration

It does not place trades and it does not close trades. It gives the app enough
structure to start lifecycle checks in a later patch.
"""

from __future__ import annotations

import re
from datetime import datetime, date
from typing import Any, Callable

from app import config
from app.providers.tradier_provider import TradierAuthError, TradierProvider
from app.utils.log_safety import sanitize_for_log

LogFn = Callable[[str], None]

OCC_SYMBOL_RE = re.compile(r"^([A-Z0-9.]+?)(\d{6})([CP])(\d{8})$")


def detect_open_options_positions(log_print: LogFn | None = None) -> dict[str, Any]:
    """Fetch Tradier positions and detect open option calendars."""
    logger = log_print or (lambda msg: print(msg, flush=True))
    provider = TradierProvider()

    result: dict[str, Any] = {
        "source": "tradier",
        "has_data": False,
        "enabled": bool(config.OPEN_OPTIONS_DETECTOR_ENABLED),
        "configured": bool(provider.is_configured),
        "account_ids": [],
        "positions": [],
        "option_legs": [],
        "calendars": [],
        "errors": [],
        "summary": {},
    }

    if not config.OPEN_OPTIONS_DETECTOR_ENABLED:
        result["errors"].append("OPEN_OPTIONS_DETECTOR_ENABLED=false")
        logger("Open Options Position Detector v1 disabled by OPEN_OPTIONS_DETECTOR_ENABLED=false.")
        return _finalize_result(result)

    if not provider.is_configured:
        result["errors"].append("TRADIER_ACCESS_TOKEN is not set")
        logger("Open Options Position Detector v1 skipped: TRADIER_ACCESS_TOKEN is not set.")
        return _finalize_result(result)

    account_ids = _resolve_account_ids(provider, logger)
    result["account_ids"] = account_ids
    if not account_ids:
        result["errors"].append("No Tradier account ID available. Set TRADIER_ACCOUNT_ID or check token/profile access.")
        logger("Open Options Position Detector v1 skipped: no Tradier account ID available.")
        return _finalize_result(result)

    raw_positions: list[dict[str, Any]] = []
    option_legs: list[dict[str, Any]] = []

    for account_id in account_ids[: max(1, int(config.OPEN_OPTIONS_MAX_ACCOUNTS or 1))]:
        try:
            account_positions = provider.get_account_positions(account_id)
            logger(f"Tradier account {account_id}: fetched {len(account_positions)} open position(s).")
        except Exception as e:
            safe_error = sanitize_for_log(e, [config.TRADIER_ACCESS_TOKEN, config.RUN_TOKEN])
            logger(f"Tradier account {account_id}: positions unavailable: {safe_error}")
            result["errors"].append(f"{account_id}: {safe_error}")
            continue

        for raw in account_positions:
            normalized = _normalize_account_position(raw, account_id)
            raw_positions.append(normalized)
            leg = _position_to_option_leg(normalized)
            if leg:
                option_legs.append(leg)

    result["positions"] = raw_positions
    result["option_legs"] = option_legs

    if option_legs and bool(config.OPEN_OPTIONS_QUOTE_LEGS):
        _attach_leg_quotes(provider, option_legs, logger)

    calendars = _detect_calendar_spreads(option_legs)
    result["calendars"] = calendars
    result["has_data"] = bool(raw_positions or option_legs or calendars)

    logger(
        "Open Options Position Detector v1: "
        f"{len(raw_positions)} total position(s), {len(option_legs)} option leg(s), "
        f"{len(calendars)} calendar spread(s) detected."
    )

    return _finalize_result(result)


def _resolve_account_ids(provider: TradierProvider, logger: LogFn) -> list[str]:
    configured = str(config.TRADIER_ACCOUNT_ID or "").strip()
    if configured:
        return [part.strip() for part in configured.split(",") if part.strip()]

    try:
        account_ids = provider.get_account_ids()
        if account_ids:
            logger(f"Open Options Position Detector v1 discovered {len(account_ids)} Tradier account ID(s) from profile.")
        return account_ids
    except TradierAuthError as e:
        safe_error = sanitize_for_log(e, [config.TRADIER_ACCESS_TOKEN, config.RUN_TOKEN])
        logger(f"Open Options Position Detector profile access denied: {safe_error}")
        return []
    except Exception as e:
        safe_error = sanitize_for_log(e, [config.TRADIER_ACCESS_TOKEN, config.RUN_TOKEN])
        logger(f"Open Options Position Detector profile lookup failed: {safe_error}")
        return []


def _normalize_account_position(raw: dict[str, Any], account_id: str) -> dict[str, Any]:
    symbol = str(raw.get("symbol") or raw.get("option_symbol") or "").upper().strip()
    quantity = _float_or_none(raw.get("quantity"))
    cost_basis = _float_or_none(raw.get("cost_basis"))
    return {
        "account_id": account_id,
        "id": raw.get("id"),
        "symbol": symbol,
        "quantity": quantity,
        "cost_basis": cost_basis,
        "date_acquired": raw.get("date_acquired"),
        "raw": raw,
    }


def _position_to_option_leg(position: dict[str, Any]) -> dict[str, Any] | None:
    symbol = str(position.get("symbol") or "").upper().strip()
    parsed = parse_occ_option_symbol(symbol)
    if not parsed:
        return None

    quantity = _float_or_none(position.get("quantity"))
    if quantity is None or quantity == 0:
        return None

    side = "long" if quantity > 0 else "short"
    abs_quantity = abs(quantity)
    cost_basis = _float_or_none(position.get("cost_basis"))

    return {
        "account_id": position.get("account_id"),
        "symbol": symbol,
        "underlying": parsed["underlying"],
        "expiration": parsed["expiration"],
        "expiration_date": parsed["expiration"],
        "dte": _days_to_expiration(parsed["expiration"]),
        "option_type": parsed["option_type"],
        "strike": parsed["strike"],
        "quantity": quantity,
        "abs_quantity": abs_quantity,
        "side": side,
        "cost_basis": cost_basis,
        "avg_cost_per_contract": (cost_basis / abs_quantity) if cost_basis is not None and abs_quantity else None,
        "quote": {},
        "mid": None,
        "bid": None,
        "ask": None,
        "market_value_estimate": None,
    }


def parse_occ_option_symbol(symbol: str) -> dict[str, Any] | None:
    """Parse compact OCC option symbols like NVDA260527C00225000."""
    match = OCC_SYMBOL_RE.match(str(symbol or "").upper().strip())
    if not match:
        return None

    underlying, yymmdd, cp, strike_raw = match.groups()
    year = 2000 + int(yymmdd[:2])
    month = int(yymmdd[2:4])
    day = int(yymmdd[4:6])
    try:
        expiration = date(year, month, day).isoformat()
    except ValueError:
        return None

    strike = int(strike_raw) / 1000.0
    return {
        "underlying": underlying,
        "expiration": expiration,
        "option_type": "call" if cp == "C" else "put",
        "strike": strike,
    }


def _attach_leg_quotes(provider: TradierProvider, option_legs: list[dict[str, Any]], logger: LogFn) -> None:
    symbols = [leg["symbol"] for leg in option_legs if leg.get("symbol")]
    if not symbols:
        return

    limit = max(1, int(config.OPEN_OPTIONS_MAX_LEGS_TO_PRICE or 1))
    limited_symbols = symbols[:limit]
    if len(symbols) > limit:
        logger(f"Open Options Position Detector pricing limited to {limit}/{len(symbols)} option leg(s).")

    try:
        quotes = provider.get_quotes(limited_symbols, greeks=True)
    except Exception as e:
        safe_error = sanitize_for_log(e, [config.TRADIER_ACCESS_TOKEN, config.RUN_TOKEN])
        logger(f"Open Options Position Detector option-leg quote fetch failed: {safe_error}")
        return

    for leg in option_legs:
        quote = quotes.get(str(leg.get("symbol") or "").upper().strip()) or {}
        if not quote:
            continue
        bid = _float_or_none(quote.get("bid"))
        ask = _float_or_none(quote.get("ask"))
        last = _float_or_none(quote.get("last"))
        mid = _midpoint(bid, ask)
        if mid is None:
            mid = last
        qty = _float_or_none(leg.get("quantity")) or 0.0
        leg["quote"] = quote
        leg["bid"] = bid
        leg["ask"] = ask
        leg["last"] = last
        leg["mid"] = mid
        leg["market_value_estimate"] = (mid * qty * 100.0) if mid is not None else None


def _detect_calendar_spreads(option_legs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, float], list[dict[str, Any]]] = {}
    for leg in option_legs:
        key = (
            str(leg.get("underlying") or "").upper(),
            str(leg.get("option_type") or "").lower(),
            round(float(leg.get("strike") or 0.0), 4),
        )
        groups.setdefault(key, []).append(leg)

    calendars: list[dict[str, Any]] = []
    for (underlying, option_type, strike), legs in groups.items():
        longs = [leg for leg in legs if leg.get("side") == "long"]
        shorts = [leg for leg in legs if leg.get("side") == "short"]
        for short_leg in shorts:
            for long_leg in longs:
                short_exp = _parse_iso_date(short_leg.get("expiration"))
                long_exp = _parse_iso_date(long_leg.get("expiration"))
                if not short_exp or not long_exp or long_exp <= short_exp:
                    continue
                spread_qty = min(float(short_leg.get("abs_quantity") or 0), float(long_leg.get("abs_quantity") or 0))
                if spread_qty <= 0:
                    continue
                calendars.append(_build_calendar_summary(underlying, option_type, strike, short_leg, long_leg, spread_qty))

    calendars.sort(key=lambda item: (item.get("underlying") or "", item.get("strike") or 0, item.get("front_expiration") or ""))
    return calendars


def _build_calendar_summary(
    underlying: str,
    option_type: str,
    strike: float,
    short_leg: dict[str, Any],
    long_leg: dict[str, Any],
    spread_qty: float,
) -> dict[str, Any]:
    front_mid = _float_or_none(short_leg.get("mid"))
    back_mid = _float_or_none(long_leg.get("mid"))
    current_mid_debit = None
    if front_mid is not None and back_mid is not None:
        current_mid_debit = back_mid - front_mid

    current_value_estimate = current_mid_debit * spread_qty * 100.0 if current_mid_debit is not None else None

    short_cost = _float_or_none(short_leg.get("cost_basis"))
    long_cost = _float_or_none(long_leg.get("cost_basis"))
    cost_basis_estimate = None
    if short_cost is not None and long_cost is not None:
        # Tradier cost basis signs can vary by response context; this is displayed as an estimate only.
        cost_basis_estimate = long_cost + short_cost

    action = "MONITOR"
    risks: list[str] = []
    reasons: list[str] = []

    short_dte = short_leg.get("dte")
    if isinstance(short_dte, int) and short_dte <= 3:
        action = "CHECK EXIT / ASSIGNMENT RISK"
        risks.append("Short front leg is close to expiration; assignment and gamma risk are elevated.")
    elif isinstance(short_dte, int) and short_dte <= 7:
        action = "RECHECK BEFORE CLOSE"
        risks.append("Short front leg is inside one week to expiration.")
    else:
        reasons.append("Detected a valid long-calendar structure with a later-dated long leg.")

    if current_mid_debit is not None:
        reasons.append("Current estimated spread value is available from Tradier option quotes.")
    else:
        risks.append("Current spread value could not be estimated because one or both leg quotes were unavailable.")

    return {
        "strategy": "Long Calendar Spread",
        "underlying": underlying,
        "ticker": underlying,
        "option_type": option_type,
        "strike": strike,
        "quantity": spread_qty,
        "front_expiration": short_leg.get("expiration"),
        "back_expiration": long_leg.get("expiration"),
        "front_dte": short_leg.get("dte"),
        "back_dte": long_leg.get("dte"),
        "short_front_leg": short_leg,
        "long_back_leg": long_leg,
        "current_mid_debit": current_mid_debit,
        "current_value_estimate": current_value_estimate,
        "cost_basis_estimate": cost_basis_estimate,
        "action": action,
        "reasons": reasons,
        "risks": risks,
        "next_check": _next_check_for_calendar(short_leg),
    }


def _next_check_for_calendar(short_leg: dict[str, Any]) -> str:
    dte = short_leg.get("dte")
    if isinstance(dte, int) and dte <= 1:
        return "Check immediately before market close; short leg expires very soon."
    if isinstance(dte, int) and dte <= 7:
        return "Reprice the spread before market close and review short-leg moneyness."
    return "Monitor daily; add earnings timestamp and entry debit before automated exit scoring."


def _finalize_result(result: dict[str, Any]) -> dict[str, Any]:
    option_legs = result.get("option_legs") or []
    calendars = result.get("calendars") or []
    result["summary"] = {
        "account_count": len(result.get("account_ids") or []),
        "total_positions": len(result.get("positions") or []),
        "option_leg_count": len(option_legs),
        "calendar_count": len(calendars),
        "has_open_options": bool(option_legs),
        "has_open_calendars": bool(calendars),
    }
    return result


def _days_to_expiration(expiration: str) -> int | None:
    exp_date = _parse_iso_date(expiration)
    if not exp_date:
        return None
    return (exp_date - date.today()).days


def _parse_iso_date(value: Any) -> date | None:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except Exception:
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _midpoint(bid: float | None, ask: float | None) -> float | None:
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    return None
