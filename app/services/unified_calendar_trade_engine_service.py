"""
app/services/unified_calendar_trade_engine_service.py — Unified Calendar Trade Engine v1.

This is an orchestration/reporting layer over the existing read-only modules:
- Earnings Trade Discovery v1: finds upcoming earnings events.
- Calendar Spread Screener v1: tries to build candidate spreads from Tradier chains.
- Earnings Calendar Strategy v1: evaluates whether a candidate actually fits earnings timing.
- Open Options Position Detector v1: detects already-entered calendars.
- Calendar Lifecycle Check v1: recommends next actions for open calendars.

The goal is one user-facing workflow:
1. Find new earnings-calendar opportunities.
2. Clearly state pass/fail requirements.
3. Show a possible spread only when one exists.
4. Score/rank candidates and recommend an entry plan.
5. Show already-entered calendars and next actions.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Callable

from app import config

LogFn = Callable[[str], None]


def build_unified_calendar_trade_engine(
    earnings_trade_discovery: dict[str, Any] | None,
    earnings_discovery_quality: dict[str, Any] | None = None,
    calendar_candidates: list[dict[str, Any]] | None = None,
    earnings_calendar_strategy: dict[str, Any] | None = None,
    open_options: dict[str, Any] | None = None,
    lifecycle_checks: dict[str, Any] | None = None,
    log_print: LogFn | None = None,
) -> dict[str, Any]:
    """Build one unified calendar-trading decision object for the report."""
    logger = log_print or (lambda msg: print(msg, flush=True))

    result: dict[str, Any] = {
        "source": "unified_calendar_trade_engine_v1",
        "enabled": bool(getattr(config, "UNIFIED_CALENDAR_ENGINE_ENABLED", True)),
        "has_data": False,
        "new_trade_rows": [],
        "open_trade_rows": [],
        "summary": {},
        "errors": [],
    }

    if not result["enabled"]:
        result["errors"].append("UNIFIED_CALENDAR_ENGINE_ENABLED=false")
        logger("Unified Calendar Trade Engine v1 disabled by UNIFIED_CALENDAR_ENGINE_ENABLED=false.")
        return _finalize(result)

    discovery = earnings_trade_discovery or {}
    quality = earnings_discovery_quality or {}
    candidates = [item for item in (calendar_candidates or []) if isinstance(item, dict)]
    strategy = earnings_calendar_strategy or {}
    open_options = open_options or {}
    lifecycle_checks = lifecycle_checks or {}

    strategy_by_ticker = {
        str(item.get("ticker") or "").upper().strip(): item
        for item in (strategy.get("items", []) or [])
        if isinstance(item, dict) and str(item.get("ticker") or "").strip()
    }
    candidates_by_ticker = {
        str(item.get("ticker") or "").upper().strip(): item
        for item in candidates
        if str(item.get("ticker") or "").strip()
    }

    discovery_items = [item for item in (discovery.get("items", []) or []) if isinstance(item, dict)]
    quality_by_ticker = {
        str(item.get("ticker") or "").upper().strip(): item
        for item in (quality.get("items", []) or [])
        if isinstance(item, dict) and str(item.get("ticker") or "").strip()
    }
    # Prefer quality rows if present because they include precheck pass/fail
    # reasons. Include un-checked raw events only when quality rows are absent.
    events_for_rows = []
    if quality_by_ticker:
        events_for_rows = list(quality_by_ticker.values())
    else:
        events_for_rows = discovery_items

    new_rows = []
    for event in events_for_rows:
        row = _build_new_trade_row(event, candidates_by_ticker, strategy_by_ticker)
        new_rows.append(row)

    # If the scanner produced a candidate that was not in the discovery list, include it
    # as a defensive fallback, but clearly mark the missing discovery event.
    discovered_tickers = {str(row.get("ticker") or "").upper() for row in new_rows}
    for ticker, candidate in candidates_by_ticker.items():
        if ticker not in discovered_tickers:
            row = _build_new_trade_row({}, candidates_by_ticker, strategy_by_ticker, fallback_ticker=ticker)
            row["requirements"].insert(0, _req("Earnings discovery", "WARN", "Candidate exists, but no matching discovery event was attached."))
            new_rows.append(row)

    new_rows.sort(key=lambda item: float(item.get("score") or 0), reverse=True)

    open_rows = _build_open_trade_rows(open_options, lifecycle_checks)

    result["new_trade_rows"] = new_rows
    result["open_trade_rows"] = open_rows
    result["has_data"] = bool(new_rows or open_rows)

    finalized = _finalize(result)
    summary = finalized["summary"]
    logger(
        "Unified Calendar Trade Engine v1 produced "
        f"{summary.get('new_trade_count', 0)} new-trade row(s), "
        f"{summary.get('pass_count', 0)} pass, "
        f"{summary.get('watch_count', 0)} watch/manual-review, "
        f"{summary.get('fail_count', 0)} fail, "
        f"{summary.get('open_trade_count', 0)} open-trade row(s)."
    )
    return finalized


def _build_new_trade_row(
    event: dict[str, Any],
    candidates_by_ticker: dict[str, dict[str, Any]],
    strategy_by_ticker: dict[str, dict[str, Any]],
    fallback_ticker: str | None = None,
) -> dict[str, Any]:
    quality_row = event if isinstance(event, dict) and event.get("checks") is not None else {}
    event_payload = quality_row.get("event") if isinstance(quality_row.get("event"), dict) else event
    ticker = str(quality_row.get("ticker") or event_payload.get("ticker") or event_payload.get("symbol") or fallback_ticker or "UNKNOWN").upper().strip()
    candidate = candidates_by_ticker.get(ticker) or {}
    strategy = strategy_by_ticker.get(ticker) or {}

    event = event_payload
    has_event = bool(event and (event.get("earnings_date") or event.get("date")))
    has_candidate = bool(candidate)
    has_strategy = bool(strategy)
    action = str(strategy.get("action") or "").upper()
    score = _float_or_none(strategy.get("score"))
    if score is None:
        score = _float_or_none(candidate.get("score"))
    if score is None:
        score = _baseline_score_for_event(event)

    requirements: list[dict[str, str]] = []
    requirements.append(
        _req(
            "Upcoming earnings event",
            "PASS" if has_event else "FAIL",
            _event_summary(event) if has_event else "No upcoming earnings event was attached.",
        )
    )

    if has_event and event.get("is_timestamp_confirmed"):
        requirements.append(_req("Earnings timestamp", "PASS", "Earnings date/session is confirmed."))
    elif has_event:
        requirements.append(_req("Earnings timestamp", "WARN", "Earnings session is unknown or unconfirmed."))
    else:
        requirements.append(_req("Earnings timestamp", "FAIL", "Cannot evaluate earnings placement without a timestamp."))

    if quality_row:
        for check in (quality_row.get("checks") or [])[:6]:
            requirements.append(
                _req(
                    f"Precheck: {check.get('name') or 'quality'}",
                    str(check.get("status") or "WARN"),
                    str(check.get("detail") or ""),
                )
            )

    if has_candidate:
        requirements.append(_req("Tradier calendar structure", "PASS", "Front/back same-strike calendar candidate was generated."))
        requirements.extend(_candidate_requirements(candidate))
    else:
        rejection = quality_row.get("primary_rejection_reason") if quality_row else None
        requirements.append(_req("Tradier calendar structure", "FAIL", rejection or "No front/back expiration pair or eligible options chain matched scanner settings."))
        requirements.append(_req("Liquidity / debit / IV", "FAIL", "No proposed spread exists, so liquidity and debit could not be scored."))

    if has_strategy:
        if strategy.get("is_preferred_setup"):
            requirements.append(_req("Earnings placement", "PASS", "Short leg expires before earnings and long leg captures the event."))
        elif strategy.get("earnings_relation") in {"earnings_unknown", "missing_expiration"}:
            requirements.append(_req("Earnings placement", "WARN", str(strategy.get("next_check") or "Manual review required.")))
        elif "AVOID" in action or "NOT AN EARNINGS" in action:
            requirements.append(_req("Earnings placement", "FAIL", str(strategy.get("next_check") or "Not a valid earnings-calendar setup.")))
        else:
            requirements.append(_req("Earnings placement", "WARN", str(strategy.get("next_check") or "Manual review required.")))
    elif has_candidate:
        requirements.append(_req("Earnings placement", "WARN", "Candidate exists, but earnings-aware strategy did not evaluate it."))

    verdict = _new_trade_verdict(has_candidate, strategy)
    entry_plan = _entry_plan(verdict, event, candidate, strategy)
    possible_spread = _possible_spread(candidate)

    return {
        "ticker": ticker,
        "type": "new_earnings_calendar_candidate",
        "score": round(max(0.0, min(100.0, float(score or 0.0))), 1),
        "verdict": verdict,
        "entry_plan": entry_plan,
        "earnings": _compact_event(event),
        "candidate": candidate,
        "strategy": strategy,
        "quality_precheck": quality_row,
        "possible_spread": possible_spread,
        "requirements": requirements,
        "reasons": _dedupe((strategy.get("reasons", []) if strategy else []) + (candidate.get("reasons", []) if candidate else [])),
        "risks": _dedupe((strategy.get("risks", []) if strategy else []) + (candidate.get("risks", []) if candidate else [])),
    }


def _candidate_requirements(candidate: dict[str, Any]) -> list[dict[str, str]]:
    reqs: list[dict[str, str]] = []
    max_spread = _float_or_none(candidate.get("max_leg_spread_pct"))
    min_oi = _float_or_none(candidate.get("min_leg_open_interest"))
    min_vol = _float_or_none(candidate.get("min_leg_volume"))
    debit_pct = _float_or_none(candidate.get("debit_pct_underlying"))
    iv_edge = _float_or_none(candidate.get("iv_edge"))

    if max_spread is None:
        reqs.append(_req("Bid/ask spread", "WARN", "Spread data unavailable."))
    elif max_spread <= float(config.CALENDAR_MAX_LEG_SPREAD_PCT):
        reqs.append(_req("Bid/ask spread", "PASS", f"Max leg spread {max_spread:.1f}% is within limit."))
    else:
        reqs.append(_req("Bid/ask spread", "FAIL", f"Max leg spread {max_spread:.1f}% exceeds limit."))

    liq_ok = True
    liq_notes = []
    if min_oi is not None:
        liq_notes.append(f"min OI {min_oi:.0f}")
        liq_ok = liq_ok and min_oi >= float(config.CALENDAR_MIN_OPEN_INTEREST)
    else:
        liq_ok = False
        liq_notes.append("OI unavailable")
    if min_vol is not None:
        liq_notes.append(f"min vol {min_vol:.0f}")
        liq_ok = liq_ok and min_vol >= float(config.CALENDAR_MIN_VOLUME)
    else:
        liq_ok = False
        liq_notes.append("volume unavailable")
    reqs.append(_req("Liquidity", "PASS" if liq_ok else "WARN", ", ".join(liq_notes)))

    if debit_pct is None:
        reqs.append(_req("Debit size", "WARN", "Debit as % of underlying unavailable."))
    elif debit_pct <= float(config.CALENDAR_MAX_DEBIT_PCT_UNDERLYING):
        reqs.append(_req("Debit size", "PASS", f"Debit is {debit_pct:.1f}% of underlying."))
    else:
        reqs.append(_req("Debit size", "FAIL", f"Debit is {debit_pct:.1f}% of underlying; too expensive."))

    if iv_edge is None:
        reqs.append(_req("IV relationship", "WARN", "IV edge unavailable."))
    elif iv_edge >= 0:
        reqs.append(_req("IV relationship", "PASS", f"Front IV exceeds/equal back IV by {iv_edge:.2f}."))
    else:
        reqs.append(_req("IV relationship", "WARN", f"Back IV is above front IV by {abs(iv_edge):.2f}."))
    return reqs


def _build_open_trade_rows(open_options: dict[str, Any], lifecycle_checks: dict[str, Any]) -> list[dict[str, Any]]:
    checks = [item for item in (lifecycle_checks or {}).get("checks", []) or [] if isinstance(item, dict)]
    if checks:
        rows = []
        for check in checks:
            rows.append(
                {
                    "ticker": str(check.get("ticker") or check.get("underlying") or "UNKNOWN").upper(),
                    "score": _score_open_trade(check),
                    "verdict": check.get("action") or "HOLD / MONITOR",
                    "next_action": check.get("next_check") or "Recheck live spread value before market close.",
                    "structure": _open_structure(check),
                    "value": _open_value_summary(check),
                    "reasons": check.get("reasons", []) or [],
                    "risks": check.get("risks", []) or [],
                    "raw": check,
                }
            )
        return rows

    calendars = [item for item in (open_options or {}).get("calendars", []) or [] if isinstance(item, dict)]
    rows = []
    for cal in calendars:
        rows.append(
            {
                "ticker": str(cal.get("ticker") or cal.get("underlying") or "UNKNOWN").upper(),
                "score": 50.0,
                "verdict": "OPEN / NEEDS LIFECYCLE CHECK",
                "next_action": "Lifecycle checker did not return a check; reprice manually before acting.",
                "structure": _open_structure(cal),
                "value": _open_value_summary(cal),
                "reasons": ["Open calendar detected from Tradier option legs."],
                "risks": ["No lifecycle check was attached to this open calendar."],
                "raw": cal,
            }
        )
    return rows


def _new_trade_verdict(has_candidate: bool, strategy: dict[str, Any]) -> str:
    if not has_candidate:
        return "FAIL / NO VALID CALENDAR STRUCTURE"
    action = str(strategy.get("action") or "").upper()
    if strategy.get("is_preferred_setup"):
        return "PASS / POSSIBLE ENTRY SETUP"
    if "EARNINGS CALENDAR CANDIDATE" in action:
        return "PASS / POSSIBLE ENTRY SETUP"
    if "URGENT" in action:
        return "WATCH / URGENT MANUAL REVIEW"
    if "MANUAL REVIEW" in action:
        return "WATCH / TIMESTAMP NEEDED"
    if "AVOID" in action or "NOT AN EARNINGS" in action:
        return "FAIL / NOT AN EARNINGS CALENDAR"
    return "WATCH / STRUCTURE FOUND"


def _entry_plan(verdict: str, event: dict[str, Any], candidate: dict[str, Any], strategy: dict[str, Any]) -> str:
    dte = _int_or_none(event.get("days_until_earnings"))
    if verdict.startswith("FAIL"):
        if not candidate:
            return "No entry. This earnings event did not produce a valid Tradier calendar candidate under current liquidity/date settings."
        return strategy.get("next_check") or "No entry until the earnings/calendar relationship passes requirements."
    if "URGENT" in verdict:
        return "Manual live review only. Recheck earnings timing, bid/ask, short-leg event risk, and max debit before any entry."
    if "TIMESTAMP" in verdict:
        return "Wait. Confirm earnings date/session before treating this as an earnings-calendar trade."
    if dte is not None and dte <= 1:
        return "Entry timing is urgent. Only consider after live quotes confirm spread quality and short leg does not carry unwanted event risk."
    if dte is not None and dte <= 4:
        return "Possible entry window: today or next trading session after live bid/ask confirms debit, liquidity, and earnings timing."
    return "Watch. Re-run closer to earnings; preferred entry is usually inside the configured pre-earnings window after liquidity confirms."


def _possible_spread(candidate: dict[str, Any]) -> dict[str, Any]:
    if not candidate:
        return {}
    return {
        "ticker": candidate.get("ticker"),
        "option_type": candidate.get("option_type") or "call",
        "strike": candidate.get("strike"),
        "short_expiration": candidate.get("front_expiration"),
        "long_expiration": candidate.get("back_expiration"),
        "front_dte": candidate.get("front_dte"),
        "back_dte": candidate.get("back_dte"),
        "short_symbol": (candidate.get("short_front_leg") or {}).get("symbol"),
        "long_symbol": (candidate.get("long_back_leg") or {}).get("symbol"),
        "conservative_debit": candidate.get("conservative_debit"),
        "mid_debit": candidate.get("mid_debit"),
        "max_leg_spread_pct": candidate.get("max_leg_spread_pct"),
        "min_leg_volume": candidate.get("min_leg_volume"),
        "min_leg_open_interest": candidate.get("min_leg_open_interest"),
        "iv_edge": candidate.get("iv_edge"),
    }


def _score_open_trade(check: dict[str, Any]) -> float:
    action = str(check.get("action") or "").upper()
    if "TAKE PROFIT" in action:
        return 90.0
    if "CUT" in action or "URGENT" in action:
        return 20.0
    if "RECHECK" in action or "EVENT" in action:
        return 55.0
    return 70.0


def _open_structure(item: dict[str, Any]) -> str:
    strike = item.get("strike")
    opt_type = str(item.get("option_type") or "call").upper()
    front = item.get("front_expiration")
    back = item.get("back_expiration")
    return f"{strike if strike is not None else '—'} {opt_type} | short {front or '—'} / long {back or '—'}"


def _open_value_summary(item: dict[str, Any]) -> str:
    parts = []
    for label, key in [
        ("current debit", "current_mid_debit"),
        ("entry debit est.", "entry_debit_estimate"),
        ("P/L est.", "estimated_pnl_pct"),
    ]:
        val = item.get(key)
        if val is not None:
            if "P/L" in label:
                parts.append(f"{label} {float(val):+.1f}%")
            else:
                parts.append(f"{label} {float(val):.2f}")
    return " | ".join(parts) if parts else "Value unavailable"


def _event_summary(event: dict[str, Any]) -> str:
    dte = event.get("days_until_earnings")
    dte_text = f"{dte} DTE" if dte is not None else "unknown DTE"
    return f"{event.get('earnings_date') or event.get('date') or 'unknown date'} / {event.get('session_label') or 'Unknown'} / {dte_text}"


def _compact_event(event: dict[str, Any]) -> dict[str, Any]:
    if not event:
        return {"has_data": False}
    return {
        "has_data": bool(event.get("has_data", True)) if (event.get("earnings_date") or event.get("date")) else False,
        "ticker": event.get("ticker") or event.get("symbol"),
        "earnings_date": event.get("earnings_date") or event.get("date"),
        "session_label": event.get("session_label") or "Unknown",
        "days_until_earnings": event.get("days_until_earnings"),
        "is_timestamp_confirmed": event.get("is_timestamp_confirmed"),
        "source": event.get("source"),
    }


def _baseline_score_for_event(event: dict[str, Any]) -> float:
    if not event:
        return 0.0
    score = 35.0
    dte = _int_or_none(event.get("days_until_earnings"))
    if dte is not None and 1 <= dte <= 5:
        score += 10.0
    if event.get("is_timestamp_confirmed"):
        score += 10.0
    return score


def _req(name: str, status: str, detail: str) -> dict[str, str]:
    status = status.upper().strip()
    if status not in {"PASS", "WARN", "FAIL"}:
        status = "WARN"
    return {"name": name, "status": status, "detail": detail}


def _finalize(result: dict[str, Any]) -> dict[str, Any]:
    new_rows = result.get("new_trade_rows", []) or []
    open_rows = result.get("open_trade_rows", []) or []
    pass_count = sum(1 for row in new_rows if str(row.get("verdict") or "").startswith("PASS"))
    fail_count = sum(1 for row in new_rows if str(row.get("verdict") or "").startswith("FAIL"))
    watch_count = len(new_rows) - pass_count - fail_count
    urgent_count = sum(1 for row in new_rows + open_rows if "URGENT" in str(row.get("verdict") or row.get("next_action") or "").upper())
    result["summary"] = {
        "new_trade_count": len(new_rows),
        "open_trade_count": len(open_rows),
        "pass_count": pass_count,
        "watch_count": watch_count,
        "fail_count": fail_count,
        "urgent_count": urgent_count,
        "has_new_candidates": bool(new_rows),
        "has_open_calendars": bool(open_rows),
    }
    result["has_data"] = bool(new_rows or open_rows)
    return result


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _dedupe(items: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out
