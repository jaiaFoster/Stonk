"""
app/services/earnings_calendar_strategy_service.py — Earnings calendar strategy scoring.

Earnings Calendar Strategy v1 consumes two already-built data products:
- Calendar Spread Screener v1 candidates from Tradier chains
- Earnings Timestamp Provider v1 events

It does not place trades. It adds an earnings-aware decision layer so a strong
technical calendar is not mistaken for a good earnings calendar when the short
leg spans the earnings event or when the event is not captured by the structure.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable

from app import config
from app.services.earnings_trust_service import normalize_earnings_trust
from app.services.strategy_row_normalization_service import normalize_strategy_row

LogFn = Callable[[str], None]
StrategyRows = list[dict[str, Any]]


def evaluate_earnings_calendar_candidates(
    calendar_candidates: list[dict[str, Any]],
    earnings_events: dict[str, dict[str, Any]] | None,
    log_print: LogFn | None = None,
) -> dict[str, Any]:
    """Return earnings-aware evaluations for calendar spread candidates."""
    logger = log_print or (lambda msg: print(msg, flush=True))
    earnings_events = earnings_events or {}

    if not getattr(config, "EARNINGS_CALENDAR_STRATEGY_ENABLED", True):
        logger("Earnings Calendar Strategy v1 disabled by EARNINGS_CALENDAR_STRATEGY_ENABLED=false.")
        return {
            "source": "earnings_calendar_strategy_v1",
            "enabled": False,
            "has_data": False,
            "items": [],
            "summary": _summary([]),
        }

    if not calendar_candidates:
        logger("Earnings Calendar Strategy v1: no calendar candidates to evaluate.")
        return {
            "source": "earnings_calendar_strategy_v1",
            "enabled": True,
            "has_data": False,
            "items": [],
            "summary": _summary([]),
        }

    evaluations: StrategyRows = []
    for candidate in calendar_candidates:
        evaluations.append(_evaluate_candidate(candidate, earnings_events.get(str(candidate.get("ticker") or "").upper())))

    evaluations.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
    logger(
        "Earnings Calendar Strategy v1 evaluated "
        f"{len(evaluations)} candidate(s); "
        f"{sum(1 for item in evaluations if item.get('is_preferred_setup'))} preferred setup(s), "
        f"{sum(1 for item in evaluations if item.get('urgent_review'))} urgent review."
    )

    return {
        "source": "earnings_calendar_strategy_v1",
        "enabled": True,
        "has_data": bool(evaluations),
        "items": evaluations,
        "summary": _summary(evaluations),
    }


def _evaluate_candidate(candidate: dict[str, Any], event: dict[str, Any] | None) -> dict[str, Any]:
    ticker = str(candidate.get("ticker") or "UNKNOWN").upper()
    base_score = _float_or_none(candidate.get("score")) or 0.0
    score = min(100.0, max(0.0, base_score))
    reasons: list[str] = []
    risks: list[str] = []
    earnings_trust = normalize_earnings_trust(event)

    front_exp = _parse_date(candidate.get("front_expiration"))
    back_exp = _parse_date(candidate.get("back_expiration"))
    earnings_date = _parse_date((event or {}).get("earnings_date") or (event or {}).get("date"))
    today = date.today()

    relation = "unknown"
    is_preferred = False
    urgent = False
    event_captured = False
    short_spans_event = False

    if candidate.get("score") is not None:
        reasons.append(f"Base calendar structure score from Tradier scanner: {base_score:.1f}.")

    _evt_confidence = (event or {}).get("earnings_date_confidence") or (event or {}).get("date_confidence") or "unknown"
    _evt_conflict = bool((event or {}).get("date_conflict") or (event or {}).get("earnings_source_conflict"))
    _date_conflict_cap: float | None = None
    if _evt_conflict:
        _date_conflict_cap = 40.0
        risks.append(f"Earnings date disputed between providers (confidence={_evt_confidence}); date may be incorrect.")
    elif _evt_confidence == "single_source":
        risks.append("Earnings date from single source only — confirm before entry.")

    if not event or not event.get("has_data") or earnings_date is None:
        relation = "earnings_unknown"
        score = min(score, float(config.EARNINGS_CALENDAR_UNKNOWN_TIMESTAMP_SCORE_CAP))
        risks.append("No confirmed upcoming earnings timestamp for this ticker; treat as a regular calendar, not an earnings calendar.")
        action = "MANUAL REVIEW / TIMESTAMP NEEDED"
        next_check = "Confirm earnings date/session before using this as an earnings-calendar trade."
    else:
        session = str(event.get("time_of_day") or "unknown")
        session_label = str(event.get("session_label") or "Unknown")
        dte = (earnings_date - today).days
        event_captured = bool(back_exp and earnings_date <= back_exp)

        reasons.append(f"Earnings timestamp available: {earnings_date.isoformat()} / {session_label}.")

        if dte <= int(config.EARNINGS_CALENDAR_URGENT_DTE):
            urgent = True
            risks.append("Earnings are today/very soon; live quotes and event timing require manual review before entry.")

        if front_exp is None or back_exp is None:
            relation = "missing_expiration"
            score = min(score, 45.0)
            risks.append("Could not parse front/back expiration dates; cannot validate earnings placement.")
            action = "AVOID / BAD DATE DATA"
            next_check = "Fix expiration parsing before considering this candidate."

        elif earnings_date < today:
            relation = "already_reported"
            score = min(score, 45.0)
            risks.append("Most recent earnings event appears to be in the past; this is no longer an earnings-calendar setup.")
            action = "NOT AN EARNINGS SETUP"
            next_check = "Wait for the next confirmed earnings event."

        elif earnings_date > back_exp:
            relation = "earnings_after_back_leg"
            score = min(score, 50.0)
            risks.append("Earnings are after both legs; this candidate does not capture the earnings catalyst.")
            action = "REGULAR CALENDAR ONLY"
            next_check = "Use only as a non-earnings calendar, or rescan with later expirations."

        elif earnings_date > front_exp and earnings_date <= back_exp:
            relation = "long_leg_captures_earnings"
            is_preferred = True
            score += float(config.EARNINGS_CALENDAR_PREFERRED_BONUS)
            reasons.append("Preferred structure: short front leg expires before earnings while long back leg captures the event window.")
            action = "EARNINGS CALENDAR CANDIDATE"
            next_check = "Verify live bid/ask, earnings timestamp, and intended max debit before entry."

        elif earnings_date == front_exp:
            relation = "earnings_on_front_expiration"
            if session == "after_close":
                # This can be a valid structure, but it is operationally sensitive.
                urgent = True
                short_spans_event = False
                score = min(score + 4.0, 85.0)
                reasons.append("Earnings are after close on the short-leg expiration date; front leg may expire before the event, but timing must be verified.")
                risks.append("Same-day expiration/event timing is operationally sensitive; after-hours exercise/assignment nuances require manual review.")
                action = "URGENT REVIEW / TIMING-SENSITIVE"
                next_check = "Confirm expiration cutoff, earnings release timing, and whether the short leg should be closed before market close."
            else:
                short_spans_event = True
                score = min(score, float(config.EARNINGS_CALENDAR_SHORT_SPANS_EVENT_SCORE_CAP))
                risks.append("Earnings occur on the short-leg expiration date before/during/unknown session; short leg may carry event risk.")
                action = "AVOID / SHORT LEG EVENT RISK"
                next_check = "Use a front expiration before the earnings event or wait for another setup."

        elif earnings_date < front_exp:
            expiry_gap = (front_exp - earnings_date).days
            step_window = int(getattr(config, "CALENDAR_SHORT_LEG_STEP_WINDOW_DAYS", 10) or 10)
            if expiry_gap <= step_window:
                relation = "near_miss_expiry_gap"
                score = min(score, 55.0)
                risks.append(f"Nearest expiry {front_exp.isoformat()} is {expiry_gap}d after earnings — holiday or weekly gap. Manual evaluation recommended.")
                action = "NEAR_MISS / EXPIRY_GAP"
                next_check = "Verify whether the gap is due to a holiday; if so, this may still be a viable earnings calendar with elevated front-leg event risk."
            else:
                relation = "short_leg_spans_earnings"
                short_spans_event = True
                score = min(score, float(config.EARNINGS_CALENDAR_SHORT_SPANS_EVENT_SCORE_CAP))
                risks.append("Short front leg spans the earnings event; this is not the preferred long-calendar earnings structure.")
                action = "AVOID / SHORT LEG EVENT RISK"
                next_check = "Look for a front expiration before earnings, or explicitly treat this as a high-risk event-volatility trade."

        else:
            relation = "unclassified"
            score = min(score, 55.0)
            risks.append("Could not classify the relationship between earnings date and option expirations.")
            action = "MANUAL REVIEW"
            next_check = "Review earnings date versus both expirations manually."

        if not event.get("is_timestamp_confirmed"):
            score = min(score, float(config.EARNINGS_CALENDAR_UNCONFIRMED_SCORE_CAP))
            risks.append("Earnings session is not confirmed; score is capped until timestamp is verified.")

        if urgent and action == "EARNINGS CALENDAR CANDIDATE":
            action = "URGENT REVIEW / EARNINGS SOON"
            next_check = "Manual review required before entry because earnings are very close."

    score = _apply_candidate_risk_caps(score, candidate, risks)
    if _date_conflict_cap is not None:
        score = min(score, _date_conflict_cap)
    score = round(max(0.0, min(100.0, score)), 1)

    trust_label = earnings_trust["earnings_trust_label"]
    if trust_label == "conflict_do_not_trade" and not config.EARNINGS_TRUST_CONFLICT_CAN_PASS:
        action = "FAIL / EARNINGS DATE CONFLICT"
        next_check = earnings_trust["earnings_trust_reason"]
        is_preferred = False
        score = min(score, 35.0)
    elif trust_label == "unknown_research_only" and not config.EARNINGS_TRUST_UNKNOWN_CAN_PASS and action in {"EARNINGS CALENDAR CANDIDATE", "URGENT REVIEW / EARNINGS SOON", "URGENT REVIEW / TIMING-SENSITIVE"}:
        action = "FAIL / EARNINGS DATE UNKNOWN"
        next_check = earnings_trust["earnings_trust_reason"]
        is_preferred = False
        score = min(score, 35.0)
    elif trust_label == "single_source_verify" and config.EARNINGS_TRUST_REQUIRE_MULTI_SOURCE_FOR_CALENDAR_PASS and action == "EARNINGS CALENDAR CANDIDATE":
        action = "WATCH / VERIFY EARNINGS DATE"
        next_check = earnings_trust["earnings_trust_reason"]
        is_preferred = False

    compact_earnings = _compact_event(event)
    max_spread = _float_or_none(candidate.get("max_leg_spread_pct"))
    min_oi = _int_or_zero(candidate.get("min_leg_open_interest"))
    min_vol = _int_or_zero(candidate.get("min_leg_volume"))
    debit_pct = _float_or_none(candidate.get("debit_pct_underlying"))
    iv_edge = _float_or_none(candidate.get("iv_edge"))
    _max_spread_thresh = float(getattr(config, "CALENDAR_MAX_LEG_SPREAD_PCT", 10.0))
    _min_oi_thresh = int(getattr(config, "CALENDAR_MIN_OPEN_INTEREST", 10))
    _min_vol_thresh = int(getattr(config, "CALENDAR_MIN_VOLUME", 5))
    _max_debit_pct = float(getattr(config, "CALENDAR_MAX_DEBIT_PCT_UNDERLYING", 4.0))
    calendar_entry_allowed = action in ("EARNINGS CALENDAR CANDIDATE", "URGENT REVIEW / EARNINGS SOON", "URGENT REVIEW / TIMING-SENSITIVE")
    liquidity_status = "pass" if (
        (max_spread is None or max_spread <= _max_spread_thresh) and
        min_oi >= _min_oi_thresh and min_vol >= _min_vol_thresh
    ) else "fail"
    spread_status = "pass" if (max_spread is None or max_spread <= _max_spread_thresh) else "fail"
    debit_status = "pass" if (debit_pct is None or debit_pct <= _max_debit_pct) else "fail"
    iv_relationship_status = (
        "favorable" if (iv_edge is not None and iv_edge > 0.02) else
        ("neutral" if (iv_edge is not None and iv_edge >= -0.02) else
         ("unfavorable" if iv_edge is not None else "unavailable"))
    )
    row = {
        "strategy_id": "earnings_calendar",
        "ticker": ticker,
        "strategy": "Earnings Long Call Calendar",
        "score": score,
        "action": action,
        "underlying_price": candidate.get("underlying_price"),
        "earnings_date": compact_earnings.get("earnings_date"),
        "earnings_time": compact_earnings.get("time_of_day") or compact_earnings.get("earnings_time"),
        "earnings_source": compact_earnings.get("source"),
        "earnings_sources_seen": compact_earnings.get("date_sources") or [],
        "earnings_trust_label": earnings_trust.get("earnings_trust_label"),
        "date_confidence": compact_earnings.get("date_confidence"),
        "strike": candidate.get("strike"),
        "option_type": candidate.get("option_type"),
        "front_expiration": candidate.get("front_expiration"),
        "back_expiration": candidate.get("back_expiration"),
        "front_dte": candidate.get("front_dte"),
        "back_dte": candidate.get("back_dte"),
        "conservative_debit": candidate.get("conservative_debit"),
        "mid_debit": candidate.get("mid_debit"),
        "debit_pct_underlying": candidate.get("debit_pct_underlying"),
        "max_leg_spread_pct": candidate.get("max_leg_spread_pct"),
        "min_leg_volume": candidate.get("min_leg_volume"),
        "min_leg_open_interest": candidate.get("min_leg_open_interest"),
        "front_iv": candidate.get("front_iv"),
        "back_iv": candidate.get("back_iv"),
        "iv_edge": candidate.get("iv_edge"),
        "short_front_leg": candidate.get("short_front_leg") or {},
        "long_back_leg": candidate.get("long_back_leg") or {},
        "earnings": compact_earnings,
        "earnings_date_confidence": compact_earnings.get("earnings_date_confidence"),
        "date_conflict": compact_earnings.get("date_conflict", False),
        "date_sources": compact_earnings.get("date_sources", []),
        "earnings_date_warning": compact_earnings.get("earnings_date_warning"),
        **earnings_trust,
        "earnings_relation": relation,
        "event_captured_by_back_leg": event_captured,
        "short_leg_spans_earnings": short_spans_event,
        "is_preferred_setup": is_preferred,
        "urgent_review": urgent,
        "reasons": reasons,
        "risks": risks,
        "next_check": next_check,
        "base_calendar_candidate": candidate,
        "expiration_pair_diagnostics": candidate.get("expiration_pair_diagnostics") or {},
        # 29.8: normalized compact fields for pre-30A readiness
        "calendar_entry_allowed": calendar_entry_allowed,
        "liquidity_status": liquidity_status,
        "spread_status": spread_status,
        "debit_status": debit_status,
        "iv_relationship_status": iv_relationship_status,
        "structure_status": relation,
    }
    normalize_strategy_row(row, "earnings_calendar")
    try:
        from app.strategies.earnings_calendar_universal import build_earnings_calendar_universal_row
        build_earnings_calendar_universal_row(row)
    except Exception:
        pass  # universal enrichment is additive; never block legacy output
    return row


def _apply_candidate_risk_caps(score: float, candidate: dict[str, Any], risks: list[str]) -> float:
    """Apply hard caps for option-market quality issues."""
    max_spread = _float_or_none(candidate.get("max_leg_spread_pct"))
    min_oi = _int_or_zero(candidate.get("min_leg_open_interest"))
    min_vol = _int_or_zero(candidate.get("min_leg_volume"))
    debit_pct = _float_or_none(candidate.get("debit_pct_underlying"))

    if max_spread is not None and max_spread > float(config.CALENDAR_MAX_LEG_SPREAD_PCT):
        score = min(score, 60.0)
        risks.append("Option bid/ask spread is wider than the preferred calendar threshold.")
    if min_oi < int(config.CALENDAR_MIN_OPEN_INTEREST):
        score = min(score, 65.0)
        risks.append("One or both legs have weak open interest for an earnings-calendar trade.")
    if min_vol < int(config.CALENDAR_MIN_VOLUME):
        score = min(score, 70.0)
        risks.append("One or both legs have weak same-day volume.")
    if debit_pct is not None and debit_pct > float(config.CALENDAR_MAX_DEBIT_PCT_UNDERLYING):
        score = min(score, 65.0)
        risks.append("Debit is high relative to underlying price.")
    return score


def _summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "candidate_count": len(items),
        "preferred_count": sum(1 for item in items if item.get("is_preferred_setup")),
        "urgent_count": sum(1 for item in items if item.get("urgent_review")),
        "avoid_count": sum(1 for item in items if str(item.get("action") or "").upper().startswith("AVOID")),
        "manual_review_count": sum(1 for item in items if "REVIEW" in str(item.get("action") or "").upper()),
        "has_candidates": bool(items),
    }


def _compact_event(event: dict[str, Any] | None) -> dict[str, Any]:
    if not event:
        return {"has_data": False, "error": "No earnings event available."}
    confidence = event.get("earnings_date_confidence") or "unknown"
    result = {
        "has_data": bool(event.get("has_data")),
        "ticker": event.get("ticker") or event.get("symbol"),
        "earnings_date": event.get("earnings_date") or event.get("date"),
        "date": event.get("earnings_date") or event.get("date"),
        "time_of_day": event.get("time_of_day"),
        "session_label": event.get("session_label"),
        "is_timestamp_confirmed": bool(event.get("is_timestamp_confirmed")),
        "earnings_date_confidence": confidence,
        "date_confidence": event.get("date_confidence") or confidence,
        "date_conflict": bool(event.get("date_conflict") or event.get("earnings_source_conflict")),
        "date_sources": list(event.get("date_sources") or event.get("sources_seen") or []),
        "days_until_earnings": event.get("days_until_earnings"),
        "source": event.get("source"),
        "error": event.get("error"),
    }
    if confidence == "disputed":
        result["earnings_date_warning"] = "date disputed between providers"
    return result


def _parse_date(value: Any) -> date | None:
    try:
        if value in {None, ""}:
            return None
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


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
