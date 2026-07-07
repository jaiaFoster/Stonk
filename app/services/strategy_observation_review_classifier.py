"""30C — Deterministic classification helpers for observation review categories.

All functions are pure (no I/O, no side effects) and fully testable.
Used by strategy_observation_review_service to tag blockers, queue items,
movement, and review priorities consistently.
"""

from __future__ import annotations

from typing import Any

# (keyword_tuple, suggested_category) — first match wins.
_BLOCKER_CATEGORY_RULES: list[tuple[tuple[str, ...], str]] = [
    (("DATA_UNAVAILABLE", "MISSING_DATA", "NO_DATA", "DATA_GAP",
      "EARNINGS_SOURCE", "EARNINGS_TRUST", "EARNINGS_DATE", "NO_ELIGIBLE_EXPIRATION",
      "COVERAGE_GAP", "CHAIN_MISSING", "MISSING_CHAIN"), "data_gap"),
    (("ILLIQUID", "LIQUIDITY", "WIDE_SPREAD", "SPREAD_TOO_WIDE",
      "SPREAD_WIDTH", "THIN_MARKET"), "liquidity_constraint"),
    (("EXPIRY_GAP", "EXPIRATION_PAIR", "NO_ELIGIBLE_EXPIR", "STRUCTURE_FAILED",
      "STRUCTURE_GAP", "CHAIN_REJECTED", "NO_CALENDAR_ENTRY",
      "CALENDAR_STRUCTURE", "PAIR_DIAGNOSTIC"), "structure_gap"),
    (("PROVIDER_BUDGET", "PAYLOAD_BUDGET", "DEV_CAP", "SKIPPED_DEV_CAP",
      "CANDIDATE_CAP", "UNIVERSE_CAP", "MAX_CANDIDATES"), "provider_budget"),
    (("THRESHOLD", "SCORE_BELOW", "MOMENTUM_WEAK", "SKEW_UNFAVORABLE",
      "IV_UNFAVORABLE", "DEBIT_TOO_HIGH", "REWARD_RISK", "SIGNAL_WEAK"), "strategy_threshold"),
    (("CONFIG", "CONFIGURATION", "DISABLED", "DRY_RUN", "FF_DRY"),
     "configuration"),
]

_BUCKET_RANK: dict[str, int] = {
    "pass": 5,
    "watch": 4,
    "unknown": 3,
    "dry_run": 2,
    "skipped": 1,
    "fail": 0,
    "error": -1,
}


def classify_blocker_category(gate_reason: str, gate_id: str = "") -> str:
    """Map a blocking gate reason/id to a review category."""
    text = (gate_reason + " " + gate_id).upper()
    for keywords, category in _BLOCKER_CATEGORY_RULES:
        if any(kw in text for kw in keywords):
            return category
    # Gate-id soft signals
    gate_u = gate_id.upper()
    if any(k in gate_u for k in ("EARNINGS", "TRUST", "DATE", "SOURCE", "COVERAGE")):
        return "data_gap"
    if any(k in gate_u for k in ("EXPIR", "CALENDAR", "STRUCTURE", "PAIR")):
        return "structure_gap"
    if any(k in gate_u for k in ("LIQUID", "SPREAD", "IV", "CHAIN")):
        return "liquidity_constraint"
    return "unknown"


def classify_review_type(
    status_bucket: str,
    verdict: str,
    primary_reason: str,
    gate_id: str = "",
    strategy_id: str = "",
) -> str:
    """Assign a review_type string for a queue item or observation."""
    verdict_u = (verdict or "").upper()
    reason_u = (primary_reason or "").upper()
    gate_u = (gate_id or "").upper()

    if "NEAR_MISS" in verdict_u or "NEAR_MISS" in reason_u or "NEAR MISS" in reason_u:
        return "repeated_near_miss"
    if "CROSS" in reason_u and "CONFIRM" in reason_u:
        return "cross_strategy_confirmation"
    if strategy_id == "forward_factor_calendar":
        return "ff_research_candidate"
    if status_bucket == "pass":
        return "pass_candidate"
    if any(k in reason_u for k in ("DATA", "MISSING", "UNAVAILABLE", "GAP", "NO_DATA")):
        return "data_quality_gap"
    if any(k in reason_u for k in ("PROVIDER", "BUDGET", "DEV_CAP", "CAP")):
        return "provider_budget_gap"
    if "LIFECYCLE" in reason_u or "LIFECYCLE" in gate_u:
        return "lifecycle_consistency_check"
    if "RISK" in reason_u and "PORTFOLIO" in reason_u:
        return "portfolio_risk_signal"
    if status_bucket in ("fail", "watch") and gate_u:
        return "repeated_blocker"
    return "unknown"


def classify_review_priority(
    status_bucket: str,
    obs_count: int,
    strategy_count: int,
    review_type: str,
) -> str:
    """Assign review_priority based on recurrence and signal strength."""
    if review_type == "cross_strategy_confirmation" and strategy_count >= 2:
        return "high"
    if review_type == "repeated_near_miss" and obs_count >= 2:
        return "high"
    if status_bucket == "pass" and strategy_count >= 2:
        return "high"
    if status_bucket == "pass":
        return "medium"
    if status_bucket == "watch" and obs_count >= 2:
        return "medium"
    if review_type == "ff_research_candidate" and obs_count >= 2:
        return "medium"
    if review_type in ("provider_budget_gap", "configuration"):
        return "ignore"
    if status_bucket in ("fail", "skipped", "error"):
        return "low"
    return "low"


def classify_movement(
    prev_bucket: str | None,
    curr_bucket: str | None,
    prev_blocking: int = 0,
    curr_blocking: int = 0,
    prev_quality: str | None = None,
    curr_quality: str | None = None,
) -> tuple[str, str]:
    """Return (movement_category, movement_reason) for a candidate across two runs."""
    _QUAL_RANK = {"good": 3, "ok": 2, "unknown": 1, "poor": 0, "missing": -1}

    if prev_bucket is None and curr_bucket is not None:
        return "new", "No prior observation for this candidate"
    if prev_bucket is not None and curr_bucket is None:
        return "disappeared", "Candidate absent from current run"
    if prev_bucket is None and curr_bucket is None:
        return "unknown", "No data in either run"

    prev_rank = _BUCKET_RANK.get(prev_bucket or "", 3)
    curr_rank = _BUCKET_RANK.get(curr_bucket or "", 3)

    if curr_rank > prev_rank:
        return "improved", f"{prev_bucket} → {curr_bucket}"
    if curr_rank < prev_rank:
        return "degraded", f"{prev_bucket} → {curr_bucket}"

    # Same bucket — check gate count and data quality
    if curr_blocking < prev_blocking:
        return "improved", "Blocking gate count decreased"
    if curr_blocking > prev_blocking:
        return "degraded", "Blocking gate count increased"

    prev_q = _QUAL_RANK.get(prev_quality or "", 1)
    curr_q = _QUAL_RANK.get(curr_quality or "", 1)
    if curr_q > prev_q:
        return "improved", "Data quality improved"
    if curr_q < prev_q:
        return "degraded", "Data quality worsened"

    return "unchanged", f"Same status bucket ({curr_bucket}) and gate profile"
