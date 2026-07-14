"""Universal strategy row normalization — ASA 30A.

Adds a stable set of normalized fields to every strategy row so all four
strategies expose the same minimal surface. The normalization layer is a thin
wrapper: it reads from existing strategy-specific fields and maps them into
canonical field names. It does not change scoring, thresholds, or strategy
logic.

Pattern: strategy engines call normalize_strategy_row(row, strategy_id) at
the end of their row-building logic. The function mutates `row` in-place and
returns it. All existing fields are preserved.

normalize_strategy_rows(rows, strategy_id) normalizes a list of rows, working
on shallow copies so original strategy state is not mutated.
"""

from __future__ import annotations

from typing import Any

from app.services.strategy_row_schema import (
    STRATEGY_ROW_SCHEMA_VERSION,
    SEMANTIC_FIELDS_VERSION,
    NORMALIZED_ROW_EXCLUDE,
)
from app.services.strategy_gate_service import make_gate, normalize_gate_status


# ─── Legacy skip-stage constants (kept for FF friendly_verdict mapping) ────────

_FF_SKIP_STAGES = frozenset({"cap_skip", "budget_skipped", "recent_fail_skip"})
_FF_SKIP_STATES = frozenset({"SKIPPED_DEV_CAP", "SKIPPED_STRATEGY_CAP", "SKIPPED_PROVIDER_BUDGET"})

_STOCK_ACTION_LABEL: dict[str, str] = {
    "CONSIDER ADDING": "Momentum Pass",
    "ADD ON PULLBACK": "Momentum Pass",
    "WATCH / CONFIRM TREND": "Watch",
    "WATCH / RESEARCH": "Watch",
    "STARTER ONLY / WAIT FOR PULLBACK": "Watch",
    "TACTICAL ONLY / DO NOT CHASE": "Tactical Watch",
    "HOLD / DO NOT ADD": "Tactical Watch",
    "AVOID / WEAK TREND": "Rejected",
    "WATCH / DATA INCOMPLETE": "Watch",
}

_DAILY_OPPORTUNITY_REASON: dict[str, str] = {
    "stock_momentum": "Stock-only signal — options execution not applicable.",
    "forward_factor_calendar": "Forward Factor is in signal-only mode — execution gated for all tickers.",
}

# Stock actions that qualify for Daily Opportunity (add candidates).
_STOCK_DO_ACTIONS = frozenset({"CONSIDER ADDING", "ADD ON PULLBACK"})


# ─── Public API ───────────────────────────────────────────────────────────────


def normalize_strategy_row(
    row: dict[str, Any],
    strategy_id: str,
    spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Add universal normalized fields to a strategy row in-place. Returns row.

    The spec parameter is optional. If not provided, it is looked up from the
    strategy spec registry. Passing spec=None is the normal call pattern.
    """
    if spec is None:
        try:
            from app.services.strategy_spec_registry import get_spec
            spec = get_spec(strategy_id) or {}
        except Exception:
            spec = {}

    row.setdefault("strategy_id", strategy_id)
    row.setdefault("strategy_row_schema_version", STRATEGY_ROW_SCHEMA_VERSION)
    # Strategies that use "action" as their result field (earnings_calendar, stock_momentum)
    # must still expose "verdict" so every normalized row satisfies the universal contract.
    if not row.get("verdict"):
        row["verdict"] = str(row.get("action") or "")

    # Spec metadata — sourced from registry, not invented.
    row.setdefault("strategy_name", spec.get("strategy_name") or strategy_id)
    row.setdefault("strategy_family", spec.get("strategy_family") or "unknown")
    row.setdefault("strategy_goal", spec.get("strategy_goal") or "")

    if "friendly_verdict" not in row:
        row["friendly_verdict"] = _friendly_verdict(row, strategy_id)

    if "primary_reason" not in row:
        row["primary_reason"] = _primary_reason(row, strategy_id)

    # Metrics dict — key numerics/statuses for this strategy.
    if "metrics" not in row:
        row["metrics"] = _metrics(row, strategy_id)

    # Data quality — reflects provider/candle data state.
    if "data_quality" not in row:
        row["data_quality"] = _data_quality(row, strategy_id)

    # Daily Opportunity eligibility.
    if "daily_opportunity_eligible" not in row:
        row["daily_opportunity_eligible"] = _daily_opportunity_eligible(row, strategy_id, spec)

    if strategy_id in _DAILY_OPPORTUNITY_REASON:
        row.setdefault("daily_opportunity_reason", _DAILY_OPPORTUNITY_REASON[strategy_id])
    elif "daily_opportunity_reason" not in row:
        if row["daily_opportunity_eligible"]:
            row["daily_opportunity_reason"] = "Eligible for Daily Opportunity based on strategy result."
        else:
            row["daily_opportunity_reason"] = "Not eligible for Daily Opportunity."

    # Trade policy fields — conservative defaults; FF enforced explicitly.
    dry_run = bool(spec.get("dry_run")) if spec else False
    row.setdefault("dry_run", dry_run)
    row.setdefault("can_trade_live", False)

    if strategy_id == "forward_factor_calendar":
        row.setdefault("can_enter_daily_opportunity", False)
        # Enforce FF policy regardless of spec lookups.
        row["can_trade_live"] = False
        row["dry_run"] = True

    # Capture pre-semantics classification so the invariant survives _decision_semantics overwrite.
    _pre_semantics_decision_class = str(row.get("decision_class") or "")
    semantics = _decision_semantics(row, strategy_id)
    for key, value in semantics.items():
        row[key] = value
    if semantics.get("eligibility_status") not in {"eligible", "conditional"} or semantics.get("action_type") in {"none", "diagnostic"}:
        row["daily_opportunity_eligible"] = False
        row["can_enter_daily_opportunity"] = False
        row["daily_opportunity_reason"] = f"Excluded from Daily Opportunity: {semantics.get('exclusion_reason') or 'not eligible'}."

    # Canonical rejected-row invariant (31B.G): after _decision_semantics, enforce that
    # any row classified as rejected can never leak into eligibility paths.
    _row_type = str(row.get("row_type") or "")
    _verdict_upper = str(row.get("verdict") or "").upper()
    _decision_class = str(row.get("decision_class") or "")
    if (
        _row_type == "rejected_candidate"
        or _verdict_upper.startswith("FAIL")
        or _decision_class == "rejected"
        or _pre_semantics_decision_class == "rejected"
    ):
        # Force trading eligibility flags to False — rejected rows must not leak into daily opportunity.
        row["daily_opportunity_eligible"] = False
        row["can_enter_daily_opportunity"] = False
        # TKT-CALENDAR-REJECTED-ELIGIBILITY: rejected rows ARE valid journal entries (learn from rejections).
        # Only mark journal-eligible when the row has explicit verdict identity; empty rows lack that identity.
        if str(row.get("verdict") or row.get("action") or "").strip():
            row["journal_eligible"] = True
        row["decision_class"] = "rejected"
        # Only override eligibility_status if semantics did not already set a non-eligible value.
        _current_elig = str(row.get("eligibility_status") or "")
        if _current_elig not in {"excluded", "ineligible", "dry_run_excluded", "blocked"}:
            row["eligibility_status"] = "ineligible"
        _action_type = str(row.get("action_type") or "")
        if _action_type in {"entry", "calendar_entry", "vertical_entry", "stock_add"}:
            row["action_type"] = "none"

    row.setdefault("semantic_source", "row")
    row.setdefault("semantic_fields_version", SEMANTIC_FIELDS_VERSION)

    # Gates — canonical gate list using make_gate() shape.
    if "gates" not in row:
        row["gates"] = _gates(row, strategy_id)

    # Journal / observation readiness fields — for 30B.
    row.setdefault("journal_eligible", _journal_eligible(row, strategy_id))
    row.setdefault("observation_key", _observation_key(row, strategy_id))
    row.setdefault("observation_refs", [])

    # 31B.9: Universal scoring — only compute if not already present.
    if "universal_score" not in row:
        try:
            from app.services.universal_scoring_service import compute_universal_score
            _us = compute_universal_score(row, strategy_id)
            for _k, _v in _us.items():
                row.setdefault(_k, _v)
        except Exception:
            pass

    return row


def normalize_strategy_rows(
    rows: list[dict[str, Any]],
    strategy_id: str,
    spec: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Normalize a list of rows, working on shallow copies to avoid mutating originals."""
    result = []
    for row in (rows or []):
        if not isinstance(row, dict):
            continue
        normalized = normalize_strategy_row({**row}, strategy_id, spec=spec)
        # Strip large raw fields from compact normalized output.
        for key in NORMALIZED_ROW_EXCLUDE:
            normalized.pop(key, None)
        result.append(normalized)
    return result


# ─── friendly_verdict ─────────────────────────────────────────────────────────


def _friendly_verdict(row: dict[str, Any], strategy_id: str) -> str:
    if strategy_id == "stock_momentum":
        action = str(row.get("action") or "")
        label = _STOCK_ACTION_LABEL.get(action)
        if label:
            return label
        upper = action.upper()
        if "AVOID" in upper or "FAIL" in upper or "WEAK" in upper:
            return "Rejected"
        if "TACTICAL" in upper or "HOLD" in upper:
            return "Tactical Watch"
        if "WATCH" in upper or "CONFIRM" in upper or "RESEARCH" in upper or "STARTER" in upper:
            return "Watch"
        if "CONSIDER" in upper or "ADD" in upper:
            return "Momentum Pass"
        return action or "Unknown"

    if strategy_id == "skew_momentum_vertical":
        verdict = str(row.get("verdict") or "")
        if verdict.startswith("PASS"):
            return "Vertical candidate"
        if verdict.startswith("WATCH"):
            return "Near candidate"
        if verdict.startswith("FAIL"):
            return "Did not qualify"
        return verdict or "Unknown"

    if strategy_id == "forward_factor_calendar":
        stage = str(row.get("ff_candidate_stage") or "")
        data_state = str(row.get("data_state") or "")
        verdict = str(row.get("verdict") or "")
        if stage in _FF_SKIP_STAGES or data_state in _FF_SKIP_STATES:
            return "Skipped by limited scan"
        upper = verdict.upper()
        if "DEV CAP" in upper or "STRATEGY CAP" in upper or "PROVIDER BUDGET" in upper:
            return "Skipped by limited scan"
        if verdict.startswith("PASS"):
            return "Signal candidate"
        if verdict.startswith("WATCH"):
            return "Near candidate"
        if verdict.startswith("FAIL"):
            return "Did not qualify"
        return verdict or "Unknown"

    if strategy_id == "earnings_calendar":
        action = str(row.get("action") or "")
        entry_status = str(row.get("entry_window_status") or "")
        if entry_status == "ENTRY_WINDOW_CLOSED":
            return "ENTRY WINDOW CLOSED / DO NOT ENTER"
        if entry_status == "SHORT_LEG_SPANS_EARNINGS":
            return "SHORT LEG SPANS EARNINGS / DO NOT ENTER"
        if entry_status == "SHORT_DTE_TOO_LOW":
            return "SHORT DTE TOO LOW / DO NOT ENTER"
        if entry_status == "FRONT_LEG_TOO_DECAYED":
            return "FRONT LEG TOO DECAYED / DO NOT ENTER"
        if entry_status == "NO_PRE_EARNINGS_SHORT_EXPIRY":
            return "NO PRE-EARNINGS SHORT EXPIRY"
        if entry_status == "MONITOR_PRE_WINDOW":
            return "MONITOR / PRE-WINDOW"
        if entry_status == "DATA_NEEDED":
            return "MONITOR / DATA NEEDED"
        if entry_status == "DATE_CONFLICT_REVIEW":
            return "DATE CONFLICT REVIEW"
        upper = action.upper()
        if "EARNINGS CALENDAR CANDIDATE" in upper:
            return "Eligible"
        if "URGENT REVIEW" in upper:
            return "Urgent review"
        if "NEAR_MISS" in upper or "REGULAR CALENDAR" in upper:
            return "Watch"
        if "WATCH" in upper and "VERIFY" in upper:
            return "Watch — verify date"
        if "FAIL" in upper or "AVOID" in upper or "NOT AN EARNINGS" in upper:
            return "Did not qualify"
        if "MANUAL REVIEW" in upper:
            return "Manual review"
        return action or "Unknown"

    return str(row.get("verdict") or row.get("action") or "Unknown")


# ─── primary_reason ───────────────────────────────────────────────────────────


def _primary_reason(row: dict[str, Any], strategy_id: str) -> str:
    if strategy_id == "stock_momentum":
        reasons = row.get("reasons") or []
        if reasons:
            return str(reasons[0])
        blockers = row.get("add_blockers") or []
        if blockers:
            return str(blockers[0])
        return str(row.get("action") or "No data available")

    if strategy_id == "skew_momentum_vertical":
        existing = row.get("primary_reason")
        if existing:
            return str(existing)
        return str(row.get("momentum_reason") or row.get("verdict") or "No reason available")

    if strategy_id == "forward_factor_calendar":
        blocker = row.get("primary_blocker")
        if blocker:
            return str(blocker)
        verdict = str(row.get("verdict") or "")
        if verdict.startswith("PASS"):
            ff = row.get("source_forward_factor") or row.get("forward_factor")
            if ff is not None:
                return f"Forward Factor {float(ff):.4f} above threshold."
        return verdict or "No reason available"

    if strategy_id == "earnings_calendar":
        reasons = row.get("reasons") or []
        if reasons:
            return str(reasons[0])
        return str(row.get("action") or "No reason available")

    existing = row.get("primary_reason") or row.get("primary_blocker")
    if existing:
        return str(existing)
    return str(row.get("verdict") or row.get("action") or "No reason available")


# ─── metrics ──────────────────────────────────────────────────────────────────


def _metrics(row: dict[str, Any], strategy_id: str) -> dict[str, Any]:
    """Extract key metrics from existing strategy-specific fields."""
    if strategy_id == "earnings_calendar":
        return {
            "iv_relationship_status": row.get("iv_relationship_status"),
            "iv_edge": row.get("iv_edge"),
            "debit_status": row.get("debit_status"),
            "debit_pct_underlying": row.get("debit_pct_underlying"),
            "liquidity_status": row.get("liquidity_status"),
            "spread_status": row.get("spread_status"),
            "front_dte": row.get("front_dte"),
            "back_dte": row.get("back_dte"),
            "earnings_trust_label": row.get("earnings_trust_label"),
            "expiration_pair_diagnostics": row.get("expiration_pair_diagnostics"),
            "entry_window_status": row.get("entry_window_status"),
            "entry_window_open": row.get("entry_window_open"),
            "short_leg_dte_minimum": row.get("short_leg_dte_minimum"),
            "entry_window_front_expiration": row.get("entry_window_front_expiration"),
            "entry_window_front_dte": row.get("entry_window_front_dte"),
            "current_dte_to_earnings": row.get("current_dte_to_earnings"),
            "ideal_entry_window": row.get("ideal_entry_window"),
            "estimated_entry_date": row.get("estimated_entry_date"),
            "days_until_entry_window": row.get("days_until_entry_window"),
            "available_expirations": row.get("available_expirations"),
            "short_leg_expires_before_earnings": row.get("short_leg_expires_before_earnings"),
            "short_leg_does_not_span_event": row.get("short_leg_does_not_span_event"),
            "available_pre_earnings_expirations": row.get("available_pre_earnings_expirations"),
            "rejected_expirations": row.get("rejected_expirations"),
            "proposed_short_expiration": row.get("proposed_short_expiration"),
            "proposed_long_expiration": row.get("proposed_long_expiration"),
            "blocker_code": row.get("blocker_code"),
            "blocker_detail": row.get("blocker_detail"),
        }

    if strategy_id == "skew_momentum_vertical":
        return {
            "momentum_status": row.get("momentum_status"),
            "skew_status": row.get("skew_status"),
            "atm_iv": row.get("atm_iv"),
            "spread_width": row.get("spread_width"),
            "estimated_debit": row.get("estimated_debit"),
            "structure_status": row.get("structure_status"),
        }

    if strategy_id == "forward_factor_calendar":
        return {
            "source_forward_factor": row.get("source_forward_factor"),
            "diagnostic_forward_factor": row.get("diagnostic_raw_iv_forward_factor"),
            "front_iv": row.get("front_iv"),
            "back_iv": row.get("back_iv"),
            "ex_earnings_iv": row.get("ex_earnings_iv"),
            "source_qualification": row.get("source_qualification"),
            "source_qualified": row.get("source_qualified"),
            "chain_approved": row.get("chain_approved"),
            "structure_built": row.get("structure_built"),
            "earnings_contaminated": row.get("earnings_contaminated"),
        }

    if strategy_id == "stock_momentum":
        return {
            "momentum_score": row.get("momentum_score") or row.get("score"),
            "relative_strength": row.get("relative_strength"),
            "trend_status": row.get("trend_status"),
            "volume_status": row.get("volume_status"),
            "price_action_status": row.get("price_action_status"),
            "risk_status": row.get("risk_status"),
        }

    return {}


# ─── data_quality ─────────────────────────────────────────────────────────────


def _data_quality(row: dict[str, Any], strategy_id: str) -> str:
    """Infer data quality tier from existing row fields."""
    existing = row.get("data_quality")
    if existing:
        return str(existing)
    # FF-specific data state
    data_state = str(row.get("data_state") or "")
    if data_state in _FF_SKIP_STATES:
        return "limited"
    stage = str(row.get("ff_candidate_stage") or "")
    if stage in _FF_SKIP_STAGES:
        return "limited"
    # Skew requirements failures signal data quality issue
    if strategy_id == "skew_momentum_vertical":
        reqs = row.get("requirements") or []
        dq_fails = [
            r for r in reqs if isinstance(r, dict)
            and str(r.get("code") or "") == "data_quality"
            and str(r.get("status") or "").upper() == "FAIL"
        ]
        if dq_fails:
            return "degraded"
    # Calendar trust label
    if strategy_id == "earnings_calendar":
        trust = str(row.get("earnings_trust_label") or "")
        if trust == "conflict_do_not_trade":
            return "conflict"
        if trust == "single_source_verify":
            return "limited"
        if trust in ("confirmed", "multi_source", "multi_source_confirmed"):
            return "good"
    return "unknown"


# ─── daily_opportunity_eligible ───────────────────────────────────────────────


def _daily_opportunity_eligible(
    row: dict[str, Any], strategy_id: str, spec: dict[str, Any]
) -> bool:
    """Determine if this row is eligible for Daily Opportunity.

    Reflects existing row logic — does not change eligibility rules.
    """
    # FF: always excluded by policy.
    if strategy_id == "forward_factor_calendar":
        return False

    # Use spec to block strategies not allowed in DO.
    if not spec.get("daily_opportunity_allowed", True):
        return False

    if strategy_id == "earnings_calendar":
        verdict = str(row.get("verdict") or row.get("action") or "").upper()
        if verdict.startswith("FAIL") or verdict.startswith("AVOID") or "NOT AN EARNINGS SETUP" in verdict:
            return False
        if str(row.get("trade_verdict") or "").upper() == "PASS" and bool(row.get("entry_allowed")) and str(row.get("recommended_action") or "").upper() == "ENTER":
            return True
        return bool(row.get("calendar_entry_allowed"))

    if strategy_id == "skew_momentum_vertical":
        verdict = str(row.get("verdict") or "")
        return verdict.startswith("PASS")

    if strategy_id == "stock_momentum":
        action = str(row.get("action") or "")
        return action in _STOCK_DO_ACTIONS

    return False


def _decision_semantics(row: dict[str, Any], strategy_id: str) -> dict[str, Any]:
    verdict = str(row.get("verdict") or row.get("action") or "")
    upper = verdict.upper()
    row_type = str(row.get("row_type") or row.get("type") or "")
    status = str(row.get("entry_window_status") or "")

    if row.get("decision_class") and row.get("action_type") and row.get("eligibility_status"):
        return {
            "decision_class": row.get("decision_class"),
            "action_type": row.get("action_type"),
            "actionability": row.get("actionability") or "review_only",
            "eligibility_status": row.get("eligibility_status"),
            "eligibility_reason": row.get("eligibility_reason") or "",
            "exclusion_reason": row.get("exclusion_reason") or "",
            "priority_tier": row.get("priority_tier") or "normal",
            "review_status": row.get("review_status") or "ready",
        }

    if strategy_id == "forward_factor_calendar":
        # 31B.8: PASS/WATCH rows surface as research signals in Daily Opportunity (clearly labeled dry-run).
        # FAIL/diagnostic rows remain excluded.
        _upper = upper
        _is_ff_pass = "PASS" in _upper and "FAIL" not in _upper
        # Only actionable WATCH verdicts are research signals; diagnostic WATCHes (e.g., EX-EARNINGS IV UNAVAILABLE) remain excluded.
        _ACTIONABLE_FF_WATCHES = ("WATCH / FORWARD FACTOR NEAR THRESHOLD", "WATCH / LIQUIDITY DATA PARTIAL", "WATCH / DEBIT ELEVATED")
        _is_ff_watch = (
            any(_upper.startswith(pfx) for pfx in _ACTIONABLE_FF_WATCHES)
            or "DRY RUN PASS" in _upper
            or bool(row.get("watch_zone_ff"))
        )
        _is_ff_near_miss = _upper.startswith("NEAR MISS")
        if _is_ff_pass:
            return {
                "decision_class": "dry_run_entry",
                "action_type": "forward_factor_entry",
                "actionability": "dry_run_only",
                "eligibility_status": "conditional",
                "eligibility_reason": "Forward Factor PASS signal — dry-run only, no execution.",
                "exclusion_reason": "dry_run",
                "priority_tier": "normal",
                "review_status": "review_required",
            }
        if _is_ff_watch:
            return {
                "decision_class": "dry_run_watch",
                "action_type": "forward_factor_watch",
                "actionability": "dry_run_only",
                "eligibility_status": "conditional",
                "eligibility_reason": "Forward Factor WATCH signal — dry-run only, no execution.",
                "exclusion_reason": "dry_run",
                "priority_tier": "low",
                "review_status": "review_required",
            }
        if _is_ff_near_miss:
            # 32C.3: NEAR MISS — visible in Strategy 3 section but excluded from Daily Opportunity main list.
            return {
                "decision_class": "near_miss",
                "action_type": "forward_factor_near_miss",
                "actionability": "dry_run_only",
                "eligibility_status": "near_miss",
                "eligibility_reason": "Forward Factor NEAR MISS — narrow miss of entry threshold; diagnostic only.",
                "exclusion_reason": "near_miss",
                "priority_tier": "diagnostic",
                "review_status": "blocked",
            }
        return {
            "decision_class": "diagnostic",
            "action_type": "diagnostic",
            "actionability": "dry_run_only",
            "eligibility_status": "dry_run_excluded",
            "eligibility_reason": "Forward Factor remains dry-run.",
            "exclusion_reason": "dry_run",
            "priority_tier": "diagnostic",
            "review_status": "blocked",
        }

    if strategy_id == "stock_momentum":
        if upper.startswith(("CONSIDER ADDING", "ADD ON")):
            return _eligible_semantics("add", "stock_add", "review_only", "normal", "ready", "Stock momentum add candidate.")
        if upper.startswith("WATCH / CONFIRM TREND"):
            return _eligible_semantics("watch", "stock_watch", "monitor_only", "low", "needs_confirmation", "Positive momentum, but confirmation is still required.")
        if upper.startswith(("TACTICAL ONLY", "STARTER ONLY", "HOLD / DO NOT ADD")):
            return _eligible_semantics("watch", "tactical_stock_watch", "monitor_only", "low", "needs_confirmation", "Tactical/watchlist signal only; do not chase.")
        return _rejected_semantics("hard_fail", "Stock momentum row did not qualify.")

    if strategy_id == "earnings_calendar":
        lifecycle = str(row.get("lifecycle_stage") or "")
        trade_verdict = str(row.get("trade_verdict") or "").upper()
        recommended = str(row.get("recommended_action") or "").upper()
        eval_state = str(row.get("evaluation_state") or "")
        if lifecycle == "OPEN_POSITION" or row_type in {"open_calendar", "lifecycle_check"}:
            return _eligible_semantics("lifecycle", "calendar_position_action", "actionable", "high", "monitor", "Active calendar lifecycle row.")
        if row_type == "rejected_candidate" or _has_hard_blocker(row):
            return _rejected_semantics(_calendar_exclusion_reason(row, upper, status), "Earnings calendar row is blocked by a hard rejection gate.")
        if eval_state == "DEFERRED_BUDGET":
            return _excluded_semantics("deferred_budget", "none", "non_actionable", "budget_deferred", "blocked", "Calendar evaluation was deferred by provider/dev budget.")
        if trade_verdict == "PASS" and bool(row.get("entry_allowed")) and recommended == "ENTER":
            return _eligible_semantics("entry", "calendar_entry", "review_only", "normal", "ready", "Calendar entry candidate passed canonical lifecycle gates.")
        if status == "MONITOR_PRE_WINDOW":
            return _excluded_semantics("monitor", "none", "monitor_only", "pre_window", "monitor", "Calendar opportunity is before the approved entry-evaluation window.")
        if lifecycle == "SURFACED" and recommended in {"MONITOR", "PREPARE"}:
            return _eligible_semantics("monitor", "calendar_monitor", "monitor_only", "low", "monitor", "Calendar opportunity is surfaced for monitoring before entry evaluation.")
        if lifecycle in {"DISCOVERED", "DEVELOPING"}:
            return _excluded_semantics("monitor", "none", "monitor_only", "pre_window", "monitor", "Calendar opportunity is before the surfaced monitor window.")
        if trade_verdict in {"BLOCKED", "FAIL"} or upper.startswith("FAIL"):
            return _rejected_semantics(_calendar_exclusion_reason(row, upper, status), "Calendar opportunity is blocked by canonical decision service.")
        if eval_state in {"DATA_INCOMPLETE", "STRUCTURE_UNAVAILABLE", "EXPECTED_MISSING", "NOT_REQUESTED"}:
            return _excluded_semantics("monitor", "none", "monitor_only", (status or eval_state).lower(), "needs_data", "Calendar row is not entry-evaluable yet.")
        return _rejected_semantics("not_daily_opportunity_eligible", "Earnings calendar row is not eligible under canonical lifecycle semantics.")

    if strategy_id == "skew_momentum_vertical":
        if upper.startswith("PASS"):
            return _eligible_semantics("entry", "vertical_entry", "review_only", "normal", "ready", "Skew vertical candidate passed row gates.")
        if upper.startswith("WATCH"):
            return {
                "decision_class": "watch",
                "action_type": "monitor",
                "actionability": "monitor_only",
                "eligibility_status": "conditional",
                "eligibility_reason": "Skew row is watch-only.",
                "exclusion_reason": "not_daily_opportunity_eligible",
                "priority_tier": "low",
                "review_status": "monitor",
            }
        return _rejected_semantics("hard_fail", "Skew row did not qualify.")

    return _rejected_semantics("not_daily_opportunity_eligible", "No canonical semantics available.")


def _has_hard_blocker(row: dict[str, Any]) -> bool:
    if bool(row.get("hard_blocker") or row.get("has_hard_blocker")):
        return True
    for gate in row.get("gates") or row.get("checks") or row.get("requirements") or []:
        if not isinstance(gate, dict):
            continue
        status = str(gate.get("status") or gate.get("result") or "").upper()
        if bool(gate.get("is_hard_block") or gate.get("hard_blocker") or gate.get("blocks")) and status in {"FAIL", "FAILED", "BLOCKED"}:
            return True
    return False


def _calendar_exclusion_reason(row: dict[str, Any], upper_verdict: str, status: str) -> str:
    code = str(row.get("blocker_code") or row.get("primary_blocker") or row.get("reason_code") or status or "")
    normalized = code.strip().lower().replace(" ", "_").replace("/", "_")
    mapping = {
        "debit_too_large": "debit_too_large",
        "entry_window_closed": "entry_window_closed",
        "short_leg_spans_earnings": "short_leg_spans_earnings",
        "short_dte_too_low": "short_dte_too_low",
        "front_leg_too_decayed": "front_leg_too_decayed",
        "no_pre_earnings_short_expiry": "no_pre_earnings_short_expiry",
        "date_conflict_review": "date_conflict",
        "date_conflict": "date_conflict",
        "data_quality": "data_quality_fail",
        "data_quality_fail": "data_quality_fail",
    }
    for key, value in mapping.items():
        if key in normalized:
            return value
    if "DEBIT TOO LARGE" in upper_verdict:
        return "debit_too_large"
    if "ENTRY_WINDOW_CLOSED" in upper_verdict:
        return "entry_window_closed"
    if "SHORT_LEG_SPANS_EARNINGS" in upper_verdict:
        return "short_leg_spans_earnings"
    if "SHORT_DTE_TOO_LOW" in upper_verdict:
        return "short_dte_too_low"
    if "NO_PRE_EARNINGS_SHORT_EXPIRY" in upper_verdict:
        return "no_pre_earnings_short_expiry"
    if "DATA QUALITY" in upper_verdict:
        return "data_quality_fail"
    return "hard_fail"


def _eligible_semantics(
    decision_class: str,
    action_type: str,
    actionability: str,
    priority_tier: str,
    review_status: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "decision_class": decision_class,
        "action_type": action_type,
        "actionability": actionability,
        "eligibility_status": "eligible",
        "eligibility_reason": reason,
        "exclusion_reason": "",
        "priority_tier": priority_tier,
        "review_status": review_status,
    }


def _excluded_semantics(
    decision_class: str,
    action_type: str,
    actionability: str,
    code: str,
    review_status: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "decision_class": decision_class,
        "action_type": action_type,
        "actionability": actionability,
        "eligibility_status": "excluded",
        "eligibility_reason": reason,
        "exclusion_reason": code,
        "priority_tier": "low",
        "review_status": review_status,
    }


def _rejected_semantics(code: str, reason: str) -> dict[str, Any]:
    return {
        "decision_class": "rejected",
        "action_type": "none",
        "actionability": "non_actionable",
        "eligibility_status": "excluded",
        "eligibility_reason": "",
        "exclusion_reason": code,
        "priority_tier": "diagnostic",
        "review_status": "blocked",
    }


# ─── gates ────────────────────────────────────────────────────────────────────


def _gates(row: dict[str, Any], strategy_id: str) -> list[dict[str, Any]] | None:
    if strategy_id == "skew_momentum_vertical":
        reqs = row.get("requirements")
        if isinstance(reqs, list):
            return [_normalize_requirement(r) for r in reqs]
        return []

    if strategy_id == "forward_factor_calendar":
        fg = row.get("ff_gates")
        if not isinstance(fg, dict):
            return []
        cheap = bool(fg.get("cheap_eligible"))
        chain = bool(fg.get("chain_approved"))
        sq = bool(fg.get("source_qualified"))
        dm = bool(fg.get("diagnostic_model"))
        sb = bool(fg.get("structure_built"))
        contaminated = bool(fg.get("earnings_contaminated"))
        stage = str(row.get("ff_candidate_stage") or "")
        if stage in _FF_SKIP_STAGES:
            return [
                make_gate("Coverage eligibility", "skipped", reason="Outside limited scan window", sort_order=10),
                make_gate("Chain approved", "not_applicable", blocking=False),
                make_gate("Source qualified", "not_applicable", blocking=False),
                make_gate("Diagnostic model", "not_applicable", blocking=False),
                make_gate("Structure built", "not_applicable", blocking=False),
                make_gate("Execution", "dry_run", reason="Signal-only mode", blocking=False, sort_order=90),
            ]
        return [
            make_gate("Coverage eligibility", "pass" if cheap else "fail", sort_order=10),
            make_gate("Chain approved",
                      "pass" if chain else ("not_applicable" if not cheap else "fail"),
                      blocking=not chain and cheap),
            make_gate("Source qualified",
                      "fail" if contaminated else ("pass" if sq else ("not_applicable" if not chain else "fail")),
                      reason="Earnings contamination" if contaminated else "",
                      blocking=contaminated or (not sq and chain)),
            make_gate("Diagnostic model",
                      "pass" if dm else ("not_applicable" if not (chain or sq) else "fail"),
                      blocking=not dm and (chain or sq)),
            make_gate("Structure built",
                      "pass" if sb else ("not_applicable" if not (dm or sq) else "fail"),
                      blocking=not sb and (dm or sq)),
            make_gate("Execution", "dry_run", reason="Signal-only mode — trade gated for all tickers",
                      blocking=False, sort_order=90),
        ]

    if strategy_id == "stock_momentum":
        mm = row.get("market_metrics") or {}
        above50 = mm.get("above_sma_50") if mm else row.get("above_sma_50")
        above200 = mm.get("above_sma_200") if mm else row.get("above_sma_200")
        add_allowed = bool(row.get("add_allowed_boolean"))
        action = str(row.get("action") or "").upper()
        overall = "pass" if add_allowed else ("watch" if "WATCH" in action else "fail")
        blockers = row.get("add_blockers") or []
        gates: list[dict[str, Any]] = [
            make_gate("Above 50-day MA", _bool_status(above50), sort_order=60, blocking=False),
            make_gate("Above 200-day MA", _bool_status(above200), sort_order=60, blocking=False),
            make_gate("Momentum verdict", overall, reason=str(row.get("action") or ""), sort_order=65),
        ]
        if blockers:
            gates.append(make_gate("Entry blockers", "fail", reason=str(blockers[0]), sort_order=75))
        return gates

    if strategy_id not in ("skew_momentum_vertical", "forward_factor_calendar",
                           "stock_momentum", "earnings_calendar"):
        return []

    if strategy_id == "earnings_calendar":
        relation = str(row.get("earnings_relation") or "unknown")
        trust_label = str(row.get("earnings_trust_label") or "unknown")
        calendar_entry_allowed = bool(row.get("calendar_entry_allowed"))
        action = str(row.get("action") or "").upper()
        gates = []
        # Expiration relationship gate
        if relation == "long_leg_captures_earnings":
            gates.append(make_gate("Expiration pair", "pass", reason="Preferred structure", sort_order=30))
        elif relation in ("missing_expiration", "already_reported", "earnings_after_back_leg",
                          "short_leg_spans_earnings"):
            gates.append(make_gate("Expiration pair", "fail", reason=relation.replace("_", " "), sort_order=30))
        elif relation in ("near_miss_expiry_gap", "earnings_on_front_expiration"):
            gates.append(make_gate("Expiration pair", "watch", reason=relation.replace("_", " "),
                                   blocking=False, sort_order=30))
        else:
            gates.append(make_gate("Expiration pair", "unknown", reason=relation,
                                   blocking=False, sort_order=30))
        # Earnings trust gate
        if trust_label == "conflict_do_not_trade":
            gates.append(make_gate("Earnings date trust", "fail",
                                   reason="Conflict — do not trade", sort_order=20))
        elif trust_label == "single_source_verify":
            gates.append(make_gate("Earnings date trust", "watch",
                                   reason="Single-source lower confidence", blocking=False, sort_order=20))
        elif trust_label in ("confirmed", "multi_source", "multi_source_confirmed"):
            gates.append(make_gate("Earnings date trust", "pass",
                                   reason="Confirmed earnings date", sort_order=20))
        else:
            gates.append(make_gate("Earnings date trust", "unknown",
                                   reason=trust_label, blocking=False, sort_order=20))
        # Calendar entry gate
        if calendar_entry_allowed:
            gates.append(make_gate("Calendar entry", "pass", sort_order=80))
        elif "URGENT" in action or "NEAR_MISS" in action or "WATCH" in action:
            gates.append(make_gate("Calendar entry", "watch", blocking=False, sort_order=80))
        else:
            gates.append(make_gate("Calendar entry", "fail", sort_order=80))
        return gates


def _normalize_requirement(req: dict[str, Any]) -> dict[str, Any]:
    """Map a raw skew requirement dict into the canonical gate shape."""
    name = str(req.get("name") or req.get("code") or "Check")
    raw_status = str(req.get("status") or "").upper()
    status = "pass" if raw_status == "PASS" else ("fail" if raw_status == "FAIL" else "unknown")
    detail = str(req.get("detail") or "")
    code = str(req.get("code") or "")
    return make_gate(name, status, id=code or None, reason=detail, sort_order=50)


# ─── journal / observation readiness ─────────────────────────────────────────


def _journal_eligible(row: dict[str, Any], strategy_id: str) -> bool:
    """True if this row has enough identity to become a 30B journal entry."""
    ticker = str(row.get("ticker") or "").strip()
    has_verdict = bool(row.get("verdict") or row.get("action"))
    return bool(ticker and has_verdict)


def _observation_key(row: dict[str, Any], strategy_id: str) -> str:
    """Stable observation key for 30B journal entries.

    Format: strategy_id:ticker:candidate_type:structure_type:expiration_or_timeframe
    """
    ticker = str(row.get("ticker") or "unknown").upper()

    if strategy_id == "earnings_calendar":
        candidate_type = "calendar_candidate"
        structure_type = str(row.get("structure_type") or "calendar_spread")
        expiration = str(row.get("front_expiration") or row.get("front_expiry") or "")
        # Include option_type so call vs put calendars on the same ticker get distinct observation keys.
        _opt_type = str(row.get("option_type") or "").lower().strip()
        if _opt_type:
            structure_type = f"{structure_type}.{_opt_type}"
    elif strategy_id == "skew_momentum_vertical":
        candidate_type = "vertical_spread"
        structure_type = str(row.get("structure_type") or "vertical")
        expiration = str(row.get("selected_expiration") or row.get("expiration") or "")
    elif strategy_id == "forward_factor_calendar":
        candidate_type = "forward_factor_signal"
        structure_type = str(row.get("structure_type") or "calendar")
        expiration = str(row.get("structure_front_expiry") or row.get("front_expiration") or "")
    elif strategy_id == "stock_momentum":
        candidate_type = "stock_momentum"
        structure_type = "equity"
        expiration = ""
    else:
        candidate_type = "unknown"
        structure_type = "unknown"
        expiration = ""

    parts = [strategy_id, ticker, candidate_type, structure_type]
    if expiration:
        parts.append(expiration)
    return ":".join(parts)


# ─── helpers ──────────────────────────────────────────────────────────────────


def _bool_status(val: Any) -> str:
    if val is True:
        return "pass"
    if val is False:
        return "fail"
    return "unknown"
