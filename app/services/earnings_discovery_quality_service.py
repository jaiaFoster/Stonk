"""
app/services/earnings_discovery_quality_service.py — Earnings discovery quality filter.

This service sits between raw earnings-calendar discovery and the expensive
calendar-spread scanner. It keeps dev mode useful by fetching a broader raw
earnings universe, then spending a small Tradier budget on optionability checks
before the app attempts full option-chain/calendar calculations.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable

from app import config
from app.providers.tradier_provider import TradierProvider
from app.utils.log_safety import sanitize_for_log

LogFn = Callable[[str], None]


def filter_earnings_discovery_for_calendar_scan(
    earnings_trade_discovery: dict[str, Any] | None,
    log_print: LogFn | None = None,
    run_mode: str = "prod",
    held_tickers: list[str] | None = None,
    earnings_events: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return optionable/liquid-enough earnings events for calendar scanning.

    The output keeps all checked rows with rejection reasons, and exposes a
    smaller `tickers` list for downstream full calendar-chain scans.
    """
    logger = log_print or (lambda msg: print(msg, flush=True))
    provider = TradierProvider()
    clean_mode = str(run_mode or "prod").strip().lower()
    discovery = earnings_trade_discovery or {}
    raw_items = [item for item in (discovery.get("items") or []) if isinstance(item, dict)]

    raw_only_count = len(raw_items)
    raw_items = _merge_universe_discovery(
        raw_items, held_tickers, earnings_events, logger,
    )

    max_to_check = max(1, int(getattr(config, "EARNINGS_DISCOVERY_MAX_OPTIONABLE_TO_CHECK", 40) or 40))
    if clean_mode == "dev":
        max_to_check = max(1, int(getattr(config, "EARNINGS_DISCOVERY_DEV_MAX_OPTIONABLE_TO_CHECK", max_to_check) or max_to_check))
    max_final = max(1, int(getattr(config, "EARNINGS_DISCOVERY_MAX_FINAL_CANDIDATES", 20) or 20))

    result: dict[str, Any] = {
        "source": "earnings_discovery_quality_filter_v1",
        "enabled": bool(getattr(config, "EARNINGS_DISCOVERY_ENABLED", True)),
        "has_data": False,
        "items": [],
        "passed_items": [],
        "rejected_items": [],
        "tickers": [],
        "events_by_ticker": {},
        "summary": {
            "raw_event_count": len(raw_items),
            "raw_only_count": raw_only_count,
            "universe_added_count": len(raw_items) - raw_only_count,
            "checked_count": 0,
            "passed_count": 0,
            "rejected_count": 0,
            "max_to_check": max_to_check,
            "max_final": max_final,
        },
        "errors": [],
    }

    if not raw_items:
        logger("Earnings Discovery Quality Filter v1 skipped: no raw earnings events to check.")
        return result

    if not provider.is_configured:
        result["errors"].append("TRADIER_ACCESS_TOKEN is not set; cannot pre-check optionability.")
        logger("Earnings Discovery Quality Filter v1 skipped: TRADIER_ACCESS_TOKEN is not set.")
        return result

    selected = _prioritize_raw_events(raw_items)[:max_to_check]
    pre_filter_count = len(selected)
    selected = _cheap_prefilter(selected, logger)
    tickers = [str(item.get("ticker") or item.get("symbol") or "").upper().strip() for item in selected]
    tickers = [ticker for ticker in tickers if ticker]

    logger(
        "Earnings Discovery Quality Filter v1 checking "
        f"{len(tickers)}/{len(raw_items)} raw earnings event(s); final_limit={max_final}"
        f"; pre-filtered {pre_filter_count - len(selected)} unlikely candidates"
        + (" (dev-mode optionability budget)" if clean_mode == "dev" else "")
    )
    logger(
        f"Earnings date gate: EARNINGS_DATE_REQUIRE_MULTI_SOURCE="
        f"{bool(getattr(config, 'EARNINGS_DATE_REQUIRE_MULTI_SOURCE', False))}"
    )

    try:
        quotes = provider.get_quotes(tickers, greeks=False) if tickers else {}
    except Exception as e:
        safe_error = sanitize_for_log(e, [config.TRADIER_ACCESS_TOKEN, config.RUN_TOKEN])
        result["errors"].append(str(safe_error))
        logger(f"Earnings Discovery Quality Filter quote precheck failed: {safe_error}")
        return result

    rows: list[dict[str, Any]] = []
    for item in selected:
        ticker = str(item.get("ticker") or item.get("symbol") or "").upper().strip()
        if not ticker:
            continue
        quote = quotes.get(ticker) or {}
        logger(f"[calendar] {ticker}: event fields include date_confidence={item.get('earnings_date_confidence')} sources={item.get('sources_seen')}")
        row = _quality_row(item, quote)
        try:
            expirations = provider.get_expirations(ticker)
            row["expiration_count"] = len(expirations)
            pair = _select_calendar_expiration_pair(expirations, event=item)
            if pair:
                row["front_expiration"] = pair[0]
                row["back_expiration"] = pair[1]
                row["front_dte"] = _dte(pair[0])
                row["back_dte"] = _dte(pair[1])
                is_near_miss = len(pair) > 2 and pair[2]
                if is_near_miss:
                    earnings_dt = _parse_date(item.get("earnings_date") or item.get("date"))
                    front_dt = _parse_date(pair[0])
                    gap_days = (front_dt - earnings_dt).days if earnings_dt and front_dt else "?"
                    row["expiry_near_miss"] = True
                    row["expiry_gap_note"] = f"Nearest expiry {pair[0]} is {gap_days}d after earnings — holiday or weekly gap. Manual evaluation recommended."
                    row["checks"].append(_check("Option expirations", "WARN", f"Near-miss: {pair[0]} / {pair[1]} — front leg expires after earnings ({gap_days}d gap)."))
                else:
                    row["checks"].append(_check("Option expirations", "PASS", f"Matched {pair[0]} / {pair[1]} calendar window."))
            else:
                near_miss_exp = _find_near_miss_expiry(expirations, item)
                if near_miss_exp:
                    row["expiry_near_miss"] = True
                    row["expiry_gap_note"] = near_miss_exp["note"]
                    row["checks"].append(_check("Option expirations", "WARN", near_miss_exp["check_detail"]))
                else:
                    row["expiry_near_miss"] = False
                    row["checks"].append(_check("Option expirations", "FAIL", "No front/back expiration pair matched scanner settings."))
        except Exception as e:
            safe_error = sanitize_for_log(e, [config.TRADIER_ACCESS_TOKEN, config.RUN_TOKEN])
            row["checks"].append(_check("Option expirations", "FAIL", f"Expiration lookup failed: {safe_error}"))
            row["errors"].append(str(safe_error))

        _finalize_quality_row(row)
        rows.append(row)

    passed = [row for row in rows if row.get("passes_precheck")]
    # Rank liquid, known-session, closer-window events first.
    passed.sort(key=lambda row: float(row.get("quality_score") or 0), reverse=True)
    final = passed[:max_final]
    rejected = [row for row in rows if not row.get("passes_precheck")]

    result["items"] = rows
    result["passed_items"] = final
    result["rejected_items"] = rejected
    result["tickers"] = [row["ticker"] for row in final]
    result["events_by_ticker"] = {row["ticker"]: row for row in rows}
    result["has_data"] = bool(rows)
    result["summary"] = {
        "raw_event_count": len(raw_items),
        "checked_count": len(rows),
        "passed_count": len(final),
        "rejected_count": len(rejected),
        "optionable_count_before_final_cap": len(passed),
        "max_to_check": max_to_check,
        "max_final": max_final,
    }
    logger(
        "Earnings Discovery Quality Filter v1 produced "
        f"{len(final)} final optionable ticker(s), {len(rejected)} rejected, "
        f"from {len(raw_items)} raw event(s)."
    )
    return result


def _quality_row(event: dict[str, Any], quote: dict[str, Any]) -> dict[str, Any]:
    ticker = str(event.get("ticker") or event.get("symbol") or "").upper().strip()
    price = _underlying_price(quote)
    volume = _number(quote.get("volume"))
    avg_volume = _number(quote.get("average_volume")) or volume
    checks: list[dict[str, str]] = []
    errors: list[str] = []

    if quote:
        checks.append(_check("Tradier quote", "PASS", f"Quote found; last/mark {price if price is not None else 'unknown'}."))
    else:
        checks.append(_check("Tradier quote", "FAIL", "No Tradier quote returned."))

    min_price = float(getattr(config, "EARNINGS_DISCOVERY_MIN_UNDERLYING_PRICE", 5) or 5)
    if price is None:
        checks.append(_check("Underlying price", "FAIL", "No usable price."))
    elif price >= min_price:
        checks.append(_check("Underlying price", "PASS", f"Price {price:.2f} is above minimum {min_price:.2f}."))
    else:
        checks.append(_check("Underlying price", "FAIL", f"Price {price:.2f} is below minimum {min_price:.2f}."))

    min_avg_vol = float(getattr(config, "EARNINGS_DISCOVERY_MIN_AVERAGE_VOLUME", 500000) or 500000)
    if avg_volume is None:
        checks.append(_check("Stock liquidity", "WARN", "Average volume unavailable."))
    elif avg_volume >= min_avg_vol:
        checks.append(_check("Stock liquidity", "PASS", f"Average/recent volume {avg_volume:.0f} is above minimum."))
    else:
        checks.append(_check("Stock liquidity", "FAIL", f"Average/recent volume {avg_volume:.0f} is below minimum {min_avg_vol:.0f}."))

    if event.get("is_timestamp_confirmed"):
        checks.append(_check("Earnings timestamp", "PASS", "Provider marked timestamp/session as confirmed."))
    else:
        checks.append(_check("Earnings timestamp", "WARN", "Earnings date/session is unconfirmed or unknown."))

    confidence = event.get("earnings_date_confidence") or "unknown"
    has_conflict = bool(event.get("earnings_source_conflict"))
    sources_seen = event.get("sources_seen") or []
    require_multi = bool(getattr(config, "EARNINGS_DATE_REQUIRE_MULTI_SOURCE", False))
    if has_conflict:
        conflict_status = "FAIL" if require_multi else "WARN"
        checks.append(_check("Earnings date agreement", conflict_status, f"Cross-source date conflict detected (confidence={confidence}, sources={sources_seen})."))
    elif len(sources_seen) >= 2:
        checks.append(_check("Earnings date agreement", "PASS", f"Date confirmed by {len(sources_seen)} sources (confidence={confidence})."))
    elif len(sources_seen) == 1:
        single_status = "FAIL" if require_multi else "WARN"
        checks.append(_check("Earnings date agreement", single_status, f"Single-source earnings date — only {sources_seen[0]} (confidence={confidence})."))

    historical_move = _number(event.get("avg_historical_earnings_move"))
    high_move_threshold = float(getattr(config, "CALENDAR_HIGH_MOVE_WARNING_THRESHOLD", 0.08) or 0.08)
    high_move_warning = bool(historical_move is not None and historical_move >= high_move_threshold)
    if high_move_warning:
        checks.append(_check("Historical earnings move", "WARN", f"Avg historical earnings move {historical_move*100:.1f}% — large moves reduce calendar edge."))

    return {
        "ticker": ticker,
        "event": event,
        "earnings_date": event.get("earnings_date") or event.get("date"),
        "session_label": event.get("session_label") or "Unknown",
        "days_until_earnings": event.get("days_until_earnings"),
        "source": event.get("source"),
        "universe_source": event.get("universe_source"),
        "earnings_date_confidence": event.get("earnings_date_confidence") or "unknown",
        "date_confidence": event.get("earnings_date_confidence") or event.get("date_confidence") or "unknown",
        "date_conflict": bool(event.get("earnings_source_conflict") or event.get("date_conflict")),
        "date_sources": event.get("sources_seen") or event.get("date_sources") or [],
        "high_move_warning": high_move_warning,
        "high_move_note": f"Historical earnings move avg {historical_move*100:.1f}% — large moves reduce calendar edge. Structure cost likely high relative to max profit." if high_move_warning else None,
        "is_timestamp_confirmed": bool(event.get("is_timestamp_confirmed")),
        "quote": quote,
        "underlying_price": price,
        "volume": volume,
        "average_volume": avg_volume,
        "expiration_count": 0,
        "front_expiration": None,
        "back_expiration": None,
        "front_dte": None,
        "back_dte": None,
        "checks": checks,
        "errors": errors,
        "passes_precheck": False,
        "quality_score": 0.0,
        "primary_rejection_reason": None,
    }


def _finalize_quality_row(row: dict[str, Any]) -> None:
    score = 45.0
    fail_reasons: list[str] = []
    for check in row.get("checks", []) or []:
        status = str(check.get("status") or "").upper()
        if status == "PASS":
            score += 8
        elif status == "WARN":
            score -= 2
        elif status == "FAIL":
            score -= 15
            fail_reasons.append(f"{check.get('name')}: {check.get('detail')}")
    if row.get("is_timestamp_confirmed"):
        score += 4
    dte = _number(row.get("days_until_earnings"))
    if dte is not None:
        ideal_min = int(getattr(config, "EARNINGS_CALENDAR_IDEAL_ENTRY_MIN_DTE", 6) or 6)
        ideal_max = int(getattr(config, "EARNINGS_CALENDAR_IDEAL_ENTRY_MAX_DTE", 12) or 12)
        late_dte = int(getattr(config, "EARNINGS_CALENDAR_LATE_ENTRY_DTE", 4) or 4)
        if ideal_min <= dte <= ideal_max:
            score += 8
        elif dte <= late_dte:
            score -= 4
    avg_volume = _number(row.get("average_volume")) or 0
    if avg_volume >= 5_000_000:
        score += 8
    elif avg_volume >= 1_000_000:
        score += 4
    price = _number(row.get("underlying_price")) or 0
    if price >= 25:
        score += 4

    row["quality_score"] = round(max(0.0, min(100.0, score)), 1)
    hard_fail = any(str(check.get("status") or "").upper() == "FAIL" for check in row.get("checks", []) or [])
    row["passes_precheck"] = not hard_fail
    row["primary_rejection_reason"] = fail_reasons[0] if fail_reasons else None


def _select_calendar_expiration_pair(expirations: list[str], event: dict[str, Any] | None = None) -> tuple[str, str] | None:
    """Pick the best front/back expiration pair.

    For earnings-calendar discovery, prefer a short leg that expires before
    the earnings event and a long leg that remains open after the event. This
    fixes the old generic calendar behavior that often selected a front leg
    after earnings, causing otherwise interesting names such as CRDO/HPE to
    be rejected as "not an earnings calendar."
    """
    today = date.today()
    parsed: list[tuple[int, str]] = []
    for raw in expirations or []:
        dte = _dte(raw, today=today)
        if dte is not None and dte >= 0:
            parsed.append((dte, str(raw)))
    parsed.sort(key=lambda item: item[0])
    if not parsed:
        return None

    if bool(getattr(config, "CALENDAR_EARNINGS_EVENT_AWARE_EXPIRATIONS", True)) and event:
        event_pair = _select_event_aware_pair(parsed, event, today)
        if event_pair:
            front, back, near_miss = event_pair[0], event_pair[1], event_pair[2] if len(event_pair) > 2 else False
            if near_miss:
                return front, back, True
            return front, back

    return _select_generic_calendar_pair(parsed)


def _select_event_aware_pair(
    parsed_expirations: list[tuple[int, str]],
    event: dict[str, Any],
    today: date,
) -> tuple[str, str] | None:
    event_date = _parse_date(event.get("earnings_date") or event.get("date"))
    if not event_date:
        return None

    session = str(event.get("session_label") or event.get("time_of_day") or event.get("hour") or "").lower()
    event_dte = (event_date - today).days
    front_min = int(getattr(config, "CALENDAR_EARNINGS_FRONT_MIN_DTE", 1) or 1)
    front_max = int(getattr(config, "CALENDAR_EARNINGS_FRONT_MAX_DTE", 14) or 14)
    back_min_after_event = int(getattr(config, "CALENDAR_EARNINGS_BACK_MIN_DTE_AFTER_EVENT", 14) or 14)
    back_max = int(getattr(config, "CALENDAR_EARNINGS_BACK_MAX_DTE", config.CALENDAR_BACK_MAX_DTE) or config.CALENDAR_BACK_MAX_DTE)
    target_gap = int(getattr(config, "CALENDAR_TARGET_EXPIRATION_GAP_DAYS", 30) or 30)

    # For AMC earnings, same-day expiration occurs before the announcement.
    # For BMO/unknown, require the short leg to expire strictly before the event.
    same_day_ok = "after" in session or "amc" in session

    front_candidates: list[tuple[int, str]] = []
    for dte, exp in parsed_expirations:
        exp_date = _parse_date(exp)
        if not exp_date:
            continue
        expires_before_event = exp_date < event_date or (same_day_ok and exp_date == event_date)
        if not expires_before_event:
            continue
        if front_min <= dte <= front_max:
            front_candidates.append((dte, exp))

    near_miss_front = False
    if not front_candidates:
        step_window = int(getattr(config, "CALENDAR_SHORT_LEG_STEP_WINDOW_DAYS", 10) or 10)
        for dte, exp in parsed_expirations:
            exp_date = _parse_date(exp)
            if not exp_date:
                continue
            gap_after = (exp_date - event_date).days
            if 0 < gap_after <= step_window and front_min <= dte <= front_max:
                front_candidates.append((dte, exp))
                near_miss_front = True
        if not front_candidates:
            return None

    best_pair: tuple[float, str, str] | None = None
    for front_dte, front_exp in front_candidates:
        for back_dte, back_exp in parsed_expirations:
            back_date = _parse_date(back_exp)
            if not back_date or back_date <= event_date:
                continue
            if back_dte < event_dte + back_min_after_event or back_dte > back_max:
                continue
            gap = back_dte - front_dte
            if gap < int(config.CALENDAR_MIN_EXPIRATION_GAP_DAYS or 14):
                continue
            # Prefer a back leg near target gap and a front leg close to, but before, earnings.
            score = abs(gap - target_gap) + abs((event_dte - front_dte) - 1) * 0.35
            if best_pair is None or score < best_pair[0]:
                best_pair = (score, front_exp, back_exp)

    if best_pair:
        return best_pair[1], best_pair[2], near_miss_front
    return None


def _select_generic_calendar_pair(parsed: list[tuple[int, str]]) -> tuple[str, str] | None:
    front_min = int(config.CALENDAR_FRONT_MIN_DTE or 7)
    front_max = int(config.CALENDAR_FRONT_MAX_DTE or 21)
    min_gap = int(config.CALENDAR_MIN_EXPIRATION_GAP_DAYS or 14)
    back_max = int(config.CALENDAR_BACK_MAX_DTE or 70)
    target_gap = int(config.CALENDAR_TARGET_EXPIRATION_GAP_DAYS or 30)

    front_candidates = [(dte, exp) for dte, exp in parsed if front_min <= dte <= front_max]
    if not front_candidates:
        return None
    best_pair: tuple[int, str, str] | None = None
    for front_dte, front_exp in front_candidates:
        for back_dte, back_exp in parsed:
            gap = back_dte - front_dte
            if gap < min_gap or back_dte > back_max:
                continue
            score = abs(gap - target_gap)
            if best_pair is None or score < best_pair[0]:
                best_pair = (score, front_exp, back_exp)
    if best_pair:
        return best_pair[1], best_pair[2]
    return None


def _find_near_miss_expiry(expirations: list[str], event: dict[str, Any]) -> dict[str, str] | None:
    """Check if any available expiry falls within the step window after earnings.

    Called when no valid front/back pair was found. If the nearest expiry is
    close to earnings (within CALENDAR_SHORT_LEG_STEP_WINDOW_DAYS), flag as
    near-miss rather than a hard structure failure.
    """
    earnings_date = _parse_date(event.get("earnings_date") or event.get("date"))
    if not earnings_date:
        return None
    step_window = int(getattr(config, "CALENDAR_SHORT_LEG_STEP_WINDOW_DAYS", 10) or 10)
    best_exp = None
    best_gap = None
    for exp_str in expirations:
        exp_dt = _parse_date(exp_str)
        if not exp_dt:
            continue
        gap = (exp_dt - earnings_date).days
        if 0 < gap <= step_window:
            if best_gap is None or gap < best_gap:
                best_gap = gap
                best_exp = exp_str
    if best_exp is None:
        return None
    return {
        "note": (
            f"Nearest expiry {best_exp} is {best_gap}d after earnings — "
            f"holiday gap or missing weekly. Consider manual entry."
        ),
        "check_detail": (
            f"Near-miss: nearest available expiry {best_exp} is {best_gap}d after earnings — "
            f"no valid pair, but front leg is close. Manual evaluation recommended."
        ),
    }


def _cheap_prefilter(events: list[dict[str, Any]], logger: LogFn) -> list[dict[str, Any]]:
    min_price = float(getattr(config, "UNIVERSE_MIN_PRICE", 10) or 10)
    excluded_fund_tickers = set(
        t.strip().upper()
        for t in str(getattr(config, "EARNINGS_EXCLUDED_FUND_TICKERS", "NAD,NEA,NMZ,NUV,NVG,ACP,AOD,FAX,NZF") or "").split(",")
        if t.strip()
    )
    kept: list[dict[str, Any]] = []
    for item in events:
        ticker = str(item.get("ticker") or item.get("symbol") or "").upper().strip()
        if len(ticker) > 5:
            logger(f"[earnings_prefilter] {ticker} skipped: ticker length {len(ticker)} > 5 (likely non-standard)")
            continue
        if ticker in excluded_fund_tickers:
            logger(f"[calendar] pre-filter: {ticker} rejected (fund exclusion list)")
            continue
        cached_price = _number(item.get("last_price") or item.get("price") or item.get("close"))
        if cached_price is not None and cached_price < min_price:
            logger(f"[earnings_prefilter] {ticker} skipped: cached price ${cached_price:.2f} < ${min_price:.2f}")
            continue
        kept.append(item)
    return kept


def _prioritize_raw_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(item: dict[str, Any]) -> tuple[int, int, str]:
        dte = _number(item.get("days_until_earnings"))
        confirmed = 0 if item.get("is_timestamp_confirmed") else 1
        ideal_min = int(getattr(config, "EARNINGS_CALENDAR_IDEAL_ENTRY_MIN_DTE", 6) or 6)
        ideal_max = int(getattr(config, "EARNINGS_CALENDAR_IDEAL_ENTRY_MAX_DTE", 12) or 12)
        ideal_mid = (ideal_min + ideal_max) / 2.0
        # Prefer confirmed events in the ideal entry window, then near-window events.
        if dte is None:
            distance_penalty = 999
        elif ideal_min <= dte <= ideal_max:
            distance_penalty = int(abs(dte - ideal_mid))
        else:
            distance_penalty = int(min(abs(dte - ideal_min), abs(dte - ideal_max)) + 20)
        return (distance_penalty, confirmed, str(item.get("ticker") or item.get("symbol") or ""))
    return sorted(events, key=key)


def _parse_date(value: Any) -> date | None:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except Exception:
        return None

def _check(name: str, status: str, detail: str) -> dict[str, str]:
    return {"name": name, "status": status.upper(), "detail": str(detail)}


def _underlying_price(quote: dict[str, Any]) -> float | None:
    if not quote:
        return None
    for key in ["last", "bid", "ask", "close", "prevclose"]:
        value = _number(quote.get(key))
        if value is not None and value > 0:
            return value
    bid = _number(quote.get("bid"))
    ask = _number(quote.get("ask"))
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    return None


def _number(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _dte(raw_date: Any, today: date | None = None) -> int | None:
    try:
        d = datetime.strptime(str(raw_date)[:10], "%Y-%m-%d").date()
    except Exception:
        return None
    today = today or date.today()
    return (d - today).days


def _merge_universe_discovery(
    raw_items: list[dict[str, Any]],
    held_tickers: list[str] | None,
    earnings_events: dict[str, dict[str, Any]] | None,
    logger: LogFn,
) -> list[dict[str, Any]]:
    if not getattr(config, "UNIVERSE_DISCOVERY_ENABLED", True):
        return raw_items
    try:
        from app.services.universe_discovery_service import get_earnings_candidates
        universe = get_earnings_candidates(
            earnings_events=earnings_events,
            exclude_held=held_tickers,
            max_tickers=int(getattr(config, "EARNINGS_DISCOVERY_UNIVERSE_MAX_CANDIDATES", 50) or 50),
            log_print=logger,
        )
    except Exception as exc:
        logger(f"[universe_discovery] merge failed (non-fatal): {exc}")
        return raw_items

    if not universe.get("has_data"):
        return raw_items

    existing_tickers = {
        str(item.get("ticker") or item.get("symbol") or "").upper().strip()
        for item in raw_items
    }
    new_count = 0
    for uitem in universe.get("items") or []:
        ticker = str(uitem.get("ticker") or "").upper().strip()
        if not ticker or ticker in existing_tickers:
            continue
        raw_items.append({
            "ticker": ticker,
            "symbol": ticker,
            "earnings_date": uitem.get("earnings_date"),
            "date": uitem.get("earnings_date"),
            "source": "universe_discovery",
            "universe_source": "universe_discovery",
            "has_data": True,
            "days_until_earnings": _dte(uitem.get("earnings_date")),
        })
        existing_tickers.add(ticker)
        new_count += 1

    logger(
        f"[universe_discovery] merged with raw events: {len(raw_items)} unique candidates; "
        f"{new_count} new tickers added vs raw-only run"
    )
    return raw_items
