"""Public screener helper — presentation logic only, no strategy mutation, no private data.

TKT-031A/C/D/E, TKT-032A/B, TKT-035A
"""

from __future__ import annotations

from typing import Any

# TKT-031C: Stock momentum action → public label
_STOCK_MOMENTUM_LABEL: dict[str, str] = {
    "CONSIDER ADDING": "Momentum Pass",
    "ADD ON PULLBACK": "Momentum Pass",
    "WATCH / CONFIRM TREND": "Watch",
    "WATCH / RESEARCH": "Watch",
    "STARTER ONLY / WAIT FOR PULLBACK": "Watch",
    "TACTICAL ONLY / DO NOT CHASE": "Tactical Watch",
    "HOLD / DO NOT ADD": "Tactical Watch",
    "AVOID / WEAK TREND": "Rejected",
}

# TKT-032A: FF source_iv_status → human label
_FF_SOURCE_IV_LABEL: dict[str, str] = {
    "SOURCE_QUALIFIED": "Source qualified",
    "EARNINGS_CONTAMINATED": "Earnings window — IV excluded",
    "SOURCE_UNAVAILABLE": "Source data unavailable",
    "SOURCE_UNSPECIFIED": "Not evaluated this run",
}

# TKT-032A: FF verdict prefixes / data_state → coverage label
_FF_COVERAGE_SKIP_STATES = {"SKIPPED_DEV_CAP", "SKIPPED_STRATEGY_CAP", "SKIPPED_PROVIDER_BUDGET"}
_FF_COVERAGE_SKIP_STAGES = {"cap_skip", "budget_skipped", "recent_fail_skip"}


def public_verdict_label(row: dict[str, Any], strategy_id: str) -> tuple[str, str]:
    """Return (public_label, original_label) for display on /screener.

    Never exposes dev-cap / provider-budget language (TKT-031A).
    """
    original = str(row.get("verdict") or row.get("action") or "")

    if strategy_id == "stock_momentum":
        action = str(row.get("action") or "")
        user_label = _STOCK_MOMENTUM_LABEL.get(action)
        if user_label is None:
            # Fallback: action words → generic bucket
            upper = action.upper()
            if "FAIL" in upper or "AVOID" in upper or "WEAK" in upper:
                user_label = "Rejected"
            elif "TACTICAL" in upper or "HOLD" in upper:
                user_label = "Tactical Watch"
            elif "WATCH" in upper or "CONFIRM" in upper or "RESEARCH" in upper or "STARTER" in upper:
                user_label = "Watch"
            elif "CONSIDER" in upper or "ADD" in upper:
                user_label = "Momentum Pass"
            else:
                user_label = action or "Unknown"
        return user_label, original

    if strategy_id == "forward_factor":
        stage = str(row.get("ff_candidate_stage") or "")
        data_state = str(row.get("data_state") or "")
        verdict = str(row.get("verdict") or "")
        # TKT-031A: replace all coverage-skip language
        if stage in _FF_COVERAGE_SKIP_STAGES or data_state in _FF_COVERAGE_SKIP_STATES:
            return "Skipped by limited scan", original
        if "DEV CAP" in verdict.upper() or "STRATEGY CAP" in verdict.upper() or "PROVIDER BUDGET" in verdict.upper():
            return "Skipped by limited scan", original
        if verdict.startswith("PASS"):
            return "Signal candidate", original
        if verdict.startswith("WATCH"):
            return "Near candidate", original
        if verdict.startswith("FAIL"):
            return "Did not qualify", original
        return verdict or "Unknown", original

    if strategy_id == "calendar":
        action = str(row.get("action") or "")
        upper = action.upper()
        if "PASS" in upper:
            return "Eligible", original
        if "WATCH" in upper:
            return "Watch", original
        if "FAIL" in upper:
            return "Did not qualify", original
        return action or "Unknown", original

    if strategy_id == "skew":
        verdict = str(row.get("verdict") or "")
        if verdict.startswith("PASS"):
            return "Vertical candidate", original
        if verdict.startswith("WATCH"):
            return "Near candidate", original
        if verdict.startswith("FAIL"):
            return "Did not qualify", original
        return verdict or "Unknown", original

    return original or "Unknown", original


def public_daily_opportunity_reason(row: dict[str, Any], strategy_id: str) -> str:
    """TKT-031D: Short human-readable reason why this row cannot enter Daily Opportunity."""
    if strategy_id == "forward_factor":
        return "Forward Factor is in signal-only mode — execution gated for all tickers."

    if strategy_id == "stock_momentum":
        return "Stock-only signal — options execution not applicable."

    if strategy_id == "calendar":
        criteria = row.get("criteria") or []
        fail_codes = {c.get("code") for c in criteria if str(c.get("status") or "").upper() == "FAIL"}
        if "liquidity" in fail_codes:
            return "Options illiquid for this name."
        if "dte" in fail_codes:
            return "Expiration pair outside timing window."
        if "earnings" in fail_codes:
            return "Earnings window conflict."
        action = str(row.get("action") or "").upper()
        if "FAIL" in action:
            return "Calendar criteria not met."
        if "WATCH" in action:
            return "Awaiting confirmation — not yet eligible."
        return "Not in qualifying state."

    if strategy_id == "skew":
        reqs = row.get("requirements") or []
        fail_codes = {r.get("code") for r in reqs if str(r.get("status") or "").upper() == "FAIL"}
        if "liquidity" in fail_codes:
            return "Options illiquid for this name."
        if "no_chain" in fail_codes or "no_vertical" in fail_codes:
            return "No valid vertical available."
        if "data_quality" in fail_codes:
            return "Data quality check failed."
        verdict = str(row.get("verdict") or "")
        if verdict.startswith("WATCH"):
            return "Awaiting confirmation signal."
        if verdict.startswith("FAIL"):
            return "Vertical criteria not met."
        return "Not in qualifying state."

    return "Not eligible for this run."


def build_public_gate_checklist(row: dict[str, Any], strategy_id: str) -> list[dict[str, Any]]:
    """TKT-031E: Universal gate checklist per row.

    Each gate: {"name": str, "status": "pass"|"watch"|"fail"|"unknown"|"not_applicable"|"dry_run"|"skipped", "detail": str}
    """
    if strategy_id == "forward_factor":
        return _ff_gate_checklist(row)

    if strategy_id == "calendar":
        return _calendar_gate_checklist(row)

    if strategy_id == "skew":
        return _skew_gate_checklist(row)

    if strategy_id == "stock_momentum":
        return _stock_gate_checklist(row)

    return [{"name": "Signal check", "status": "unknown", "detail": ""}]


def _ff_gate_checklist(row: dict[str, Any]) -> list[dict[str, Any]]:
    stage = str(row.get("ff_candidate_stage") or "")
    gates = row.get("ff_gates") or {}

    if stage in _FF_COVERAGE_SKIP_STAGES:
        return [
            _gate("Coverage eligibility", "skipped", "Outside limited scan window"),
            _gate("Chain approved", "not_applicable"),
            _gate("Source qualified", "not_applicable"),
            _gate("Diagnostic model", "not_applicable"),
            _gate("Structure built", "not_applicable"),
            _gate("Execution", "dry_run", "Signal-only mode — trade gated"),
        ]

    cheap = bool(gates.get("cheap_eligible", False))
    chain = bool(gates.get("chain_approved", False))
    sq = bool(gates.get("source_qualified", False))
    dm = bool(gates.get("diagnostic_model", False))
    sb = bool(gates.get("structure_built", False))
    contaminated = bool(gates.get("earnings_contaminated", False))

    return [
        _gate("Coverage eligibility", "pass" if cheap else "fail",
              "" if cheap else "Outside scan coverage"),
        _gate("Chain approved",
              "pass" if chain else ("not_applicable" if not cheap else "fail"),
              "" if chain or not cheap else "Options chain not selected"),
        _gate("Source qualified",
              "fail" if contaminated else ("pass" if sq else ("not_applicable" if not chain else "fail")),
              "Earnings contamination detected" if contaminated else (
                  "" if sq or not chain else "IV source not qualified")),
        _gate("Diagnostic model",
              "pass" if dm else ("not_applicable" if not (chain or sq) else "fail"),
              "" if dm or not (chain or sq) else "Diagnostic IV not available"),
        _gate("Structure built",
              "pass" if sb else ("not_applicable" if not (dm or sq) else "fail"),
              "" if sb or not (dm or sq) else "Double-calendar structure unavailable"),
        _gate("Execution", "dry_run", "Signal-only mode — trade gated for all tickers"),
    ]


def _calendar_gate_checklist(row: dict[str, Any]) -> list[dict[str, Any]]:
    criteria = row.get("criteria") or []
    if not criteria:
        action = str(row.get("action") or "").upper()
        overall = "pass" if "PASS" in action else ("watch" if "WATCH" in action else "fail")
        return [_gate("Calendar criteria", overall, action)]
    checklist = []
    for c in criteria:
        name = str(c.get("name") or c.get("code") or "Check")
        raw = str(c.get("status") or "").upper()
        status = "pass" if raw == "PASS" else ("watch" if raw == "WARN" else "fail")
        checklist.append(_gate(name, status, str(c.get("detail") or "")))
    return checklist


def _skew_gate_checklist(row: dict[str, Any]) -> list[dict[str, Any]]:
    reqs = row.get("requirements") or []
    if not reqs:
        verdict = str(row.get("verdict") or "").upper()
        overall = "pass" if verdict.startswith("PASS") else ("watch" if verdict.startswith("WATCH") else "fail")
        return [_gate("Vertical criteria", overall, verdict)]
    checklist = []
    for r in reqs:
        name = str(r.get("name") or r.get("code") or "Check")
        raw = str(r.get("status") or "").upper()
        status = "pass" if raw == "PASS" else "fail"
        checklist.append(_gate(name, status, str(r.get("detail") or "")))
    return checklist


def _stock_gate_checklist(row: dict[str, Any]) -> list[dict[str, Any]]:
    mm = row.get("market_metrics") or {}
    above50 = mm.get("above_sma_50")
    above200 = mm.get("above_sma_200")

    def _bool_status(val: Any) -> str:
        if val is True:
            return "pass"
        if val is False:
            return "fail"
        return "unknown"

    public_label, _ = public_verdict_label(row, "stock_momentum")
    if public_label == "Momentum Pass":
        overall_status = "pass"
    elif public_label in ("Watch", "Tactical Watch"):
        overall_status = "watch"
    else:
        overall_status = "fail"

    return [
        _gate("Above 50-day MA", _bool_status(above50)),
        _gate("Above 200-day MA", _bool_status(above200)),
        _gate("Momentum verdict", overall_status, public_label),
    ]


def _gate(name: str, status: str, detail: str = "") -> dict[str, Any]:
    return {"name": name, "status": status, "detail": detail}


def public_ff_source_label(row: dict[str, Any]) -> str:
    """TKT-032A: Map FF source_iv_status to a public human-readable label."""
    # TKT-031A: never expose dev-cap language
    stage = str(row.get("ff_candidate_stage") or "")
    data_state = str(row.get("data_state") or "")
    verdict = str(row.get("verdict") or "")
    if stage in _FF_COVERAGE_SKIP_STAGES or data_state in _FF_COVERAGE_SKIP_STATES:
        return "Skipped by limited scan"
    if "DEV CAP" in verdict.upper() or "STRATEGY CAP" in verdict.upper() or "PROVIDER BUDGET" in verdict.upper():
        return "Skipped by limited scan"
    if "RECENT REPEAT FAILURE" in verdict.upper():
        return "Skipped — recent failure pattern"

    siv = str(row.get("source_iv_status") or "")
    return _FF_SOURCE_IV_LABEL.get(siv, "Not evaluated this run")


def ff_grouping(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """TKT-032B: Partition FF rows into evaluated / skipped / rejected groups."""
    evaluated: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for row in rows:
        stage = str(row.get("ff_candidate_stage") or "")
        verdict = str(row.get("verdict") or "")
        tier = str(row.get("signal_tier") or "")

        if stage in _FF_COVERAGE_SKIP_STAGES:
            skipped.append(row)
        elif tier in ("SOURCE_QUALIFIED_POSITIVE", "DIAGNOSTIC_POSITIVE", "WATCH_NEAR_POSITIVE"):
            evaluated.append(row)
        elif stage == "selected":
            evaluated.append(row)
        elif "FAIL" in verdict.upper():
            rejected.append(row)
        elif verdict.upper().startswith("WATCH") or verdict.upper().startswith("PASS"):
            evaluated.append(row)
        else:
            rejected.append(row)

    return {"evaluated": evaluated, "skipped": skipped, "rejected": rejected}
