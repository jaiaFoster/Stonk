"""Positive-signal gate for Forward Factor dry-run observations."""

from __future__ import annotations

from typing import Any

from app import config

SOURCE_QUALIFIED_POSITIVE = "SOURCE_QUALIFIED_POSITIVE"
DIAGNOSTIC_POSITIVE = "DIAGNOSTIC_POSITIVE"
WATCH_NEAR_POSITIVE = "WATCH_NEAR_POSITIVE"
NEGATIVE_OR_BLOCKED = "NEGATIVE_OR_BLOCKED"
NOT_EVALUATED = "NOT_EVALUATED"


def evaluate_forward_factor_signal_gate(row: dict[str, Any]) -> dict[str, Any]:
    verdict = str(row.get("verdict") or "")
    if verdict.startswith("SKIPPED"):
        return _gate(NOT_EVALUATED, False, False, verdict, row, blockers=[row.get("primary_blocker") or "Not evaluated."])

    source_ff = _number(row.get("forward_factor"))
    diagnostic_ff = _number(row.get("diagnostic_raw_iv_forward_factor"))
    has_source_iv = source_ff is not None and row.get("front_ex_earnings_iv") is not None and row.get("back_ex_earnings_iv") is not None
    earnings_contaminated = bool(row.get("earnings_contaminated"))
    source_qualified = has_source_iv and not earnings_contaminated
    signal = source_ff if has_source_iv else diagnostic_ff
    if earnings_contaminated:
        source_iv_status = "EARNINGS_CONTAMINATED"
    elif has_source_iv:
        source_iv_status = "SOURCE_QUALIFIED"
    elif diagnostic_ff is not None:
        source_iv_status = "SOURCE_UNAVAILABLE"
    else:
        source_iv_status = "SOURCE_UNSPECIFIED"
    structure_complete = row.get("structure_status") == "COMPLETE"
    liquidity_status = str(row.get("liquidity_status") or "NOT_EVALUATED").upper()
    debit_at_risk = _number(row.get("debit_at_risk"))
    debit_pass = debit_at_risk is not None and debit_at_risk <= config.FF_MAX_DEBIT_DOLLARS
    debit_warning = debit_at_risk is not None and debit_at_risk > config.FF_WARN_DEBIT_DOLLARS
    above_threshold = signal is not None and signal + 1e-12 >= config.FF_MIN_FORWARD_FACTOR
    warnings = []
    blockers = []

    if earnings_contaminated:
        warnings.append(f"Earnings contamination: {row.get('earnings_contamination_reason') or 'expiration overlaps earnings window'}.")
    if not has_source_iv:
        warnings.append("Source-correct ex-earnings IV is unavailable; diagnostic raw-IV FF is not source-qualified.")
    if not above_threshold:
        blockers.append("Forward Factor is below configured threshold or unavailable.")
    if not structure_complete:
        blockers.append(row.get("structure_reason") or "Matched double-calendar structure is unavailable.")
    if liquidity_status == "FAIL":
        blockers.append("Four-leg package liquidity failed configured limits.")
    elif liquidity_status == "WATCH":
        warnings.append("Four-leg liquidity requires review.")
    if debit_at_risk is None:
        blockers.append("Debit at risk is unavailable.")
    elif not debit_pass:
        blockers.append("Debit at risk exceeds configured maximum.")
    elif debit_warning:
        warnings.append("Debit at risk exceeds warning threshold.")

    positive_reasons = []
    if above_threshold:
        positive_reasons.append(f"{'Source-qualified' if source_qualified else 'Diagnostic raw-IV'} Forward Factor is above threshold.")
    if structure_complete:
        positive_reasons.append("Matched approximately ±35-delta double calendar constructed.")
    if liquidity_status == "PASS":
        positive_reasons.append("Liquidity and package slippage pass configured limits.")
    if debit_pass:
        positive_reasons.append("Debit at risk passes configured maximum.")

    positive = above_threshold and structure_complete and liquidity_status == "PASS" and debit_pass and not debit_warning
    near = above_threshold and structure_complete and liquidity_status in {"PASS", "WATCH"} and debit_pass and (liquidity_status == "WATCH" or debit_warning)
    if positive and source_qualified:
        tier, gate_verdict, confidence = SOURCE_QUALIFIED_POSITIVE, "SOURCE-QUALIFIED POSITIVE FF SIGNAL / REVIEW ENTRY", "high"
    elif positive:
        tier, gate_verdict, confidence = DIAGNOSTIC_POSITIVE, "DIAGNOSTIC POSITIVE FF SIGNAL / REVIEW ONLY", "medium"
    elif near:
        tier, gate_verdict, confidence = WATCH_NEAR_POSITIVE, "WATCH / NEAR POSITIVE FF SIGNAL", "low"
    else:
        tier, gate_verdict, confidence = NEGATIVE_OR_BLOCKED, _blocked_verdict(row, above_threshold, liquidity_status, debit_pass), "low"

    return _gate(
        tier, positive, source_qualified, gate_verdict, row, confidence=confidence,
        positive_reasons=positive_reasons, warnings=warnings, blockers=[str(item) for item in blockers if item],
        source_iv_status=source_iv_status,
    )


def _gate(
    tier: str, positive: bool, source_qualified: bool, verdict: str, row: dict[str, Any], *,
    confidence: str = "none", positive_reasons=None, warnings=None, blockers=None, source_iv_status: str = "SOURCE_UNSPECIFIED",
) -> dict[str, Any]:
    signal_score = _signal_score(row, source_qualified)
    blockers = blockers or []
    return {
        "signal_tier": tier, "is_positive_signal": positive, "is_source_qualified": source_qualified,
        "is_diagnostic_only": bool(not source_qualified and row.get("diagnostic_raw_iv_forward_factor") is not None),
        "is_trade_review_candidate": positive and not bool(row.get("earnings_contaminated")),
        "can_enter_daily_opportunity": False,
        "earnings_contaminated": bool(row.get("earnings_contaminated")),
        "source_qualification": row.get("source_qualification"),
        "source_iv_status": source_iv_status, "confidence": confidence, "verdict": verdict,
        "signal_score": signal_score, "actionability_score": 0.0,
        "positive_reasons": positive_reasons or [], "warnings": warnings or [], "blockers": blockers,
        "entry_review": {
            "positive_signal": positive, "source_qualified": source_qualified, "review_only": True,
            "status": "REVIEW ENTRY" if positive else "DO NOT TREAT AS ENTRY CANDIDATE",
            "checks": [
                "Confirm live four-leg bid/ask before entry.",
                "Confirm debit at risk fits account sizing.",
                "Earnings contamination — signal is diagnostic only." if bool(row.get("earnings_contaminated")) else "Diagnostic raw-IV FF is not source-qualified." if not source_qualified else "Confirm source-qualified IV inputs remain current.",
            ],
        },
        "primary_blocker": blockers[0] if blockers else row.get("primary_blocker"),
    }


def _signal_score(row: dict[str, Any], source_qualified: bool) -> float:
    ff = _number(row.get("forward_factor") if source_qualified else row.get("diagnostic_raw_iv_forward_factor")) or 0.0
    strength = min(35.0, max(0.0, ff / max(config.FF_MIN_FORWARD_FACTOR, .001) * 28.0))
    pair = max(0.0, 15.0 - float(row.get("distance_from_target") or 0) * .25)
    structure = 15.0 if row.get("structure_status") == "COMPLETE" else 0.0
    liquidity = 15.0 if row.get("liquidity_status") == "PASS" else 7.5 if row.get("liquidity_status") == "WATCH" else 0.0
    debit = _number(row.get("debit_at_risk"))
    risk = 10.0 if debit is not None and debit <= config.FF_WARN_DEBIT_DOLLARS else 5.0 if debit is not None and debit <= config.FF_MAX_DEBIT_DOLLARS else 0.0
    freshness = 10.0 if ((row.get("data_eligibility") or {}).get("data_state") == "COMPLETE") else 5.0
    return round(strength + pair + structure + liquidity + risk + freshness, 1)


def _blocked_verdict(row: dict[str, Any], above_threshold: bool, liquidity_status: str, debit_pass: bool) -> str:
    existing = str(row.get("verdict") or "")
    if row.get("diagnostic_raw_iv_forward_factor") is None and row.get("forward_factor") is None:
        return existing or "FAIL / NOT A VALID FF SETUP"
    if row.get("structure_status") not in {None, "COMPLETE"}:
        return existing or "FAIL / NOT A VALID FF SETUP"
    if liquidity_status == "FAIL":
        return "FAIL / OPTIONS ILLIQUID"
    if not debit_pass and row.get("debit_at_risk") is not None:
        return "FAIL / DEBIT TOO LARGE"
    if not above_threshold:
        return "FAIL / FORWARD FACTOR BELOW THRESHOLD"
    return existing or "FAIL / NOT A VALID FF SETUP"


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
