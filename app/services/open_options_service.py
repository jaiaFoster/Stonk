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
from app.providers.robinhood_provider import get_open_option_positions as get_robinhood_open_option_positions
from app.utils.log_safety import sanitize_for_log

LogFn = Callable[[str], None]

OCC_SYMBOL_RE = re.compile(r"^([A-Z0-9.]+?)(\d{6})([CP])(\d{8})$")


def detect_open_options_positions(log_print: LogFn | None = None) -> dict[str, Any]:
    """Path A of the open options detection pipeline.
    Both paths must produce identical schemas. Any field added to one path
    must be added to the other. Long-term goal: merge into a single canonical
    pipeline post-FF stabilization.
    """
    logger = log_print or (lambda msg: print(msg, flush=True))
    provider = TradierProvider()

    result: dict[str, Any] = {
        "source": "combined_broker_options",
        "sources": [],
        "has_data": False,
        "enabled": bool(config.OPEN_OPTIONS_DETECTOR_ENABLED),
        "configured": bool(provider.is_configured or getattr(config, "ROBINHOOD_OPTIONS_DETECTOR_ENABLED", True)),
        "account_ids": [],
        "positions": [],
        "option_legs": [],
        "calendars": [],
        "errors": [],
        "summary": {},
    }

    if not config.OPEN_OPTIONS_DETECTOR_ENABLED:
        result["errors"].append("OPEN_OPTIONS_DETECTOR_ENABLED=false")
        logger("Open Options Position Detector v2 disabled by OPEN_OPTIONS_DETECTOR_ENABLED=false.")
        return _finalize_result(result)

    raw_positions: list[dict[str, Any]] = []
    option_legs: list[dict[str, Any]] = []
    account_ids: list[str] = []

    # --- Tradier positions ---
    if provider.is_configured:
        result["sources"].append({"source": "tradier", "configured": True})
        tradier_account_ids = _resolve_account_ids(provider, logger)
        account_ids.extend([f"tradier:{acct}" for acct in tradier_account_ids])
        if not tradier_account_ids:
            result["errors"].append("Tradier: no account ID available. Set TRADIER_ACCOUNT_ID or check token/profile access.")
        for account_id in tradier_account_ids[: max(1, int(config.OPEN_OPTIONS_MAX_ACCOUNTS or 1))]:
            try:
                account_positions = provider.get_account_positions(account_id)
                logger(f"Tradier account {account_id}: fetched {len(account_positions)} open position(s).")
            except Exception as e:
                safe_error = sanitize_for_log(e, [config.TRADIER_ACCESS_TOKEN, config.RUN_TOKEN])
                logger(f"Tradier account {account_id}: positions unavailable: {safe_error}")
                result["errors"].append(f"Tradier {account_id}: {safe_error}")
                continue

            for raw in account_positions:
                normalized = _normalize_account_position(raw, account_id)
                normalized["source"] = "tradier"
                normalized["broker"] = "tradier"
                raw_positions.append(normalized)
                leg = _position_to_option_leg(normalized)
                if leg:
                    leg["source"] = "tradier"
                    leg["broker"] = "tradier"
                    option_legs.append(leg)
    else:
        result["sources"].append({"source": "tradier", "configured": False})
        result["errors"].append("Tradier: TRADIER_ACCESS_TOKEN is not set")
        logger("Open Options Position Detector v2: Tradier skipped because TRADIER_ACCESS_TOKEN is not set.")

    # --- Robinhood positions ---
    if bool(getattr(config, "ROBINHOOD_OPTIONS_DETECTOR_ENABLED", True)):
        rh_account_numbers = _configured_robinhood_option_accounts()
        try:
            rh_payload = get_robinhood_open_option_positions(
                account_numbers=rh_account_numbers,
                max_positions=getattr(config, "ROBINHOOD_OPTIONS_MAX_POSITIONS", None),
            )
            result["sources"].append(
                {
                    "source": "robinhood",
                    "configured": bool(rh_payload.get("configured")),
                    "account_count": len(rh_payload.get("accounts") or []),
                    "position_count": len(rh_payload.get("positions") or []),
                    "provider_status": rh_payload.get("provider_status") or {},
                }
            )
            if rh_payload.get("provider_status"):
                result.setdefault("provider_status", {})["robinhood"] = rh_payload.get("provider_status")
            for err in rh_payload.get("errors", []) or []:
                result["errors"].append(f"Robinhood: {err}")
            for acct in rh_payload.get("accounts", []) or []:
                acct_num = acct.get("account_number")
                if acct_num:
                    account_ids.append(f"robinhood:{acct_num}")
            for pos in rh_payload.get("positions", []) or []:
                if not isinstance(pos, dict):
                    continue
                raw_positions.append(pos)
                leg = _robinhood_position_to_option_leg(pos)
                if leg:
                    option_legs.append(leg)
            logger(
                "Robinhood Open Options Detector v1: "
                f"{len(rh_payload.get('positions') or [])} open option position(s) normalized."
            )
        except Exception as e:
            safe_error = sanitize_for_log(e, [config.ROBINHOOD_PASSWORD, config.RUN_TOKEN])
            result["errors"].append(f"Robinhood options unavailable: {safe_error}")
            logger(f"Robinhood Open Options Detector v1 failed: {safe_error}")
    else:
        result["sources"].append({"source": "robinhood", "configured": False, "disabled": True})
        logger("Robinhood Open Options Detector v1 disabled by ROBINHOOD_OPTIONS_DETECTOR_ENABLED=false.")

    result["account_ids"] = account_ids
    result["positions"] = raw_positions
    result["option_legs"] = option_legs

    logger(f"[open_options] Path A: {len(option_legs)} legs normalized | sides: {[(l.get('underlying'), l.get('side'), l.get('strike'), l.get('option_type')) for l in option_legs]}")

    # Price all detected option legs through Tradier quotes when available. This
    # works for Robinhood legs too because they are normalized into OCC symbols.
    if option_legs and bool(config.OPEN_OPTIONS_QUOTE_LEGS) and provider.is_configured:
        _attach_leg_quotes(provider, option_legs, logger)

    logger(f"[open_options] Path A: quote attach complete | mids: {[(l.get('underlying'), l.get('strike'), l.get('mid')) for l in option_legs]}")

    calendars = _detect_calendar_spreads(option_legs)
    if calendars and provider.is_configured and bool(getattr(config, "CALENDAR_LIFECYCLE_FETCH_UNDERLYING_QUOTES", True)):
        enriched = _attach_underlying_quotes(provider, calendars, logger)
        logger(f"Calendar Lifecycle: underlying quote enriched for {enriched}/{len(calendars)} active calendar(s).")
    result["calendars"] = calendars

    verticals = _detect_vertical_spreads(option_legs)
    result["verticals"] = verticals
    for v in verticals:
        _cv = v.get("current_value")
        _pom = v.get("pct_of_max_profit")
        _upnl = v.get("unrealized_pnl")
        _sig = v.get("exit_signal", "HOLD")
        _tk = v.get("ticker", "?")
        _ls = v.get("long_strike")
        _ss = v.get("short_strike")
        _ot = str(v.get("option_type", "?"))[0].upper()
        _exp = str(v.get("expiration", ""))
        cv_s = f"{_cv:.2f}" if _cv is not None else "null"
        pom_s = f"{_pom:.1f}" if _pom is not None else "null"
        upnl_s = f"${_upnl:.0f}" if _upnl is not None else "null"
        logger(f"[open_options] {_tk} ${_ls}/{_ss}{_ot} {_exp}: current_value={cv_s} pct_of_max={pom_s}% unrealized_pnl={upnl_s} signal={_sig}")

    single_legs = _collect_unmatched_legs(option_legs, calendars, verticals)
    result["single_legs"] = single_legs

    logger(f"[open_options] Path A: detection complete | verticals={len(verticals)} calendars={len(calendars)} singles={len(single_legs)}")

    result["has_data"] = bool(raw_positions or option_legs or calendars or verticals or single_legs)

    logger(
        "Open Options Position Detector v2: "
        f"{len(raw_positions)} total position(s), {len(option_legs)} option leg(s), "
        f"{len(calendars)} calendar spread(s), {len(verticals)} vertical spread(s), "
        f"{len(single_legs)} unmatched single leg(s) detected."
    )

    return _finalize_result(result)


def detect_from_robinhood_raw_positions(
    raw_positions: list[dict[str, Any]],
    log_print: LogFn | None = None,
    provider=None,
) -> dict[str, Any]:
    """
    Path B of the open options detection pipeline.
    Both paths must produce identical schemas. Any field added to one path
    must be added to the other. Long-term goal: merge into a single canonical
    pipeline post-FF stabilization.
    """
    from app.providers.robinhood_provider import _normalize_option_position  # type: ignore[attr-defined]

    logger = log_print or (lambda msg: print(msg, flush=True))

    result: dict[str, Any] = {
        "source": "robinhood_session_reuse",
        "sources": [{"source": "robinhood", "configured": True}],
        "has_data": False,
        "enabled": True,
        "configured": True,
        "account_ids": [],
        "positions": [],
        "option_legs": [],
        "calendars": [],
        "errors": [],
        "summary": {},
    }

    if not raw_positions:
        return _finalize_result(result)

    seen_positions: set[str] = set()
    option_legs: list[dict[str, Any]] = []
    raw_normalized: list[dict[str, Any]] = []

    for raw in raw_positions:
        if not isinstance(raw, dict):
            continue
        try:
            dedupe_key = str(raw.get("id") or raw.get("url") or raw.get("option") or id(raw))
            if dedupe_key in seen_positions:
                continue
            seen_positions.add(dedupe_key)

            normalized = _normalize_option_position(raw, None, "robinhood_default")
            if not normalized:
                continue
            raw_normalized.append(normalized)
            leg = _robinhood_position_to_option_leg(normalized)
            if leg:
                option_legs.append(leg)
        except Exception as exc:
            result["errors"].append(f"normalize_failed: {exc}")

    result["positions"] = raw_normalized
    result["option_legs"] = option_legs

    logger(f"[open_options] Path B: {len(option_legs)} legs normalized | sides: {[(l.get('underlying'), l.get('side')) for l in option_legs]}")

    if option_legs:
        try:
            _prov = provider or TradierProvider()
            if _prov.is_configured and bool(config.OPEN_OPTIONS_QUOTE_LEGS):
                _attach_leg_quotes(_prov, option_legs, logger)
                logger(f"[open_options] Path B: quote attach applied to {len(option_legs)} legs")
            else:
                logger(f"[open_options] Path B: quote attach skipped (provider not configured or OPEN_OPTIONS_QUOTE_LEGS=False)")
        except Exception as e:
            logger(f"[open_options] Path B: quote attach failed: {e}")

    calendars = _detect_calendar_spreads(option_legs)
    verticals = _detect_vertical_spreads(option_legs)
    single_legs = _collect_unmatched_legs(option_legs, calendars, verticals)
    result["calendars"] = calendars
    result["verticals"] = verticals
    result["single_legs"] = single_legs
    result["has_data"] = bool(raw_normalized or option_legs or calendars or verticals or single_legs)

    logger(f"[open_options] Path B: detection complete | verticals={len(verticals)} calendars={len(calendars)} singles={len(single_legs)}")

    logger(
        f"detect_from_robinhood_raw_positions: "
        f"{len(raw_normalized)} normalized, {len(option_legs)} legs, "
        f"{len(calendars)} calendar(s), {len(verticals)} vertical(s)."
    )
    return _finalize_result(result)


def _configured_robinhood_option_accounts() -> list[str] | None:
    raw = str(getattr(config, "ROBINHOOD_OPTIONS_ACCOUNT_NUMBERS", "") or "").strip()
    if not raw:
        return None
    return [part.strip() for part in raw.split(",") if part.strip()]


def _robinhood_position_to_option_leg(position: dict[str, Any]) -> dict[str, Any] | None:
    underlying = str(position.get("underlying") or "").upper().strip()
    expiration = str(position.get("expiration") or position.get("expiration_date") or "").strip()[:10]
    option_type = str(position.get("option_type") or "").lower().strip()
    strike = _float_or_none(position.get("strike"))
    quantity = _float_or_none(position.get("quantity"))
    if not underlying or not expiration or option_type not in {"call", "put"} or strike is None:
        return None
    if quantity is None or quantity == 0:
        return None

    symbol = str(position.get("symbol") or "").upper().strip()
    if not symbol:
        symbol = _occ_symbol(underlying, expiration, option_type, strike)

    abs_quantity = abs(quantity)
    side = str(position.get("side") or "unknown").lower().strip()
    if side not in {"long", "short", "unknown"}:
        side = "unknown"

    return {
        "source": "robinhood",
        "broker": "robinhood",
        "account_id": position.get("account_id"),
        "account_label": position.get("account_label"),
        "symbol": symbol,
        "underlying": underlying,
        "expiration": expiration,
        "expiration_date": expiration,
        "dte": _days_to_expiration(expiration),
        "option_type": option_type,
        "strike": strike,
        "quantity": quantity,
        "abs_quantity": abs_quantity,
        "side": side,
        "side_is_explicit": bool(position.get("side_is_explicit")),
        "side_inferred": False,
        "cost_basis": _float_or_none(position.get("cost_basis")),
        "avg_cost_per_contract": _float_or_none(position.get("avg_cost_per_contract")),
        "avg_cost_per_share": _float_or_none(position.get("avg_cost_per_share") or position.get("avg_cost_per_contract")),
        "avg_price_raw": _float_or_none(position.get("avg_price_raw")),
        "avg_price_scale": position.get("avg_price_scale"),
        "quote": {},
        "mid": None,
        "bid": None,
        "ask": None,
        "market_value_estimate": None,
        "raw": position.get("raw") or position,
    }


def _occ_symbol(underlying: str, expiration: str, option_type: str, strike: float) -> str:
    try:
        yymmdd = str(expiration).replace("-", "")[2:]
        cp = "C" if str(option_type).lower() == "call" else "P"
        strike_int = int(round(float(strike) * 1000))
        return f"{str(underlying).upper()}{yymmdd}{cp}{strike_int:08d}"
    except Exception:
        return ""


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
        "avg_cost_per_share": ((cost_basis / abs_quantity) / 100.0) if cost_basis is not None and abs_quantity else None,
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


def _attach_underlying_quotes(provider: TradierProvider, calendars: list[dict[str, Any]], logger: LogFn) -> int:
    tickers = sorted({
        str(cal.get("ticker") or cal.get("underlying") or "").upper().strip()
        for cal in calendars
        if isinstance(cal, dict) and str(cal.get("ticker") or cal.get("underlying") or "").strip()
    })
    if not tickers:
        return 0
    try:
        quotes = provider.get_quotes(tickers, greeks=False)
    except Exception as e:
        safe_error = sanitize_for_log(e, [config.TRADIER_ACCESS_TOKEN, config.RUN_TOKEN])
        logger(f"Calendar Lifecycle underlying quote enrichment failed: {safe_error}")
        return 0

    enriched = 0
    for cal in calendars:
        ticker = str(cal.get("ticker") or cal.get("underlying") or "").upper().strip()
        quote = quotes.get(ticker) or {}
        price, key = _best_equity_quote_price(quote)
        if price is None:
            continue
        cal["underlying_price"] = price
        cal["underlying_price_source"] = f"tradier_underlying_quote.{key}"
        enriched += 1
        for leg_key in ("short_front_leg", "long_back_leg"):
            leg = cal.get(leg_key)
            if isinstance(leg, dict):
                leg["underlying_price"] = price
                leg["underlying_price_source"] = f"tradier_underlying_quote.{key}"
    return enriched


def _best_equity_quote_price(quote: dict[str, Any]) -> tuple[float | None, str]:
    for key in ("last", "mark", "bid", "ask", "close", "prevclose"):
        price = _float_or_none(quote.get(key))
        if price is not None and price > 0:
            return price, key
    return None, "unavailable"


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

        # First use explicit long/short side data when the broker provides it.
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

        # Robinhood option-position rows may omit whether the leg is long or
        # short. If all legs in a same ticker/type/strike group are unknown but
        # there are multiple expirations, infer the common calendar structure:
        # front expiration short, back expiration long. This is read-only and
        # flagged as inferred in the row so the UI can disclose lower certainty.
        if not shorts and bool(getattr(config, "ROBINHOOD_OPTIONS_INFER_CALENDARS", True)):
            unknown_or_rh = [
                leg for leg in legs
                if leg.get("source") == "robinhood" and leg.get("side") in {"unknown", "long", None, ""}
            ]
            if len(unknown_or_rh) >= 2:
                ordered = sorted(
                    [leg for leg in unknown_or_rh if _parse_iso_date(leg.get("expiration"))],
                    key=lambda leg: _parse_iso_date(leg.get("expiration")),
                )
                if len(ordered) >= 2:
                    front = dict(ordered[0])
                    back = dict(ordered[-1])
                    front_exp = _parse_iso_date(front.get("expiration"))
                    back_exp = _parse_iso_date(back.get("expiration"))
                    if front_exp and back_exp and back_exp > front_exp:
                        spread_qty = min(float(front.get("abs_quantity") or 0), float(back.get("abs_quantity") or 0))
                        if spread_qty > 0:
                            front["side"] = "short"
                            front["side_inferred"] = True
                            back["side"] = "long"
                            back["side_inferred"] = True
                            calendar = _build_calendar_summary(underlying, option_type, strike, front, back, spread_qty)
                            calendar["side_inferred"] = True
                            calendar.setdefault("risks", []).append(
                                "Robinhood did not expose explicit long/short side for one or more legs; front-short/back-long calendar structure was inferred from expirations."
                            )
                            calendars.append(calendar)

    # De-dupe in case explicit and inferred paths produce the same calendar.
    deduped: dict[tuple[str, str, float, str, str], dict[str, Any]] = {}
    for item in calendars:
        key = (
            str(item.get("underlying") or ""),
            str(item.get("option_type") or ""),
            round(float(item.get("strike") or 0.0), 4),
            str(item.get("front_expiration") or ""),
            str(item.get("back_expiration") or ""),
        )
        existing = deduped.get(key)
        if not existing or (existing.get("side_inferred") and not item.get("side_inferred")):
            deduped[key] = item

    result = list(deduped.values())
    result.sort(key=lambda item: (item.get("underlying") or "", item.get("strike") or 0, item.get("front_expiration") or ""))
    return result

def _detect_vertical_spreads(option_legs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Detect vertical spreads: same ticker, same option_type, same expiration, different strikes.
    Returns list of vertical spread summaries.
    """
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for leg in option_legs:
        key = (
            str(leg.get("underlying") or "").upper(),
            str(leg.get("option_type") or "").lower(),
            str(leg.get("expiration") or ""),
        )
        if key[0] and key[1] in {"call", "put"} and key[2]:
            groups.setdefault(key, []).append(leg)

    verticals: list[dict[str, Any]] = []
    for (underlying, option_type, expiration), legs in groups.items():
        if len(legs) < 2:
            continue
        longs = [leg for leg in legs if leg.get("side") == "long"]
        shorts = [leg for leg in legs if leg.get("side") == "short"]
        if not longs or not shorts:
            continue
        for long_leg in longs:
            for short_leg in shorts:
                long_strike = _float_or_none(long_leg.get("strike"))
                short_strike = _float_or_none(short_leg.get("strike"))
                if long_strike is None or short_strike is None or long_strike == short_strike:
                    continue
                qty = min(float(long_leg.get("abs_quantity") or 0), float(short_leg.get("abs_quantity") or 0))
                if qty <= 0:
                    continue
                width = abs(long_strike - short_strike)
                long_entry = _entry_price_per_share(long_leg)
                short_entry = _entry_price_per_share(short_leg)
                net_debit = None
                if long_entry is not None and short_entry is not None:
                    net_debit = long_entry - short_entry
                long_mid = _float_or_none(long_leg.get("mid"))
                short_mid = _float_or_none(short_leg.get("mid"))
                current_value = None
                if long_mid is not None and short_mid is not None:
                    current_value = long_mid - short_mid
                max_profit = (width - net_debit) * 100.0 if net_debit is not None else None
                max_loss = net_debit * 100.0 if net_debit is not None else None
                pct_of_max_profit = None
                if current_value is not None and net_debit is not None and width > 0:
                    pct_of_max_profit = round(current_value / width * 100.0, 2)
                unrealized_pnl = None
                unrealized_pnl_pct = None
                if current_value is not None and net_debit is not None:
                    unrealized_pnl = round((current_value - net_debit) * 100.0 * qty, 2)
                    if net_debit != 0:
                        unrealized_pnl_pct = round((current_value - net_debit) / abs(net_debit) * 100.0, 2)
                dte = long_leg.get("dte")
                exit_signal = "HOLD"
                if isinstance(dte, (int, float)) and dte <= getattr(config, "SKEW_EXIT_DTE_THRESHOLD", 3):
                    exit_signal = "EXIT_EXPIRY"
                elif pct_of_max_profit is not None and pct_of_max_profit >= getattr(config, "SKEW_PROFIT_TARGET_PCT", 50):
                    exit_signal = "EXIT_TARGET"
                elif unrealized_pnl_pct is not None and unrealized_pnl_pct <= -getattr(config, "SKEW_STOP_LOSS_PCT", 50):
                    exit_signal = "EXIT_STOP"
                verticals.append({
                    "strategy": "Debit Vertical Spread",
                    "strategy_type": "skew_vertical",
                    "underlying": underlying,
                    "ticker": underlying,
                    "option_type": option_type,
                    "expiration": expiration,
                    "dte": dte,
                    "quantity": qty,
                    "long_strike": long_strike,
                    "short_strike": short_strike,
                    "width": width,
                    "long_leg": long_leg,
                    "short_leg": short_leg,
                    "legs": [
                        {
                            "strike": long_strike,
                            "expiration": expiration,
                            "dte": dte,
                            "position": "long",
                            "position_type": "long",
                            "side": "long",
                            "option_type": option_type,
                            "quantity": long_leg.get("abs_quantity"),
                            "average_price": long_leg.get("avg_cost_per_share"),
                            "current_price": long_leg.get("mid"),
                        },
                        {
                            "strike": short_strike,
                            "expiration": expiration,
                            "dte": dte,
                            "position": "short",
                            "position_type": "short",
                            "side": "short",
                            "option_type": option_type,
                            "quantity": short_leg.get("abs_quantity"),
                            "average_price": short_leg.get("avg_cost_per_share"),
                            "current_price": short_leg.get("mid"),
                        },
                    ],
                    "net_debit": net_debit,
                    "current_value": current_value,
                    "max_profit": max_profit,
                    "max_loss": max_loss,
                    "pct_of_max_profit": pct_of_max_profit,
                    "unrealized_pnl": unrealized_pnl,
                    "unrealized_pnl_pct": unrealized_pnl_pct,
                    "exit_signal": exit_signal,
                    "source": str(long_leg.get("broker") or long_leg.get("source") or "robinhood"),
                    "broker": str(long_leg.get("broker") or long_leg.get("source") or "robinhood"),
                })

    seen: set[tuple[str, str, float, float, str]] = set()
    deduped: list[dict[str, Any]] = []
    for v in verticals:
        key = (
            str(v.get("ticker") or ""),
            str(v.get("option_type") or ""),
            float(v.get("long_strike") or 0),
            float(v.get("short_strike") or 0),
            str(v.get("expiration") or ""),
        )
        if key not in seen:
            seen.add(key)
            deduped.append(v)
    return deduped


def _collect_unmatched_legs(
    option_legs: list[dict[str, Any]],
    calendars: list[dict[str, Any]],
    verticals: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    _ALL_SIDES = ("long", "short", "unknown")
    matched: set[tuple[str, str, float, str]] = set()
    for cal in calendars:
        underlying = str(cal.get("underlying") or "").upper()
        otype = str(cal.get("option_type") or "").lower()
        strike = round(float(cal.get("strike") or 0), 4)
        for exp_key in ("front_expiration", "back_expiration"):
            exp = str(cal.get(exp_key) or "")
            if exp:
                matched.add((underlying, otype, strike, exp))
    for vert in verticals:
        ticker = str(vert.get("ticker") or "").upper()
        otype = str(vert.get("option_type") or "").lower()
        exp = str(vert.get("expiration") or "")
        for strike_key in ("long_strike", "short_strike"):
            strike = round(float(vert.get(strike_key) or 0), 4)
            matched.add((ticker, otype, strike, exp))
    single: list[dict[str, Any]] = []
    for leg in option_legs:
        underlying = str(leg.get("underlying") or "").upper()
        otype = str(leg.get("option_type") or "").lower()
        strike = round(float(leg.get("strike") or 0), 4)
        exp = str(leg.get("expiration") or "")
        side = str(leg.get("side") or "unknown").lower()
        key = (underlying, otype, strike, exp)
        if key in matched:
            continue
        avg_price = _float_or_none(leg.get("avg_cost_per_share"))
        if avg_price is None:
            avg_price = _float_or_none(leg.get("avg_cost_per_contract"))
            if avg_price is not None and abs(avg_price) >= 25.0:
                avg_price = avg_price / 100.0
        cur_price = _float_or_none(leg.get("mid") or leg.get("mark") or leg.get("last"))
        qty = float(leg.get("abs_quantity") or leg.get("quantity") or 1)
        unrealized_pnl = None
        if avg_price is not None and cur_price is not None:
            multiplier = 1 if side == "long" else -1
            unrealized_pnl = round((cur_price - avg_price) * qty * 100 * multiplier, 2)
        single.append({
            "strategy_type": "single_leg",
            "ticker": underlying,
            "option_type": otype,
            "position": side,
            "strike": float(leg.get("strike") or 0),
            "expiration": exp,
            "dte": leg.get("dte"),
            "quantity": qty,
            "average_price": avg_price,
            "current_price": cur_price,
            "unrealized_pnl": unrealized_pnl,
            "broker": str(leg.get("broker") or leg.get("source") or "unknown"),
        })
    seen_keys: set[tuple] = set()
    deduped: list[dict[str, Any]] = []
    for s in single:
        dk = (
            str(s.get("ticker") or "").upper(),
            str(s.get("option_type") or "").lower(),
            float(s.get("strike") or 0),
            str(s.get("expiration") or ""),
            str(s.get("position") or ""),
        )
        if dk not in seen_keys:
            seen_keys.add(dk)
            deduped.append(s)
    return deduped


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

    short_entry = _entry_price_per_share(short_leg)
    long_entry = _entry_price_per_share(long_leg)
    entry_mid_debit_estimate = None
    entry_value_estimate = None
    entry_source = "unavailable"
    entry_quality = "missing"
    if short_entry is not None and long_entry is not None:
        entry_mid_debit_estimate = long_entry - short_entry
        entry_value_estimate = entry_mid_debit_estimate * spread_qty * 100.0
        entry_source = "broker_leg_average_prices"
        entry_quality = _entry_quality(short_leg, long_leg)

    short_cost = _float_or_none(short_leg.get("cost_basis"))
    long_cost = _float_or_none(long_leg.get("cost_basis"))
    cost_basis_estimate = None
    if entry_value_estimate is not None:
        cost_basis_estimate = entry_value_estimate
    elif short_cost is not None and long_cost is not None:
        # Broker cost-basis signs can vary by source. This fallback is lower
        # confidence than leg-average prices and is displayed as an estimate.
        cost_basis_estimate = long_cost + short_cost
        if spread_qty > 0:
            entry_mid_debit_estimate = cost_basis_estimate / (spread_qty * 100.0)
            entry_value_estimate = cost_basis_estimate
            entry_source = "broker_total_cost_basis"
            entry_quality = "estimated_from_total_cost_basis"

    pnl_per_spread_estimate = None
    pnl_total_estimate = None
    pnl_pct_estimate = None
    if current_mid_debit is not None and entry_mid_debit_estimate is not None:
        pnl_per_spread_estimate = (current_mid_debit - entry_mid_debit_estimate) * 100.0
        pnl_total_estimate = pnl_per_spread_estimate * spread_qty
        if entry_mid_debit_estimate != 0:
            pnl_pct_estimate = ((current_mid_debit - entry_mid_debit_estimate) / abs(entry_mid_debit_estimate)) * 100.0

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
        reasons.append("Current estimated spread value is available from live option quotes.")
    else:
        risks.append("Current spread value could not be estimated because one or both leg quotes were unavailable.")

    if entry_mid_debit_estimate is not None:
        reasons.append(f"Entry debit estimate from {entry_source}: {entry_mid_debit_estimate:.2f}.")
    else:
        risks.append("Entry debit estimate unavailable from broker payload; P/L confidence is lower.")

    broker_sources = sorted({str(short_leg.get("broker") or short_leg.get("source") or "unknown"), str(long_leg.get("broker") or long_leg.get("source") or "unknown")})
    side_inferred = bool(short_leg.get("side_inferred") or long_leg.get("side_inferred"))
    if side_inferred:
        risks.append("Calendar leg direction was inferred because the broker payload did not clearly mark long/short side.")
    if broker_sources:
        reasons.append("Detected from " + ", ".join(broker_sources) + " option positions.")

    pricing_quality = _calendar_pricing_quality(short_leg, long_leg, current_mid_debit, entry_mid_debit_estimate, side_inferred)

    return {
        "strategy": "Long Calendar Spread",
        "source": ",".join(broker_sources),
        "broker": ",".join(broker_sources),
        "side_inferred": side_inferred,
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
        "entry_mid_debit_estimate": entry_mid_debit_estimate,
        "entry_value_estimate": entry_value_estimate,
        "entry_source": entry_source,
        "entry_quality": entry_quality,
        "cost_basis_estimate": cost_basis_estimate,
        "pnl_per_spread_estimate": pnl_per_spread_estimate,
        "pnl_total_estimate": pnl_total_estimate,
        "pnl_pct_estimate": pnl_pct_estimate,
        "pricing_quality": pricing_quality,
        "short_leg_quote": _compact_leg_quote(short_leg),
        "long_leg_quote": _compact_leg_quote(long_leg),
        "action": action,
        "reasons": reasons,
        "risks": risks,
        "next_check": _next_check_for_calendar(short_leg),
    }


def _entry_price_per_share(leg: dict[str, Any]) -> float | None:
    for key in ["avg_cost_per_share", "avg_cost_per_contract"]:
        val = _float_or_none(leg.get(key))
        if val is not None:
            # If a broker gave cents despite upstream normalization, protect the
            # lifecycle math from a 100x display error.
            if abs(val) >= 25.0:
                return val / 100.0
            return abs(val)
    cost = _float_or_none(leg.get("cost_basis"))
    qty = _float_or_none(leg.get("abs_quantity"))
    if cost is not None and qty and qty > 0:
        per_share = abs(cost) / (qty * 100.0)
        if abs(per_share) >= 25.0:
            return per_share / 100.0
        return per_share
    return None


def _entry_quality(short_leg: dict[str, Any], long_leg: dict[str, Any]) -> str:
    scales = {str(short_leg.get("avg_price_scale") or "unknown"), str(long_leg.get("avg_price_scale") or "unknown")}
    if any("forced" in scale for scale in scales):
        return "forced_scale"
    if any("auto_cents" in scale for scale in scales):
        return "auto_scaled_from_cents"
    if all(scale in {"auto_dollars", "unknown", "missing"} for scale in scales):
        return "broker_average_price"
    return ",".join(sorted(scales))


def _calendar_pricing_quality(
    short_leg: dict[str, Any],
    long_leg: dict[str, Any],
    current_mid_debit: float | None,
    entry_mid_debit_estimate: float | None,
    side_inferred: bool,
) -> dict[str, Any]:
    warnings: list[str] = []
    if current_mid_debit is None:
        warnings.append("missing_current_mid")
    if entry_mid_debit_estimate is None:
        warnings.append("missing_entry_debit")
    if side_inferred:
        warnings.append("leg_side_inferred")
    for label, leg in [("short", short_leg), ("long", long_leg)]:
        bid = _float_or_none(leg.get("bid"))
        ask = _float_or_none(leg.get("ask"))
        mid = _float_or_none(leg.get("mid"))
        if bid is None or ask is None or mid is None:
            warnings.append(f"{label}_quote_incomplete")
        elif mid > 0 and ((ask - bid) / mid) > 0.25:
            warnings.append(f"{label}_wide_spread")
    confidence = "high"
    if warnings:
        confidence = "medium" if len(warnings) <= 2 else "low"
    return {"confidence": confidence, "warnings": warnings}


def _compact_leg_quote(leg: dict[str, Any]) -> dict[str, Any]:
    quote = leg.get("quote") or {}
    greeks = quote.get("greeks") or quote.get("greek") or {}
    return {
        "symbol": leg.get("symbol"),
        "side": leg.get("side"),
        "expiration": leg.get("expiration"),
        "dte": leg.get("dte"),
        "bid": _float_or_none(leg.get("bid")),
        "ask": _float_or_none(leg.get("ask")),
        "mid": _float_or_none(leg.get("mid")),
        "last": _float_or_none(leg.get("last")),
        "delta": _float_or_none(greeks.get("delta") if isinstance(greeks, dict) else None),
        "theta": _float_or_none(greeks.get("theta") if isinstance(greeks, dict) else None),
        "iv": _float_or_none((greeks.get("mid_iv") or greeks.get("smv_vol") or greeks.get("iv")) if isinstance(greeks, dict) else None),
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
    verticals = result.get("verticals") or []
    single_legs = result.get("single_legs") or []
    brokers = sorted({str((leg or {}).get("broker") or (leg or {}).get("source") or "unknown") for leg in option_legs if isinstance(leg, dict)})
    inferred_calendar_count = sum(1 for cal in calendars if isinstance(cal, dict) and cal.get("side_inferred"))
    result.setdefault("verticals", verticals)
    result.setdefault("single_legs", single_legs)
    result["summary"] = {
        "account_count": len(result.get("account_ids") or []),
        "total_positions": len(result.get("positions") or []),
        "option_leg_count": len(option_legs),
        "calendar_count": len(calendars),
        "vertical_count": len(verticals),
        "single_leg_count": len(single_legs),
        "inferred_calendar_count": inferred_calendar_count,
        "brokers": brokers,
        "has_open_options": bool(option_legs),
        "has_open_calendars": bool(calendars),
        "has_open_verticals": bool(verticals),
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
