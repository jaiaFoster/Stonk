"""
app/services/calendar_ranking_service.py — Calendar Ranking v2.

This layer sits after the existing earnings-calendar strategy. It does not
place trades. It creates a stricter, more explicit ranking object that answers:

- Does this candidate pass the core earnings-calendar requirements?
- Is today inside the preferred entry window?
- Is liquidity/debit/IV good enough to deserve deeper historical review?

Only candidates passing the strict criteria gate are marked backtest_eligible.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable

from app import config
from app.services.calendar_verdict_service import attach_final_verdicts_to_ranking

LogFn = Callable[[str], None]


def build_calendar_ranking(
    calendar_candidates: list[dict[str, Any]] | None,
    earnings_calendar_strategy: dict[str, Any] | None,
    log_print: LogFn | None = None,
    account_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    logger = log_print or (lambda msg: print(msg, flush=True))
    candidates = [c for c in (calendar_candidates or []) if isinstance(c, dict)]
    strategy_items = [s for s in ((earnings_calendar_strategy or {}).get("items", []) or []) if isinstance(s, dict)]
    strategy_by_ticker = {str(s.get("ticker") or "").upper().strip(): s for s in strategy_items if str(s.get("ticker") or "").strip()}

    result: dict[str, Any] = {
        "source": "calendar_ranking_v2",
        "enabled": True,
        "has_data": False,
        "items": [],
        "eligible_for_backtest": [],
        "summary": {},
        "errors": [],
    }

    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        ticker = str(candidate.get("ticker") or "").upper().strip()
        strategy = strategy_by_ticker.get(ticker, {})
        rows.append(_rank_candidate(candidate, strategy, account_context=account_context))

    rows.sort(key=lambda row: float(row.get("rank_score") or 0), reverse=True)
    eligible = [row for row in rows if row.get("passes_all_criteria") and row.get("backtest_eligible")]

    result["items"] = rows
    result["eligible_for_backtest"] = eligible[: max(1, int(getattr(config, "CALENDAR_BACKTEST_MAX_CANDIDATES", 3) or 3))]
    result["has_data"] = bool(rows)
    result["summary"] = {
        "candidate_count": len(rows),
        "pass_count": sum(1 for row in rows if row.get("passes_all_criteria")),
        "backtest_eligible_count": len(eligible),
        "ideal_entry_count": sum(1 for row in rows if row.get("entry_timing") == "IDEAL"),
        "late_count": sum(1 for row in rows if row.get("entry_timing") == "LATE"),
    }
    attach_final_verdicts_to_ranking(result, account_context=account_context)

    logger(
        "Calendar Ranking v2 ranked "
        f"{len(rows)} candidate(s); {result['summary']['pass_count']} pass all criteria; "
        f"{len(eligible)} backtest-eligible."
    )
    logger(
        "Calendar Verdict Service: finalized "
        f"{len(rows)} candidate(s); Hard-fail overrides applied: {result['summary'].get('hard_fail_count', 0)}; "
        f"Trade-type classification completed: {len(rows)}."
    )
    return result


def _rank_candidate(candidate: dict[str, Any], strategy: dict[str, Any], account_context: dict[str, Any] | None = None) -> dict[str, Any]:
    ticker = str(candidate.get("ticker") or strategy.get("ticker") or "UNKNOWN").upper().strip()
    score = float(strategy.get("score") if strategy.get("score") is not None else candidate.get("score") or 0)
    reasons: list[str] = []
    risks: list[str] = []
    criteria: list[dict[str, Any]] = []

    dte = _event_dte(candidate, strategy)
    timing_score, timing_label, timing_detail = _entry_timing_score(dte)
    criteria.append(_criterion("Entry window", timing_label != "TOO_EARLY", timing_detail, status="PASS" if timing_label == "IDEAL" else "WARN" if timing_label in {"EARLY", "LATE"} else "FAIL"))

    is_preferred = bool(strategy.get("is_preferred_setup"))
    timing_payload = candidate.get("earnings_timing") if isinstance(candidate.get("earnings_timing"), dict) else {}
    captures_event = bool(timing_payload.get("captures_event")) or is_preferred
    criteria.append(_criterion("Earnings placement", captures_event, "Short leg expires before earnings and long leg remains open after earnings." if captures_event else str(strategy.get("next_check") or "Candidate does not cleanly capture the earnings event.")))

    confirmed = bool((strategy.get("earnings") or {}).get("is_timestamp_confirmed") or (candidate.get("earnings_event") or {}).get("is_timestamp_confirmed"))
    criteria.append(_criterion("Confirmed timestamp", confirmed, "Provider confirmed earnings session/date." if confirmed else "Earnings timestamp is unconfirmed or unknown.", status="PASS" if confirmed else "WARN"))

    max_spread = _float(candidate.get("max_leg_spread_pct"))
    spread_ok = max_spread is not None and max_spread <= float(config.CALENDAR_MAX_LEG_SPREAD_PCT)
    criteria.append(_criterion("Bid/ask spread", spread_ok, _threshold_detail("Max leg spread", max_spread, float(config.CALENDAR_MAX_LEG_SPREAD_PCT), "max", "%", "limit") if max_spread is not None else "Spread unavailable."))

    min_oi = _float(candidate.get("min_leg_open_interest"))
    oi_ok = min_oi is not None and min_oi >= float(config.CALENDAR_MIN_OPEN_INTEREST)
    criteria.append(_criterion("Open interest", oi_ok, _threshold_detail("Min OI", min_oi, float(config.CALENDAR_MIN_OPEN_INTEREST), "min", "", "minimum") if min_oi is not None else "Open interest unavailable."))

    min_vol = _float(candidate.get("min_leg_volume"))
    vol_ok = min_vol is not None and min_vol >= float(config.CALENDAR_MIN_VOLUME)
    criteria.append(_criterion("Same-day volume", vol_ok, _threshold_detail("Min volume", min_vol, float(config.CALENDAR_MIN_VOLUME), "min", "", "preferred minimum") if min_vol is not None else "Volume unavailable.", status="PASS" if vol_ok else "WARN"))

    # TKT-012: tiered debit cap — sizing gate only, does not hard-fail signal.
    tier_result = candidate.get("debit_cap_tier_result") if isinstance(candidate.get("debit_cap_tier_result"), dict) else {}
    debit_pct = _float(candidate.get("debit_pct_underlying"))
    if tier_result:
        debit_ok = bool(tier_result.get("passes"))
        cap_pct = tier_result.get("cap_pct") or float(config.CALENDAR_MAX_DEBIT_PCT_UNDERLYING)
        tier_label = tier_result.get("tier", "")
        debit_detail = (
            f"Debit {debit_pct:.1f}% of underlying; cap={cap_pct:.0f}% ({tier_label}); {'passes' if debit_ok else 'exceeds cap'}."
            if debit_pct is not None else "Debit percent unavailable."
        )
    else:
        debit_ok = debit_pct is not None and debit_pct <= float(config.CALENDAR_MAX_DEBIT_PCT_UNDERLYING)
        debit_detail = f"Debit {debit_pct:.1f}% of underlying <= {config.CALENDAR_MAX_DEBIT_PCT_UNDERLYING}%" if debit_pct is not None else "Debit percent unavailable."
    criteria.append(_criterion("Debit size", debit_ok, debit_detail, status="PASS" if debit_ok else "WARN"))

    iv_edge = _float(candidate.get("iv_edge"))
    iv_ok = iv_edge is not None and iv_edge >= 0
    criteria.append(
        _criterion(
            "IV relationship",
            iv_ok,
            f"Front IV - back IV = {iv_edge:.2f}" if iv_edge is not None else "IV edge unavailable.",
            status="PASS" if iv_ok else "FAIL" if iv_edge is not None else "WARN",
        )
    )

    hard_fails = [c for c in criteria if c.get("status") == "FAIL"]
    pass_count = sum(1 for c in criteria if c.get("status") == "PASS")
    passes_all = not hard_fails and is_preferred and timing_label in {"IDEAL", "EARLY", "LATE"}

    if timing_label == "IDEAL":
        reasons.append("Today is inside the preferred pre-earnings entry window.")
    elif timing_label == "LATE":
        risks.append("Entry is late; IV may already be elevated and front-leg gamma/expiration risk is higher.")
    elif timing_label == "EARLY":
        risks.append("Entry is early; monitor instead of forcing a trade today.")

    if is_preferred:
        reasons.append("Earnings strategy marked this as a preferred event-capturing calendar structure.")
    else:
        risks.append("Earnings strategy did not mark this as a preferred setup.")

    rank_score = score + timing_score
    if passes_all:
        rank_score += 10
    rank_score -= len(hard_fails) * 12
    rank_score = max(0.0, min(100.0, rank_score))

    min_passed = int(getattr(config, "CALENDAR_RANKING_MIN_PASSED_REQUIREMENTS", 7) or 7)
    min_score = float(getattr(config, "CALENDAR_RANKING_MIN_SCORE_TO_BACKTEST", 70) or 70)
    candle_quality = candidate.get("candle_quality") if isinstance(candidate.get("candle_quality"), dict) else {}
    candle_confidence = str(candle_quality.get("confidence") or "missing").lower()
    candle_backtest_ok = candle_confidence in {"high", "medium"}
    backtest_eligible = bool(passes_all and pass_count >= min_passed and rank_score >= min_score and candle_backtest_ok)
    backtest_blockers: list[str] = []
    if not candle_backtest_ok:
        backtest_blockers.append("insufficient_historical_candle_data")

    action = "PASS / BACKTEST" if backtest_eligible else "PASS / WATCH" if passes_all else "FAIL / DO NOT BACKTEST"
    if timing_label == "LATE" and passes_all:
        action = "PASS / LATE REVIEW"

    return {
        "ticker": ticker,
        "rank_score": round(rank_score, 1),
        "base_score": round(score, 1),
        "action": action,
        "entry_timing": timing_label,
        "days_until_earnings": dte,
        "passes_all_criteria": passes_all,
        "backtest_eligible": backtest_eligible,
        "backtest_eligibility": backtest_eligible,
        "backtest_mode": "eligibility_backtest" if backtest_eligible else "skipped_insufficient_candles" if not candle_backtest_ok else "not_eligible",
        "backtest_blockers": backtest_blockers,
        "candle_quality": candle_quality,
        "candle_provider": candle_quality.get("selected_provider"),
        "passed_requirement_count": pass_count,
        "failed_requirement_count": len(hard_fails),
        "criteria": criteria,
        "candidate": candidate,
        "strategy": strategy,
        "reasons": _dedupe(reasons + list(strategy.get("reasons", []) or [])[:3]),
        "risks": _dedupe(risks + list(strategy.get("risks", []) or [])[:4]),
        "next_check": _next_check(action, timing_label),
    }


def _entry_timing_score(dte: int | None) -> tuple[float, str, str]:
    ideal_min = int(getattr(config, "EARNINGS_CALENDAR_IDEAL_ENTRY_MIN_DTE", 6) or 6)
    ideal_max = int(getattr(config, "EARNINGS_CALENDAR_IDEAL_ENTRY_MAX_DTE", 12) or 12)
    late_dte = int(getattr(config, "EARNINGS_CALENDAR_LATE_ENTRY_DTE", 4) or 4)
    if dte is None:
        return -8.0, "UNKNOWN", "Earnings DTE unavailable."
    if ideal_min <= dte <= ideal_max:
        return 8.0, "IDEAL", f"{dte} DTE is inside the preferred {ideal_min}-{ideal_max} DTE entry window."
    if late_dte < dte < ideal_min:
        return -2.0, "LATE", f"{dte} DTE is slightly late versus the preferred {ideal_min}-{ideal_max} DTE window."
    if dte <= late_dte:
        return -10.0, "LATE", f"{dte} DTE is late; only exceptional liquidity/IV setups should remain under review."
    return -4.0, "EARLY", f"{dte} DTE is early; keep on watch and re-run closer to earnings."


def _event_dte(candidate: dict[str, Any], strategy: dict[str, Any]) -> int | None:
    earnings = strategy.get("earnings") if isinstance(strategy.get("earnings"), dict) else {}
    for source in (earnings, candidate.get("earnings_event") if isinstance(candidate.get("earnings_event"), dict) else {}):
        value = source.get("days_until_earnings")
        if value is not None:
            try:
                return int(float(value))
            except (TypeError, ValueError):
                pass
        event_date = _parse_date(source.get("earnings_date") or source.get("date"))
        if event_date:
            return (event_date - date.today()).days
    return None


def _criterion(name: str, ok: bool, detail: str, status: str | None = None) -> dict[str, Any]:
    return {"name": name, "status": status or ("PASS" if ok else "FAIL"), "detail": str(detail), "ok": bool(ok)}


def _threshold_detail(label: str, value: float, threshold: float, direction: str, suffix: str, threshold_label: str) -> str:
    if direction == "max":
        op = "<=" if value <= threshold else ">"
    else:
        op = ">=" if value >= threshold else "<"
    if suffix == "%":
        left = f"{value:.1f}%"
        right = f"{threshold:g}%"
    else:
        left = f"{value:.0f}"
        right = f"{threshold:g}"
    return f"{label} {left} {op} {right} {threshold_label}."


def _next_check(action: str, timing_label: str) -> str:
    if action == "PASS / BACKTEST":
        return "Run mini-backtest and recheck live bid/ask before any entry."
    if timing_label == "EARLY":
        return "Keep on the upcoming earnings watchlist; re-run inside the preferred entry window."
    if timing_label == "LATE":
        return "Treat as late review only; avoid chasing unless liquidity, IV edge, and risk/reward remain excellent."
    return "Do not backtest or enter until all core calendar criteria pass."


def _parse_date(value: Any) -> date | None:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _float(value: Any) -> float | None:
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _dedupe(items: list[Any]) -> list[str]:
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out
