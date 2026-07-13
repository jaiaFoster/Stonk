"""
app/services/calendar_spread_service.py — Calendar spread candidate scanner.

Calendar Spread Screener v1 uses Tradier option chains to look for simple
near-ATM long call calendar candidates:

- sell a front-expiration call
- buy a later-expiration call
- same underlying, strike, and option type
- conservative debit estimated from long ask - short bid

This scanner is intentionally read-only. It does not inspect open positions and
it does not place trades. Open-spread detection and lifecycle exit logic are a
future module.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable

from app import config
from app.providers.tradier_provider import TradierProvider
from app.services.tradier_service import CRYPTO_TICKERS
from app.utils.log_safety import sanitize_for_log

LogFn = Callable[[str], None]
CalendarCandidates = list[dict[str, Any]]


# ── Calendar Stage Taxonomy ──────────────────────────────────────────────────

class CalendarStage:
    """Stable lifecycle stage constants for calendar spread candidates.

    Describes where a ticker sits in the calendar entry lifecycle so that
    rows persisted before, during, and after the entry window can be
    distinguished without changing any scoring or gate logic.
    """
    DISCOVERED = "DISCOVERED"
    PRE_WINDOW = "PRE_WINDOW"
    APPROACHING_WINDOW = "APPROACHING_WINDOW"
    ENTRY_WINDOW_OPEN = "ENTRY_WINDOW_OPEN"
    ENTRY_WINDOW_CLOSING = "ENTRY_WINDOW_CLOSING"
    ENTRY_WINDOW_CLOSED = "ENTRY_WINDOW_CLOSED"
    POST_EVENT = "POST_EVENT"
    DATA_INCOMPLETE = "DATA_INCOMPLETE"
    STRUCTURE_UNAVAILABLE = "STRUCTURE_UNAVAILABLE"


def scan_calendar_spreads_for_positions(
    positions: list[dict[str, Any]],
    log_print: LogFn | None = None,
    max_tickers: int | None = None,
    allowed_tickers: list[str] | None = None,
) -> CalendarCandidates:
    """Scan selected equity tickers for near-ATM call calendar spread candidates."""
    logger = log_print or (lambda msg: print(msg, flush=True))
    provider = TradierProvider()

    all_equity_tickers = _equity_tickers_from_positions(positions)
    selected = _select_tickers(
        all_equity_tickers,
        max_tickers=max_tickers if max_tickers is not None else config.CALENDAR_MAX_TICKERS_PER_RUN,
        allowed_tickers=allowed_tickers,
    )

    if not config.CALENDAR_SCANNER_ENABLED:
        logger("Calendar Spread Screener v1 disabled by CALENDAR_SCANNER_ENABLED=false.")
        return []

    if not provider.is_configured:
        logger("Calendar Spread Screener v1 skipped: TRADIER_ACCESS_TOKEN is not set.")
        return []

    if not selected:
        logger("Calendar Spread Screener v1 skipped: no eligible equity tickers selected.")
        return []

    logger(
        f"Scanning calendar spread candidates for {len(selected)} ticker(s); "
        f"option_type={config.CALENDAR_OPTION_TYPE}; max_tickers={max_tickers if max_tickers is not None else config.CALENDAR_MAX_TICKERS_PER_RUN}"
    )

    candidates: CalendarCandidates = []
    _usb_ticker_stats: list[dict] = []

    try:
        quotes = provider.get_quotes(selected, greeks=False)
    except Exception as e:
        safe_error = sanitize_for_log(e, [config.TRADIER_ACCESS_TOKEN, config.RUN_TOKEN])
        logger(f"Calendar Spread Screener quote fetch failed: {safe_error}")
        return []

    for ticker in selected:
        try:
            quote = quotes.get(ticker, {}) or {}
            underlying_price = _underlying_price(quote)
            if underlying_price is None or underlying_price <= 0:
                logger(f"Calendar {ticker}: skipped because underlying quote was unavailable.")
                continue

            expirations = provider.get_expirations(ticker)
            earnings_event = _event_for_ticker(positions, ticker)
            pair_diagnostics: dict[str, Any] = {}
            pairs = _select_expiration_pairs(expirations, earnings_event=earnings_event, diagnostics=pair_diagnostics)

            if getattr(config, "UNIVERSAL_STRUCTURE_BUILDER_ENABLED", False):
                from app.services.options_structure_builder import enumerate_expiration_pairs, PairStatus  # noqa: PLC0415
                from app.models.options_structure_spec import OptionsStructureSpec  # noqa: PLC0415
                _usb_spec = OptionsStructureSpec(
                    strategy_id="calendar_spread",
                    structure_type=f"{str(config.CALENDAR_OPTION_TYPE or 'call').lower().strip()}_calendar",
                    option_types=[str(config.CALENDAR_OPTION_TYPE or "call")],
                    front_dte_min=int(getattr(config, "CALENDAR_EARNINGS_FRONT_MIN_DTE", 7) or 7),
                    front_dte_max=int(getattr(config, "CALENDAR_EARNINGS_FRONT_MAX_DTE", 14) or 14),
                    min_expiration_gap_days=14,
                    max_expiration_gap_days=49,
                )
                _event_date_str: str | None = None
                if earnings_event:
                    _raw_ed = earnings_event.get("earnings_date") or earnings_event.get("date") or ""
                    _event_date_str = str(_raw_ed).strip() or None
                _usb_pair_records = enumerate_expiration_pairs(
                    expirations=expirations,
                    spec=_usb_spec,
                    event_date=_event_date_str,
                )
                _usb_valid_statuses = {PairStatus.VALID, PairStatus.VALID_BUT_LOW_DTE, PairStatus.VALID_BUT_WIDE_GAP}
                _usb_n_valid = sum(1 for r in _usb_pair_records if r.pair_status in _usb_valid_statuses)
                _usb_n_considered = len(_usb_pair_records)
                _usb_n_rejected = _usb_n_considered - _usb_n_valid
                _usb_missing = 1 if not expirations else 0
                logger(
                    f"UNIVERSAL_STRUCTURE_BUILDER ticker={ticker} "
                    f"pairs_considered={_usb_n_considered} pairs_valid={_usb_n_valid} "
                    f"pairs_rejected={_usb_n_rejected} strategy_id=calendar_spread"
                )
                _usb_ticker_stats.append({
                    "ticker": ticker,
                    "pairs_considered": _usb_n_considered,
                    "pairs_valid": _usb_n_valid,
                    "pairs_rejected": _usb_n_rejected,
                    "missing_chain": _usb_missing,
                })

            if not pairs:
                # Persist a low-DTE rejection row instead of silently discarding the ticker.
                # Only applies when there is an earnings event and the best available front
                # expiration is below the approved minimum DTE threshold.
                if earnings_event:
                    _front_min = int(getattr(config, "CALENDAR_EARNINGS_FRONT_MIN_DTE", 7) or 7)
                    _best_front_dte = pair_diagnostics.get("best_available_front_dte")
                    if _best_front_dte is not None and _best_front_dte < _front_min:
                        _rejection = _build_low_dte_rejection_row(
                            ticker=ticker,
                            underlying_price=underlying_price,
                            front_dte=_best_front_dte,
                            front_exp=pair_diagnostics.get("best_available_front_exp"),
                            earnings_event=earnings_event,
                            pair_diagnostics=pair_diagnostics,
                        )
                        candidates.append(_rejection)
                        logger(
                            f"Calendar {ticker}: persisting low-DTE rejection row "
                            f"(best_front_dte={_best_front_dte} < approved_min={_front_min}); "
                            f"daily_opportunity_eligible=False."
                        )

                    tried = pair_diagnostics.get("tried_front_expirations", [])
                    reason = pair_diagnostics.get("no_valid_pair_reason", "NO_VALID_EXPIRATION_PAIR")
                    near_miss = pair_diagnostics.get("near_miss_gap")
                    near_miss_detail = f" near_miss_gap={near_miss['gap_days']}d (need {near_miss['min_gap']}d, short by {near_miss['shortfall_days']}d)" if near_miss else ""
                    logger(
                        f"Calendar {ticker}: {reason}; "
                        f"earnings_date={pair_diagnostics.get('event_date')}; "
                        f"tried_front_expirations={tried}{near_miss_detail}"
                    )
                else:
                    logger(f"Calendar {ticker}: no front/back expiration pair matched scanner settings.")
                continue

            ticker_candidates: CalendarCandidates = []
            for front_exp, back_exp in pairs:
                front_chain = provider.get_option_chain(
                    ticker,
                    front_exp,
                    greeks=bool(config.TRADIER_INCLUDE_GREEKS),
                )
                back_chain = provider.get_option_chain(
                    ticker,
                    back_exp,
                    greeks=bool(config.TRADIER_INCLUDE_GREEKS),
                )
                candidate = _build_best_candidate(
                    ticker=ticker,
                    quote=quote,
                    underlying_price=underlying_price,
                    front_expiration=front_exp,
                    back_expiration=back_exp,
                    front_chain=front_chain,
                    back_chain=back_chain,
                    earnings_event=earnings_event,
                )
                if candidate:
                    ticker_candidates.append(candidate)

            ticker_candidates.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
            ticker_candidates = ticker_candidates[: max(1, int(config.CALENDAR_MAX_CANDIDATES_PER_TICKER or 1))]
            candidates.extend(ticker_candidates)
            logger(f"Calendar {ticker}: generated {len(ticker_candidates)} candidate(s).")

        except Exception as e:
            safe_error = sanitize_for_log(e, [config.TRADIER_ACCESS_TOKEN, config.RUN_TOKEN])
            logger(f"Calendar Spread Screener unavailable for {ticker}: {safe_error}")

    candidates.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
    if getattr(config, "UNIVERSAL_STRUCTURE_BUILDER_ENABLED", False) and _usb_ticker_stats:
        _usb_audit_considered = sum(s["pairs_considered"] for s in _usb_ticker_stats)
        _usb_audit_valid = sum(s["pairs_valid"] for s in _usb_ticker_stats)
        _usb_audit_rejected = sum(s["pairs_rejected"] for s in _usb_ticker_stats)
        _usb_audit_missing = sum(s["missing_chain"] for s in _usb_ticker_stats)
        logger(
            f"CALENDAR_DISCOVERY_AUDIT tickers_scanned={len(_usb_ticker_stats)} "
            f"pairs_considered_total={_usb_audit_considered} valid_pairs={_usb_audit_valid} "
            f"rejected_pairs={_usb_audit_rejected} missing_chain={_usb_audit_missing}"
        )
    logger(f"Calendar Spread Screener v1 generated {len(candidates)} candidate(s).")
    return candidates


def _build_best_candidate(
    ticker: str,
    quote: dict[str, Any],
    underlying_price: float,
    front_expiration: str,
    back_expiration: str,
    front_chain: list[dict[str, Any]],
    back_chain: list[dict[str, Any]],
    earnings_event: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    option_type = str(config.CALENDAR_OPTION_TYPE or "call").lower().strip()
    front_options = [
        opt for opt in front_chain
        if str(opt.get("option_type") or "").lower() == option_type and _positive_mid_or_bid_ask(opt)
    ]
    back_options = [
        opt for opt in back_chain
        if str(opt.get("option_type") or "").lower() == option_type and _positive_mid_or_bid_ask(opt)
    ]

    if not front_options or not back_options:
        return None

    back_by_strike = {_strike_key(opt.get("strike")): opt for opt in back_options if opt.get("strike") is not None}
    matched_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for front in front_options:
        key = _strike_key(front.get("strike"))
        back = back_by_strike.get(key)
        if back:
            matched_pairs.append((front, back))

    if not matched_pairs:
        return None

    # For v1, focus on the closest common ATM strike. Later versions can score
    # several strikes around ATM and choose based on skew, liquidity, and debit.
    front_leg, back_leg = min(
        matched_pairs,
        key=lambda pair: abs(float(pair[0].get("strike") or 0) - underlying_price),
    )

    return _score_candidate(
        ticker=ticker,
        quote=quote,
        underlying_price=underlying_price,
        front_expiration=front_expiration,
        back_expiration=back_expiration,
        front_leg=front_leg,
        back_leg=back_leg,
        earnings_event=earnings_event,
    )


def _score_candidate(
    ticker: str,
    quote: dict[str, Any],
    underlying_price: float,
    front_expiration: str,
    back_expiration: str,
    front_leg: dict[str, Any],
    back_leg: dict[str, Any],
    earnings_event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    today = date.today()
    front_dte = _days_to_expiration(front_expiration, today)
    back_dte = _days_to_expiration(back_expiration, today)
    strike = _float_or_none(front_leg.get("strike"))

    front_bid = _float_or_none(front_leg.get("bid"))
    front_ask = _float_or_none(front_leg.get("ask"))
    front_mid = _float_or_none(front_leg.get("mid")) or _midpoint(front_bid, front_ask)

    back_bid = _float_or_none(back_leg.get("bid"))
    back_ask = _float_or_none(back_leg.get("ask"))
    back_mid = _float_or_none(back_leg.get("mid")) or _midpoint(back_bid, back_ask)

    conservative_debit = None
    if back_ask is not None and front_bid is not None:
        conservative_debit = back_ask - front_bid

    mid_debit = None
    if back_mid is not None and front_mid is not None:
        mid_debit = back_mid - front_mid

    debit_for_scoring = conservative_debit if conservative_debit is not None else mid_debit
    debit_pct_underlying = None
    if debit_for_scoring is not None and underlying_price > 0:
        debit_pct_underlying = (debit_for_scoring / underlying_price) * 100.0

    front_spread_pct = _spread_pct(front_bid, front_ask, front_mid)
    back_spread_pct = _spread_pct(back_bid, back_ask, back_mid)
    max_leg_spread_pct = _max_not_none(front_spread_pct, back_spread_pct)

    front_volume = _int_or_zero(front_leg.get("volume"))
    back_volume = _int_or_zero(back_leg.get("volume"))
    front_oi = _int_or_zero(front_leg.get("open_interest"))
    back_oi = _int_or_zero(back_leg.get("open_interest"))
    min_leg_volume = min(front_volume, back_volume)
    min_leg_open_interest = min(front_oi, back_oi)

    front_iv = _float_or_none(front_leg.get("iv"))
    back_iv = _float_or_none(back_leg.get("iv"))
    iv_edge = None
    if front_iv is not None and back_iv is not None:
        iv_edge = front_iv - back_iv

    atm_distance_pct = None
    if strike is not None and underlying_price > 0:
        atm_distance_pct = abs(strike - underlying_price) / underlying_price * 100.0

    # Evolutionary calendar fields — stage taxonomy and entry-window metrics.
    _entry_min_dte = int(getattr(config, "CALENDAR_EARNINGS_FRONT_MIN_DTE", 7) or 7)
    _entry_max_dte = int(getattr(config, "CALENDAR_EARNINGS_FRONT_MAX_DTE", 14) or 14)
    days_until_earnings = _days_until_earnings_from_event(earnings_event)
    if front_dte is None:
        days_until_entry_window: int | None = None
    elif front_dte > _entry_max_dte:
        days_until_entry_window = front_dte - _entry_max_dte
    elif front_dte >= _entry_min_dte:
        days_until_entry_window = 0
    else:
        days_until_entry_window = front_dte - _entry_min_dte  # negative — past window
    calendar_stage = _determine_calendar_stage(
        front_dte=front_dte,
        back_dte=back_dte,
        days_until_earnings=days_until_earnings,
        min_dte=_entry_min_dte,
        max_dte=_entry_max_dte,
    )

    # TKT-012: tiered debit cap (sizing gate, does not affect signal score).
    tiered_cap_pct = _tiered_debit_cap_pct(underlying_price)
    tiered_debit_cap_result: dict[str, Any] | None = None
    if debit_for_scoring is not None and underlying_price > 0:
        debit_pct_raw = debit_for_scoring / underlying_price
        tier = (
            "tier_3" if underlying_price >= float(getattr(config, "CALENDAR_DEBIT_CAP_TIER_2_MAX_PRICE", 500.0) or 500.0)
            else "tier_2" if underlying_price >= float(getattr(config, "CALENDAR_DEBIT_CAP_TIER_1_MAX_PRICE", 100.0) or 100.0)
            else "tier_1"
        )
        tiered_debit_cap_result = {
            "underlying_price": underlying_price,
            "debit": round(debit_for_scoring, 2),
            "debit_pct_underlying": round(debit_pct_raw * 100, 2),
            "cap_pct": round(tiered_cap_pct * 100, 2),
            "passes": debit_pct_raw <= tiered_cap_pct,
            "tier": tier,
        }

    score, reasons, risks = _calendar_score(
        conservative_debit=conservative_debit,
        mid_debit=mid_debit,
        debit_pct_underlying=debit_pct_underlying,
        max_leg_spread_pct=max_leg_spread_pct,
        min_leg_volume=min_leg_volume,
        min_leg_open_interest=min_leg_open_interest,
        atm_distance_pct=atm_distance_pct,
        iv_edge=iv_edge,
        front_dte=front_dte,
        back_dte=back_dte,
    )
    action = _calendar_action(score)

    return {
        "ticker": ticker,
        "strategy": "Long Call Calendar",
        "action": action,
        "score": round(score, 1),
        "underlying_price": underlying_price,
        "quote": quote,
        "option_type": str(config.CALENDAR_OPTION_TYPE or "call").lower().strip(),
        "strike": strike,
        "front_expiration": front_expiration,
        "back_expiration": back_expiration,
        "front_dte": front_dte,
        "back_dte": back_dte,
        "days_between_expirations": None if front_dte is None or back_dte is None else back_dte - front_dte,
        "short_front_leg": _compact_leg(front_leg),
        "long_back_leg": _compact_leg(back_leg),
        "conservative_debit": conservative_debit,
        "mid_debit": mid_debit,
        "debit_pct_underlying": debit_pct_underlying,
        "debit_cap_tier_result": tiered_debit_cap_result,
        "front_iv": front_iv,
        "back_iv": back_iv,
        "iv_edge": iv_edge,
        "front_leg_spread_pct": front_spread_pct,
        "back_leg_spread_pct": back_spread_pct,
        "max_leg_spread_pct": max_leg_spread_pct,
        "min_leg_volume": min_leg_volume,
        "min_leg_open_interest": min_leg_open_interest,
        "atm_distance_pct": atm_distance_pct,
        "reasons": reasons,
        "risks": risks,
        "earnings_event": earnings_event or {},
        "earnings_timing": _earnings_timing_payload(earnings_event, front_expiration, back_expiration),
        "next_check": _next_check(action),
        # Evolutionary calendar evidence fields (stage taxonomy + entry-window context).
        "calendar_stage": calendar_stage,
        "days_until_earnings": days_until_earnings,
        "days_until_entry_window": days_until_entry_window,
        "approved_entry_window_min_dte": _entry_min_dte,
        "approved_entry_window_max_dte": _entry_max_dte,
        "distance_from_entry_window": (front_dte - _entry_min_dte) if front_dte is not None else None,
    }


def _tiered_debit_cap_pct(underlying_price: float) -> float:
    """Return the applicable debit cap as a fraction of underlying (TKT-012)."""
    t1_max = float(getattr(config, "CALENDAR_DEBIT_CAP_TIER_1_MAX_PRICE", 100.0) or 100.0)
    t2_max = float(getattr(config, "CALENDAR_DEBIT_CAP_TIER_2_MAX_PRICE", 500.0) or 500.0)
    if underlying_price < t1_max:
        return float(getattr(config, "CALENDAR_DEBIT_CAP_TIER_1_PCT", 0.08) or 0.08)
    if underlying_price < t2_max:
        return float(getattr(config, "CALENDAR_DEBIT_CAP_TIER_2_PCT", 0.10) or 0.10)
    return float(getattr(config, "CALENDAR_DEBIT_CAP_TIER_3_PCT", 0.12) or 0.12)


def _calendar_score(
    conservative_debit: float | None,
    mid_debit: float | None,
    debit_pct_underlying: float | None,
    max_leg_spread_pct: float | None,
    min_leg_volume: int,
    min_leg_open_interest: int,
    atm_distance_pct: float | None,
    iv_edge: float | None,
    front_dte: int | None,
    back_dte: int | None,
) -> tuple[float, list[str], list[str]]:
    score = 50.0
    reasons: list[str] = []
    risks: list[str] = []

    debit = conservative_debit if conservative_debit is not None else mid_debit
    if debit is None or debit <= 0:
        score -= 30
        risks.append("Estimated calendar debit is unavailable or non-positive; avoid until quotes normalize.")
    else:
        reasons.append("Valid positive estimated net debit from Tradier bid/ask data.")
        if debit_pct_underlying is not None and debit_pct_underlying <= float(config.CALENDAR_MAX_DEBIT_PCT_UNDERLYING):
            score += 8
            reasons.append("Debit is small relative to underlying price.")
        elif debit_pct_underlying is not None:
            score -= 8
            risks.append("Debit is high relative to underlying price.")

    if max_leg_spread_pct is not None and max_leg_spread_pct <= float(config.CALENDAR_MAX_LEG_SPREAD_PCT):
        score += 12
        reasons.append("Bid/ask spread is acceptable for both legs.")
    elif max_leg_spread_pct is not None:
        score -= 14
        risks.append("One or both option legs have a wide bid/ask spread.")
    else:
        score -= 8
        risks.append("Bid/ask spread could not be measured cleanly.")

    if min_leg_open_interest >= int(config.CALENDAR_MIN_OPEN_INTEREST):
        score += 12
        reasons.append("Both legs have acceptable open interest.")
    else:
        score -= 12
        risks.append("Open interest is weak on at least one leg.")

    if min_leg_volume >= int(config.CALENDAR_MIN_VOLUME):
        score += 8
        reasons.append("Both legs have at least minimal same-day volume.")
    else:
        score -= 8
        risks.append("Volume is weak on at least one leg.")

    if atm_distance_pct is not None and atm_distance_pct <= float(config.CALENDAR_MAX_ATM_DISTANCE_PCT):
        score += 8
        reasons.append("Strike is close to the current underlying price.")
    elif atm_distance_pct is not None:
        score -= 6
        risks.append("Selected common strike is not very close to ATM.")

    if iv_edge is not None and iv_edge >= 0:
        score += 7
        reasons.append("Front-leg IV is at least as high as back-leg IV, which can support a calendar setup.")
    elif iv_edge is not None:
        score -= 5
        risks.append("Back-leg IV is above front-leg IV; calendar IV setup is less favorable.")
    else:
        risks.append("IV relationship unavailable; do not rely on IV edge yet.")

    if front_dte is not None and back_dte is not None:
        gap = back_dte - front_dte
        if gap >= int(config.CALENDAR_MIN_EXPIRATION_GAP_DAYS):
            score += 5
            reasons.append("Front/back expiration spacing is wide enough for a true calendar structure.")
        else:
            score -= 10
            risks.append("Front/back expirations are too close together for the preferred structure.")

    score = max(0.0, min(100.0, score))
    return score, reasons, risks


def _calendar_action(score: float) -> str:
    if score >= 78:
        return "WATCH / STRONG CANDIDATE"
    if score >= 65:
        return "WATCH"
    if score >= 50:
        return "WEAK WATCH"
    return "AVOID"


def _next_check(action: str) -> str:
    if action.startswith("WATCH"):
        return "Recheck with earnings timestamp, full two-expiration scan, and live bid/ask before entry."
    if action == "WEAK WATCH":
        return "Keep on watchlist only; needs better liquidity/spread or earnings context."
    return "Avoid for now; liquidity, spread, debit, or structure does not pass v1 filters."


def _select_expiration_pairs(
    expirations: list[str],
    earnings_event: dict[str, Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> list[tuple[str, str]]:
    today = date.today()
    parsed: list[tuple[int, str]] = []
    for raw in expirations:
        dte = _days_to_expiration(str(raw), today)
        if dte is not None and dte >= 0:
            parsed.append((dte, str(raw)))
    parsed.sort(key=lambda item: item[0])

    # TKT-ADV-006: use the pre-selected pair from the quality precheck when
    # available — avoids re-running selection in the background thread where
    # expirations or timing may differ from when the precheck ran.
    if earnings_event:
        precheck_front = str(earnings_event.get("precheck_front_expiration") or "").strip()
        precheck_back = str(earnings_event.get("precheck_back_expiration") or "").strip()
        if precheck_front and precheck_back:
            available = {exp for _, exp in parsed}
            if precheck_front in available and precheck_back in available:
                if diagnostics is not None:
                    diagnostics["source"] = "quality_precheck"
                    diagnostics["precheck_front"] = precheck_front
                    diagnostics["precheck_back"] = precheck_back
                return [(precheck_front, precheck_back)]
            # Pair stale (expirations have passed since precheck ran) — fall through.
            if diagnostics is not None:
                diagnostics["precheck_pair_stale"] = True
                diagnostics["precheck_front"] = precheck_front
                diagnostics["precheck_back"] = precheck_back

    if bool(getattr(config, "CALENDAR_EARNINGS_EVENT_AWARE_EXPIRATIONS", True)) and earnings_event:
        event_pairs, _diag = _select_earnings_expiration_pairs(parsed, earnings_event, today)
        if diagnostics is not None:
            diagnostics.update(_diag)
        if event_pairs:
            return event_pairs

    return _select_generic_expiration_pairs(parsed)


def _select_earnings_expiration_pairs(
    parsed: list[tuple[int, str]],
    earnings_event: dict[str, Any],
    today: date,
) -> tuple[list[tuple[str, str]], dict[str, Any]]:
    """Return (pairs, diagnostics). TKT-028: step through all expirations before earnings."""
    event_date = _parse_date(earnings_event.get("earnings_date") or earnings_event.get("date"))
    if not event_date:
        return [], {"no_valid_pair_reason": "no_event_date"}

    session = str(earnings_event.get("session_label") or earnings_event.get("time_of_day") or earnings_event.get("hour") or "").lower()
    same_day_ok = "after" in session or "amc" in session
    event_dte = (event_date - today).days
    front_min = int(getattr(config, "CALENDAR_EARNINGS_FRONT_MIN_DTE", 1) or 1)
    front_max = int(getattr(config, "CALENDAR_EARNINGS_FRONT_MAX_DTE", 14) or 14)
    min_gap = int(config.CALENDAR_MIN_EXPIRATION_GAP_DAYS or 14)
    target_gap = int(config.CALENDAR_TARGET_EXPIRATION_GAP_DAYS or 30)
    back_min_after_event = int(getattr(config, "CALENDAR_EARNINGS_BACK_MIN_DTE_AFTER_EVENT", 14) or 14)
    back_max = int(getattr(config, "CALENDAR_EARNINGS_BACK_MAX_DTE", config.CALENDAR_BACK_MAX_DTE) or config.CALENDAR_BACK_MAX_DTE)

    # All expirations before earnings — candidates for front leg regardless of DTE cap.
    all_before_event: list[tuple[int, str]] = []
    for dte, exp in parsed:
        exp_date = _parse_date(exp)
        if not exp_date:
            continue
        if exp_date < event_date or (same_day_ok and exp_date == event_date):
            all_before_event.append((dte, exp))

    def _score_fronts(fronts: list[tuple[int, str]]) -> list[tuple[float, str, str]]:
        scored: list[tuple[float, str, str]] = []
        for front_dte, front_exp in fronts:
            for back_dte, back_exp in parsed:
                back_date = _parse_date(back_exp)
                if not back_date or back_date <= event_date:
                    continue
                gap = back_dte - front_dte
                if gap < min_gap or back_dte > back_max or back_dte < event_dte + back_min_after_event:
                    continue
                score = abs(gap - target_gap) + abs((event_dte - front_dte) - 1) * 0.35
                scored.append((score, front_exp, back_exp))
        return scored

    # First pass: respect configured DTE window.
    front_candidates = [(dte, exp) for dte, exp in all_before_event if front_min <= dte <= front_max]
    scored_pairs = _score_fronts(front_candidates)

    widened = False
    if not scored_pairs and all_before_event:
        # TKT-028: widen — try all expirations before event, drop the DTE max cap.
        wider_candidates = [(dte, exp) for dte, exp in all_before_event if dte >= front_min]
        scored_pairs = _score_fronts(wider_candidates)
        widened = True

    # Best available front expiration — used by caller to detect low-DTE rejection.
    if all_before_event:
        _best_front_pair = max(all_before_event, key=lambda x: x[0])
        _best_available_front_dte: int | None = _best_front_pair[0]
        _best_available_front_exp: str | None = _best_front_pair[1]
    else:
        _best_available_front_dte = None
        _best_available_front_exp = None

    diagnostics: dict[str, Any] = {
        "tried_front_expirations": [exp for _, exp in all_before_event],
        "front_candidates_in_dte_window": [exp for _, exp in front_candidates],
        "pairs_scored": len(scored_pairs),
        "search_widened": widened,
        "event_date": event_date.isoformat(),
        "event_dte": event_dte,
        "front_max_dte_config": front_max,
        "best_available_front_dte": _best_available_front_dte,
        "best_available_front_exp": _best_available_front_exp,
    }

    if not scored_pairs:
        near_miss_gap = _check_near_miss_gap(all_before_event, parsed, event_date, min_gap, back_min_after_event, back_max, event_dte)
        if near_miss_gap:
            diagnostics["no_valid_pair_reason"] = "NEAR_MISS_EXPIRY_GAP"
            diagnostics["near_miss_gap"] = near_miss_gap
        else:
            diagnostics["no_valid_pair_reason"] = "NO_VALID_EXPIRATION_PAIR"
        return [], diagnostics

    scored_pairs.sort(key=lambda item: item[0])
    limit = max(1, int(config.CALENDAR_MAX_EXPIRATION_PAIRS_PER_TICKER or 1))
    return [(front, back) for _, front, back in scored_pairs[:limit]], diagnostics


def _select_generic_expiration_pairs(parsed: list[tuple[int, str]]) -> list[tuple[str, str]]:
    front_candidates = [
        item for item in parsed
        if int(config.CALENDAR_FRONT_MIN_DTE) <= item[0] <= int(config.CALENDAR_FRONT_MAX_DTE)
    ]

    pairs: list[tuple[str, str]] = []
    for front_dte, front_exp in front_candidates:
        back_candidates = [
            item for item in parsed
            if item[0] >= front_dte + int(config.CALENDAR_MIN_EXPIRATION_GAP_DAYS)
            and item[0] <= int(config.CALENDAR_BACK_MAX_DTE)
        ]
        if not back_candidates:
            back_candidates = [item for item in parsed if item[0] > front_dte]
        if not back_candidates:
            continue

        target_gap = int(config.CALENDAR_TARGET_EXPIRATION_GAP_DAYS)
        back_dte, back_exp = min(back_candidates, key=lambda item: abs((item[0] - front_dte) - target_gap))
        pairs.append((front_exp, back_exp))

        if len(pairs) >= max(1, int(config.CALENDAR_MAX_EXPIRATION_PAIRS_PER_TICKER or 1)):
            break

    return pairs


def _check_near_miss_gap(
    front_candidates: list[tuple[int, str]],
    all_parsed: list[tuple[int, str]],
    event_date: date,
    min_gap: int,
    back_min_after_event: int,
    back_max: int,
    event_dte: int,
) -> dict[str, Any] | None:
    near_miss_threshold = 5
    best: dict[str, Any] | None = None
    for front_dte, front_exp in front_candidates:
        for back_dte, back_exp in all_parsed:
            back_date = _parse_date(back_exp)
            if not back_date or back_date <= event_date:
                continue
            if back_dte > back_max or back_dte < event_dte + back_min_after_event:
                continue
            gap = back_dte - front_dte
            shortfall = min_gap - gap
            if 0 < shortfall <= near_miss_threshold:
                if best is None or shortfall < best["shortfall_days"]:
                    best = {"front_exp": front_exp, "back_exp": back_exp, "gap_days": gap, "min_gap": min_gap, "shortfall_days": shortfall}
    return best


def _event_for_ticker(positions: list[dict[str, Any]], ticker: str) -> dict[str, Any] | None:
    clean = str(ticker or "").upper().strip()
    for pos in positions or []:
        if str(pos.get("ticker") or "").upper().strip() != clean:
            continue
        event = pos.get("earnings_event")
        if isinstance(event, dict) and (event.get("earnings_date") or event.get("date")):
            return event
    return None


def _earnings_timing_payload(earnings_event: dict[str, Any] | None, front_expiration: str, back_expiration: str) -> dict[str, Any]:
    event_date = _parse_date((earnings_event or {}).get("earnings_date") or (earnings_event or {}).get("date"))
    front_date = _parse_date(front_expiration)
    back_date = _parse_date(back_expiration)
    session = str((earnings_event or {}).get("session_label") or (earnings_event or {}).get("time_of_day") or "").lower()
    same_day_ok = "after" in session or "amc" in session
    short_before = False
    long_after = False
    if event_date and front_date:
        short_before = front_date < event_date or (same_day_ok and front_date == event_date)
    if event_date and back_date:
        long_after = back_date > event_date
    return {
        "earnings_date": event_date.isoformat() if event_date else None,
        "session_label": (earnings_event or {}).get("session_label"),
        "short_expires_before_event": short_before,
        "long_expires_after_event": long_after,
        "captures_event": bool(short_before and long_after),
    }


def _determine_calendar_stage(
    front_dte: int | None,
    back_dte: int | None,
    days_until_earnings: int | None,
    min_dte: int,
    max_dte: int,
) -> str:
    """Determine the calendar lifecycle stage based on DTE and earnings proximity."""
    if days_until_earnings is not None and days_until_earnings < 0:
        return CalendarStage.POST_EVENT
    if front_dte is None or back_dte is None:
        return CalendarStage.DATA_INCOMPLETE
    if front_dte > max_dte:
        days_until_window = front_dte - max_dte
        if days_until_window > 14:
            return CalendarStage.PRE_WINDOW
        else:
            return CalendarStage.APPROACHING_WINDOW
    if front_dte >= min_dte:
        if front_dte <= min_dte + 3:
            return CalendarStage.ENTRY_WINDOW_CLOSING
        return CalendarStage.ENTRY_WINDOW_OPEN
    return CalendarStage.ENTRY_WINDOW_CLOSED


def _days_until_earnings_from_event(earnings_event: dict[str, Any] | None) -> int | None:
    """Return days until earnings from an earnings event dict, or None if unavailable."""
    if not earnings_event:
        return None
    cached = earnings_event.get("days_until_earnings")
    if cached is not None:
        try:
            return int(cached)
        except (TypeError, ValueError):
            pass
    event_date = _parse_date(earnings_event.get("earnings_date") or earnings_event.get("date"))
    if event_date is None:
        return None
    return (event_date - date.today()).days


def _build_low_dte_rejection_row(
    ticker: str,
    underlying_price: float,
    front_dte: int,
    front_exp: str | None,
    earnings_event: dict[str, Any] | None,
    pair_diagnostics: dict[str, Any],
) -> dict[str, Any]:
    """Build a persisted rejection row for a ticker whose best front DTE is below
    the approved minimum. The approved scoring gate is NOT changed — this row is
    stored so the ticker is visible in the row store, observation journal, and
    opportunity history with daily_opportunity_eligible=False.
    """
    min_dte = int(getattr(config, "CALENDAR_EARNINGS_FRONT_MIN_DTE", 7) or 7)
    max_dte = int(getattr(config, "CALENDAR_EARNINGS_FRONT_MAX_DTE", 14) or 14)
    days_until_earnings = _days_until_earnings_from_event(earnings_event)
    return {
        "ticker": ticker,
        "strategy": "Long Call Calendar",
        "calendar_stage": CalendarStage.ENTRY_WINDOW_CLOSED,
        "verdict": "FAIL",
        "action": "FAIL / FRONT DTE BELOW APPROVED MINIMUM",
        "daily_opportunity_eligible": False,
        "rejection_code": "FRONT_DTE_BELOW_APPROVED_MINIMUM",
        "rejection_reason": (
            f"Front leg has {front_dte} DTE, below approved minimum of {min_dte}"
        ),
        "front_dte": front_dte,
        "front_expiration": front_exp,
        "back_dte": None,
        "back_expiration": None,
        "score": 0.0,
        "underlying_price": underlying_price,
        "option_type": str(config.CALENDAR_OPTION_TYPE or "call").lower().strip(),
        "days_until_earnings": days_until_earnings,
        "approved_entry_window_min_dte": min_dte,
        "approved_entry_window_max_dte": max_dte,
        "distance_from_entry_window": front_dte - min_dte,  # negative — below minimum
        "days_until_entry_window": front_dte - min_dte,     # negative — past window
        "earnings_event": earnings_event or {},
        "expiration_pair_diagnostics": pair_diagnostics,
    }


def _parse_date(value: Any) -> date | None:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except Exception:
        return None

def _equity_tickers_from_positions(positions: list[dict[str, Any]]) -> list[str]:
    tickers: list[str] = []
    for pos in positions:
        ticker = str(pos.get("ticker") or "").upper().strip()
        if not ticker or ticker in CRYPTO_TICKERS:
            continue
        if str(pos.get("account", "")).strip().lower() == "crypto":
            continue
        if ticker not in tickers:
            tickers.append(ticker)
    return tickers


def _select_tickers(
    tickers: list[str],
    max_tickers: int | None,
    allowed_tickers: list[str] | None = None,
) -> list[str]:
    normalized = [str(t).upper().strip() for t in tickers if str(t).strip()]
    if allowed_tickers is not None:
        allowed = {str(t).upper().strip() for t in allowed_tickers if str(t).strip()}
        normalized = [t for t in normalized if t in allowed]

    preferred_order = ["NVDA", "AMZN", "META", "GOOGL", "SOFI", "QBTS", "HOOD", "SMR", "VST"]
    ordered = [ticker for ticker in preferred_order if ticker in normalized]
    ordered.extend([ticker for ticker in normalized if ticker not in ordered])

    limit = max(1, int(max_tickers or 1))
    return ordered[:limit]


def _compact_leg(option: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": option.get("symbol"),
        "option_type": option.get("option_type"),
        "expiration_date": option.get("expiration_date"),
        "strike": _float_or_none(option.get("strike")),
        "bid": _float_or_none(option.get("bid")),
        "ask": _float_or_none(option.get("ask")),
        "mid": _float_or_none(option.get("mid")) or _midpoint(_float_or_none(option.get("bid")), _float_or_none(option.get("ask"))),
        "last": _float_or_none(option.get("last")),
        "volume": _int_or_zero(option.get("volume")),
        "open_interest": _int_or_zero(option.get("open_interest")),
        "delta": _float_or_none(option.get("delta")),
        "theta": _float_or_none(option.get("theta")),
        "iv": _float_or_none(option.get("iv")),
    }


def _underlying_price(quote: dict[str, Any]) -> float | None:
    for key in ["last", "bid", "ask", "close", "prevclose"]:
        value = _float_or_none(quote.get(key))
        if value is not None and value > 0:
            return value
    bid = _float_or_none(quote.get("bid"))
    ask = _float_or_none(quote.get("ask"))
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    return None


def _days_to_expiration(expiration: str, today: date | None = None) -> int | None:
    today = today or date.today()
    try:
        exp_date = datetime.strptime(str(expiration), "%Y-%m-%d").date()
    except ValueError:
        return None
    return (exp_date - today).days


def _positive_mid_or_bid_ask(option: dict[str, Any]) -> bool:
    bid = _float_or_none(option.get("bid"))
    ask = _float_or_none(option.get("ask"))
    mid = _float_or_none(option.get("mid")) or _midpoint(bid, ask)
    return mid is not None and mid > 0


def _strike_key(value: Any) -> str:
    converted = _float_or_none(value)
    if converted is None:
        return ""
    return f"{converted:.4f}"


def _spread_pct(bid: float | None, ask: float | None, mid: float | None) -> float | None:
    if bid is None or ask is None or mid is None or mid <= 0:
        return None
    if bid < 0 or ask <= 0 or ask < bid:
        return None
    return (ask - bid) / mid * 100.0


def _midpoint(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None or bid < 0 or ask <= 0 or ask < bid:
        return None
    return (bid + ask) / 2.0


def _max_not_none(*values: float | None) -> float | None:
    present = [v for v in values if v is not None]
    return max(present) if present else None


def _float_or_none(value: Any) -> float | None:
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_zero(value: Any) -> int:
    try:
        if value in {None, ""}:
            return 0
        return int(float(value))
    except (TypeError, ValueError):
        return 0
