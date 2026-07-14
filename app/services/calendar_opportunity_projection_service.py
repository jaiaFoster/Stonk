"""Canonical earnings-calendar opportunity projection.

This service owns the compatibility bridge from existing calendar rows to the
33B lifecycle fields. It does not fetch data, rebuild structures, or decide
strategy math; it projects already-computed facts into stable opportunity,
structure, lifecycle, and reconciliation fields.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.models.calendar_evolution_policy import CalendarEvolutionPolicy
from app.models.strategy_opportunity_lifecycle import EvaluationState, LifecycleStage, Verdict
from app.services.calendar_decision_service import decide_calendar_opportunity
from app.services.calendar_opportunity_lifecycle_adapter import (
    build_calendar_lifecycle_opportunity,
    build_opportunity_id,
    build_structure_id,
)
from app.services.calendar_opportunity_state_service import attach_calendar_display_fields
from app.services.calendar_risk_fact_service import evaluate_account_risk
from app.services.earnings_trust_service import normalize_earnings_trust


CALENDAR_STRATEGY_DEFINITION_ID = "earnings_calendar"
CALENDAR_STRATEGY_DEFINITION_VERSION = "v1"
CALENDAR_STRUCTURE_TEMPLATE_ID = "earnings_calendar_same_strike"
CALENDAR_ENUMERATION_POLICY_VERSION = "34A.expiration_enumeration.v1"


def build_calendar_canonical_projection(
    *,
    earnings_trade_discovery: dict[str, Any] | None,
    earnings_discovery_quality: dict[str, Any] | None,
    calendar_candidates: list[dict[str, Any]] | None,
    earnings_calendar_strategy: dict[str, Any] | None,
    calendar_ranking: dict[str, Any] | None,
    account_context: dict[str, Any] | None,
    open_options: dict[str, Any] | None,
    lifecycle_checks: dict[str, Any] | None,
    policy: CalendarEvolutionPolicy,
    evaluation_date: date | None = None,
    run_mode: str = "prod",
    log_print: Any | None = None,
) -> dict[str, Any]:
    """Project canonical calendar parent rows from computed facts.

    This is the live 33C.1 replacement for the old Unified Calendar Trade
    Engine. It creates parent opportunity rows only; expiration/strike attempts
    remain nested diagnostics.
    """
    today = evaluation_date or date.today()
    logger = log_print or (lambda msg: None)
    quality = earnings_discovery_quality or {}
    discovery = earnings_trade_discovery or {}
    candidates = [item for item in (calendar_candidates or []) if isinstance(item, dict)]
    strategy = earnings_calendar_strategy or {}
    ranking = calendar_ranking or {}

    candidates_by_ticker = _by_ticker(candidates)
    strategy_by_ticker = _by_ticker(strategy.get("items") or [])
    ranking_by_ticker = _by_ticker(ranking.get("items") or [])

    events_for_rows: list[dict[str, Any]] = []
    quality_items = [item for item in (quality.get("items") or []) if isinstance(item, dict)]
    if quality_items:
        events_for_rows.extend(quality_items)
    else:
        events_for_rows.extend([item for item in (discovery.get("items") or []) if isinstance(item, dict)])

    seen = {str(item.get("ticker") or item.get("symbol") or "").upper().strip() for item in events_for_rows}
    if str(run_mode or "").lower() == "dev":
        for item in (discovery.get("items") or []):
            ticker = str((item or {}).get("ticker") or (item or {}).get("symbol") or "").upper().strip()
            if ticker and ticker not in seen:
                deferred = dict(item)
                deferred["ticker"] = ticker
                deferred["entry_window_status"] = "DEV_MODE_BUDGET_NOT_SELECTED"
                deferred["exit_reason"] = "DEV_MODE_BUDGET_NOT_SELECTED"
                deferred["primary_rejection_reason"] = "Calendar evaluation deferred by dev provider budget."
                events_for_rows.append(deferred)
                seen.add(ticker)

    new_rows: list[dict[str, Any]] = []
    for event in events_for_rows:
        ticker = str(event.get("ticker") or event.get("symbol") or "UNKNOWN").upper().strip()
        if not ticker:
            continue
        row = _build_parent_opportunity_row(
            event,
            candidate=candidates_by_ticker.get(ticker) or {},
            strategy=strategy_by_ticker.get(ticker) or {},
            ranking=ranking_by_ticker.get(ticker) or {},
            account_context=account_context,
        )
        _enrich_row(row, policy=policy, evaluation_date=today, open_position=False)
        new_rows.append(attach_calendar_display_fields(row))

    open_rows: list[dict[str, Any]] = []
    for row in _build_open_position_rows(open_options or {}, lifecycle_checks or {}):
        _enrich_row(row, policy=policy, evaluation_date=today, open_position=True)
        open_rows.append(attach_calendar_display_fields(row))

    new_rows = _collapse_parent_rows(new_rows)
    result = {
        "source": "calendar_canonical_projection_v1",
        "has_data": bool(new_rows or open_rows),
        "new_trade_rows": new_rows,
        "open_trade_rows": open_rows,
        "blocked_rows": [],
        "summary": _summary(new_rows, open_rows),
        "errors": [],
    }
    validation = validate_calendar_canonical_rows(new_rows + open_rows)
    result["canonical_validation"] = validation
    if validation["invariant_violations"]:
        result["errors"].extend(validation["invariant_violations"])
    result["calendar_row_reconciliation"] = build_calendar_row_reconciliation(result)
    audit = _decision_audit(new_rows, open_rows)
    logger(
        "CALENDAR_CANONICAL_PROJECTION "
        f"opportunity_parents={len(new_rows)} "
        f"open_position_parents=0 "
        f"open_position_children={len(open_rows)} "
        f"diagnostics={sum((row.get('structure_attempt_summary') or {}).get('attempt_count', 0) for row in new_rows)}"
    )
    logger(
        "CALENDAR_DECISION_AUDIT "
        f"not_evaluated={audit.get('NOT_EVALUATED', 0)} "
        f"fully_evaluated={audit.get('FULLY_EVALUATED', 0)} "
        f"pass={audit.get('PASS', 0)} "
        f"watch={audit.get('WATCH', 0)} "
        f"near_miss={audit.get('NEAR_MISS', 0)} "
        f"fail={audit.get('FAIL', 0)} "
        f"blocked={audit.get('BLOCKED', 0)} "
        f"invariant_violations={audit.get('invariant_violations', 0)}"
    )
    return result


def validate_calendar_canonical_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate canonical row invariants before persistence."""
    violations: list[dict[str, Any]] = []
    seen_parent_ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        ticker = row.get("ticker")
        row_id = row.get("row_id")
        row_model = str(row.get("row_model") or row.get("row_type") or "")
        entry_eval = bool(row.get("entry_evaluation_eligible"))
        entry_allowed = bool(row.get("entry_allowed"))
        state = str(row.get("evaluation_state") or "")
        verdict = str(row.get("trade_verdict") or "")
        action = str(row.get("recommended_action") or "")
        if row_model == "OPPORTUNITY_PARENT":
            opp_id = str(row.get("opportunity_id") or "")
            if opp_id and opp_id in seen_parent_ids:
                violations.append({"code": "DUPLICATE_PARENT_OPPORTUNITY", "ticker": ticker, "row_id": row_id, "opportunity_id": opp_id})
            seen_parent_ids.add(opp_id)
        if row_model == "OPPORTUNITY_PARENT" and not entry_eval and verdict != Verdict.NOT_EVALUATED:
            violations.append({"code": "NON_ENTRY_ROW_HAS_FINAL_VERDICT", "ticker": ticker, "row_id": row_id, "trade_verdict": verdict})
        if entry_allowed and not entry_eval:
            violations.append({"code": "ENTRY_ALLOWED_WITHOUT_ENTRY_EVALUATION", "ticker": ticker, "row_id": row_id, "evaluation_state": state, "trade_verdict": verdict, "recommended_action": action})
        if entry_allowed and state != EvaluationState.FULLY_EVALUATED:
            violations.append({"code": "ENTRY_ALLOWED_NOT_FULLY_EVALUATED", "ticker": ticker, "row_id": row_id, "evaluation_state": state, "trade_verdict": verdict})
        if entry_allowed and verdict != Verdict.PASS:
            violations.append({"code": "ENTRY_ALLOWED_NON_PASS_VERDICT", "ticker": ticker, "row_id": row_id, "trade_verdict": verdict})
        if entry_allowed and action != "ENTER":
            violations.append({"code": "ENTRY_ALLOWED_NON_ENTER_ACTION", "ticker": ticker, "row_id": row_id, "recommended_action": action})
        if verdict == Verdict.NOT_EVALUATED and entry_allowed:
            violations.append({"code": "NOT_EVALUATED_ENTRY_ALLOWED", "ticker": ticker, "row_id": row_id})
        if state == EvaluationState.STRUCTURE_UNAVAILABLE and entry_allowed:
            violations.append({"code": "STRUCTURE_UNAVAILABLE_ENTRY_ALLOWED", "ticker": ticker, "row_id": row_id})
        if state == EvaluationState.DEFERRED_BUDGET:
            if verdict != Verdict.NOT_EVALUATED:
                violations.append({"code": "DEFERRED_BUDGET_FINAL_VERDICT", "ticker": ticker, "row_id": row_id, "trade_verdict": verdict})
            if entry_allowed:
                violations.append({"code": "DEFERRED_BUDGET_ENTRY_ALLOWED", "ticker": ticker, "row_id": row_id})
            if action not in {"NONE", "MONITOR"}:
                violations.append({"code": "DEFERRED_BUDGET_BAD_ACTION", "ticker": ticker, "row_id": row_id, "recommended_action": action})
        if verdict in {Verdict.PASS, Verdict.WATCH, Verdict.NEAR_MISS, Verdict.FAIL} and state != EvaluationState.FULLY_EVALUATED:
            violations.append({"code": "FINAL_VERDICT_NOT_FULLY_EVALUATED", "ticker": ticker, "row_id": row_id, "evaluation_state": state, "trade_verdict": verdict})
        if verdict in {Verdict.PASS, Verdict.WATCH, Verdict.NEAR_MISS, Verdict.FAIL} and not entry_eval:
            violations.append({"code": "FINAL_VERDICT_NOT_ENTRY_EVALUABLE", "ticker": ticker, "row_id": row_id, "trade_verdict": verdict})
        if row.get("lifecycle_stage") == LifecycleStage.OPEN_POSITION and action not in {"HOLD", "EXIT", "REVIEW"}:
            violations.append({"code": "OPEN_POSITION_BAD_ACTION", "ticker": ticker, "row_id": row_id, "recommended_action": action})
        if not action:
            violations.append({"code": "ACTION_MISSING", "ticker": ticker, "row_id": row_id})
        if row_model == "OPPORTUNITY_PARENT" and row.get("current_structure_id") and row_id != row.get("opportunity_id"):
            violations.append({"code": "STRUCTURE_ATTEMPT_PERSISTED_AS_PARENT", "ticker": ticker, "row_id": row_id})
    return {
        "checked_rows": len([row for row in rows if isinstance(row, dict)]),
        "violation_count": len(violations),
        "invariant_violations": violations,
    }


def enrich_calendar_engine_rows(
    engine: dict[str, Any] | None,
    *,
    policy: CalendarEvolutionPolicy,
    evaluation_date: date | None = None,
) -> dict[str, Any]:
    """Mutate and return a unified calendar engine with lifecycle fields."""
    if not isinstance(engine, dict):
        return engine or {}
    today = evaluation_date or date.today()
    for key in ("new_trade_rows", "open_trade_rows", "blocked_rows"):
        for row in engine.get(key) or []:
            if isinstance(row, dict):
                _enrich_row(row, policy=policy, evaluation_date=today, open_position=(key == "open_trade_rows"))
    engine["new_trade_rows"] = _collapse_parent_rows(engine.get("new_trade_rows") or [])
    engine["calendar_row_reconciliation"] = build_calendar_row_reconciliation(engine)
    return engine


def _build_parent_opportunity_row(
    event: dict[str, Any],
    *,
    candidate: dict[str, Any],
    strategy: dict[str, Any],
    ranking: dict[str, Any],
    account_context: dict[str, Any] | None,
) -> dict[str, Any]:
    quality_row = event if event.get("checks") is not None else {}
    event_payload = quality_row.get("event") if isinstance(quality_row.get("event"), dict) else event
    ticker = str(quality_row.get("ticker") or event_payload.get("ticker") or event_payload.get("symbol") or "UNKNOWN").upper().strip()
    trust = normalize_earnings_trust({**event_payload, **quality_row})
    score = _float_or_none(strategy.get("score"))
    if score is None:
        score = _float_or_none(candidate.get("score"))
    if score is None:
        score = _baseline_score_for_event(event_payload)
    possible_spread = _possible_spread(candidate)
    acct_risk = evaluate_account_risk(candidate, account_context)
    status = str(quality_row.get("entry_window_status") or "")
    no_structure = quality_row.get("primary_rejection_reason") or quality_row.get("entry_window_reason") or ""
    raw_verdict = _raw_candidate_verdict(candidate, strategy, quality_row)
    row = {
        "strategy_id": "earnings_calendar",
        "strategy_label": "Earnings Calendar",
        "strategy_definition_id": CALENDAR_STRATEGY_DEFINITION_ID,
        "strategy_definition_version": CALENDAR_STRATEGY_DEFINITION_VERSION,
        "structure_template_id": CALENDAR_STRUCTURE_TEMPLATE_ID,
        "enumeration_policy_version": CALENDAR_ENUMERATION_POLICY_VERSION,
        "source": "calendar_canonical_projection_v1",
        "row_model": "OPPORTUNITY_PARENT",
        "row_type": "OPPORTUNITY_PARENT",
        "type": "calendar_opportunity_parent",
        "ticker": ticker,
        "score": round(max(0.0, min(100.0, float(score or 0.0))), 1),
        "verdict": raw_verdict,
        "raw_scanner_verdict": raw_verdict,
        "main_blocker": no_structure,
        "main_reason": no_structure,
        "backtest_status": "not_applicable",
        "account_risk_status": acct_risk["account_risk_status"],
        "account_risk_warning": acct_risk.get("account_risk_warning") or "",
        "account_value_used": acct_risk.get("account_value_estimate"),
        "debit_pct_of_account": acct_risk.get("debit_pct_of_account"),
        "entry_plan": "",
        "earnings": _compact_event(event_payload),
        "candidate": candidate,
        "strategy": strategy,
        "ranking": ranking,
        "quality_precheck": quality_row,
        "possible_spread": possible_spread,
        "requirements": _requirements(event_payload, quality_row, candidate, trust),
        "coverage_accounting": {
            "policy_version": "34A.calendar_coverage.v1",
            "quality_row_present": bool(quality_row),
            "candidate_row_present": bool(candidate),
            "strategy_row_present": bool(strategy),
            "ranking_row_present": bool(ranking),
            "has_expiration_pair": bool(quality_row.get("expiration_pair") or candidate.get("expiration_pair")),
            "rejected_expiration_count": len(quality_row.get("rejected_expirations") or []),
        },
        "reasons": _dedupe([strategy.get("next_check"), candidate.get("next_check"), quality_row.get("entry_window_reason")]),
        "risks": _dedupe([quality_row.get("primary_rejection_reason"), quality_row.get("entry_window_reason"), acct_risk.get("account_risk_warning")]),
        **trust,
    }
    for key in (
        "date_confidence", "date_sources", "date_conflict", "expiry_near_miss", "expiry_gap_note",
        "expiration_pair", "expiration_pair_diagnostics", "entry_window_status", "entry_window_open",
        "entry_window_reason", "short_leg_expires_before_earnings", "short_leg_dte_minimum",
        "short_leg_time_value_minimum", "short_leg_does_not_span_event", "entry_window_front_expiration",
        "entry_window_front_dte", "expiry_gap_valid", "available_pre_earnings_expirations",
        "rejected_expirations", "proposed_short_expiration", "proposed_long_expiration",
        "available_expirations", "current_dte_to_earnings", "ideal_entry_window",
        "estimated_entry_date", "days_until_entry_window", "blocker_code", "blocker_detail",
        "front_expiration", "back_expiration", "front_dte", "back_dte", "exit_reason", "exit_stage",
        "pipeline_trace",
    ):
        if key in quality_row:
            row.setdefault(key, quality_row.get(key))
    if status == "DEV_MODE_BUDGET_NOT_SELECTED" or str(row.get("exit_reason") or "") == "DEV_MODE_BUDGET_NOT_SELECTED":
        row["disposition_code"] = "DEV_MODE_BUDGET_NOT_SELECTED"
    elif status:
        row["disposition_code"] = status
    row.setdefault("date_confidence", quality_row.get("date_confidence") or quality_row.get("earnings_date_confidence") or "unknown")
    row.setdefault("date_sources", quality_row.get("date_sources") or [])
    row.setdefault("date_conflict", bool(quality_row.get("date_conflict")))
    row.setdefault("expiry_near_miss", bool(quality_row.get("expiry_near_miss")))
    row.setdefault("expiry_gap_note", quality_row.get("expiry_gap_note") or "")
    row.setdefault("expiration_pair_diagnostics", quality_row.get("expiration_pair_diagnostics") or {})
    row.setdefault("high_move_warning", bool(quality_row.get("high_move_warning")))
    row.setdefault("high_move_note", quality_row.get("high_move_note") or "")
    return row


def _build_open_position_rows(open_options: dict[str, Any], lifecycle_checks: dict[str, Any]) -> list[dict[str, Any]]:
    checks = [item for item in (lifecycle_checks or {}).get("checks", []) or [] if isinstance(item, dict)]
    source_rows = checks or [item for item in (open_options or {}).get("calendars", []) or [] if isinstance(item, dict)]
    rows: list[dict[str, Any]] = []
    for item in source_rows:
        structure_summary = {
            "structure_type": "calendar",
            "option_type": str(item.get("option_type") or "call").lower(),
            "strike": item.get("strike"),
            "front_expiration": item.get("front_expiration"),
            "back_expiration": item.get("back_expiration"),
            "legs": item.get("legs") or [],
            "current_debit": item.get("current_mid_debit"),
        }
        rows.append({
            "strategy_id": "earnings_calendar",
            "strategy_label": "Earnings Calendar",
            "strategy_definition_id": CALENDAR_STRATEGY_DEFINITION_ID,
            "strategy_definition_version": CALENDAR_STRATEGY_DEFINITION_VERSION,
            "structure_template_id": CALENDAR_STRUCTURE_TEMPLATE_ID,
            "enumeration_policy_version": CALENDAR_ENUMERATION_POLICY_VERSION,
            "source": "calendar_lifecycle_v1" if checks else "open_options_detector_v2",
            "row_model": "OPEN_POSITION_CHILD",
            "row_type": "OPEN_POSITION_CHILD",
            "type": "open_calendar",
            "ticker": str(item.get("ticker") or item.get("underlying") or "UNKNOWN").upper(),
            "score": _score_open_position(item),
            "verdict": item.get("action") or "HOLD / MONITOR",
            "next_action": item.get("next_check") or "Recheck live spread value before market close.",
            "structure": _open_structure(item),
            "structure_summary": structure_summary,
            "option_type": structure_summary["option_type"],
            "strike": structure_summary["strike"],
            "front_expiration": structure_summary["front_expiration"],
            "back_expiration": structure_summary["back_expiration"],
            "value": _open_value_summary(item),
            "hold_through_score": item.get("hold_through_score"),
            "hold_through_action": item.get("hold_through_action"),
            "trade_type": item.get("trade_type"),
            "trade_type_label": item.get("trade_type_label"),
            "reasons": item.get("reasons", []) or [],
            "risks": item.get("risks", []) or [],
            "raw": item,
        })
    return rows


def build_calendar_row_reconciliation(
    engine: dict[str, Any] | None,
    *,
    persisted_rows: int | None = None,
    api_visible_rows: int | None = None,
    history_rows: int | None = None,
    journal_rows: int | None = None,
) -> dict[str, Any]:
    engine = engine or {}
    parent_rows = [row for row in (engine.get("new_trade_rows") or []) if isinstance(row, dict)]
    open_rows = [row for row in (engine.get("open_trade_rows") or []) if isinstance(row, dict)]
    blocked_rows = [row for row in (engine.get("blocked_rows") or []) if isinstance(row, dict)]
    generated = len(parent_rows) + len(open_rows) + len(blocked_rows)
    invalid = 0
    duplicates = 0
    seen: set[str] = set()
    excluded: dict[str, int] = {}
    daily_visible = 0
    for key in ("new_trade_rows", "open_trade_rows", "blocked_rows"):
        for row in engine.get(key) or []:
            if not isinstance(row, dict):
                invalid += 1
                continue
            row_id = str(row.get("row_id") or row.get("opportunity_id") or "")
            if row_id:
                if row_id in seen:
                    duplicates += 1
                seen.add(row_id)
            reason = str(row.get("exclusion_reason") or row.get("disposition_code") or row.get("entry_window_status") or "")
            if not row.get("can_enter_daily_opportunity") and reason:
                excluded[reason] = excluded.get(reason, 0) + 1
            if row.get("can_enter_daily_opportunity"):
                daily_visible += 1
    return {
        "opportunity_parents_generated": len(parent_rows),
        "open_position_parents_generated": 0,
        "open_position_children_generated": len(open_rows),
        "parent_generated": len(parent_rows),
        "open_parent_generated": 0,
        "open_child_generated": len(open_rows),
        "structure_records": sum(1 for row in parent_rows if row.get("current_structure_id")),
        "diagnostic_records": sum(int((row.get("structure_attempt_summary") or {}).get("attempt_count") or 0) for row in parent_rows),
        "generated_rows": generated,
        "normalized_rows": generated - invalid,
        "duplicate_rows": duplicates,
        "invalid_rows": invalid,
        "persisted_rows": persisted_rows if persisted_rows is not None else 0,
        "api_rows": api_visible_rows if api_visible_rows is not None else 0,
        "api_visible_parents": api_visible_rows if api_visible_rows is not None else 0,
        "api_visible_children": 0,
        "earnings_calendar_api_rows": api_visible_rows if api_visible_rows is not None else 0,
        "strategy_lifecycle_api_rows": 0,
        "open_positions_api_parent_rows": 0,
        "open_positions_api_child_rows": 0,
        "daily_opportunity_rows": daily_visible,
        "history_rows": history_rows if history_rows is not None else 0,
        "journal_rows": journal_rows if journal_rows is not None else 0,
        "excluded_rows_by_reason": excluded,
        "api_exclusions": {},
        "persistence_exclusions": {},
    }


def _enrich_row(
    row: dict[str, Any],
    *,
    policy: CalendarEvolutionPolicy,
    evaluation_date: date,
    open_position: bool = False,
) -> None:
    ticker = str(row.get("ticker") or row.get("symbol") or "UNKNOWN").upper().strip()
    event_date = _row_event_date(row)
    if event_date is None:
        row.setdefault("lifecycle_stage", LifecycleStage.DISCOVERED)
        row.setdefault("evaluation_state", EvaluationState.DATA_INCOMPLETE)
        row.setdefault("trade_verdict", Verdict.NOT_EVALUATED)
        row.setdefault("recommended_action", "MONITOR")
        row.setdefault("calendar_stage", "DATA_NEEDED")
        row.setdefault("entry_allowed", False)
        row.setdefault("surface_eligible", False)
        row.setdefault("build_eligible", False)
        row.setdefault("disposition_code", "DATA_NEEDED")
        return

    days_until = int(row.get("current_dte_to_earnings") or (event_date - evaluation_date).days)
    status = str(row.get("entry_window_status") or "")
    has_structure = bool(row.get("possible_spread") or row.get("candidate") or row.get("front_expiration"))
    structure_state = _structure_state(row, status)
    opp, errors = build_calendar_lifecycle_opportunity(
        ticker=ticker,
        earnings_date=event_date,
        days_until_event=days_until,
        policy=policy,
        has_structure=has_structure,
        structure_evaluation_state=structure_state,
        has_open_position=open_position,
        evaluation_date=evaluation_date,
        metadata={},
    )
    opp_id = build_opportunity_id(ticker, event_date)
    row.setdefault("opportunity_id", opp_id)
    row.setdefault("anchor_type", "earnings_date")
    row.setdefault("anchor_id", event_date.isoformat())
    row.setdefault("clock_type", "event_dte")
    row.setdefault("clock_value", days_until)
    row.setdefault("anchor_timestamp", event_date.isoformat())
    row["lifecycle_stage"] = opp.lifecycle_stage
    row["build_eligible"] = opp.build_eligible
    row["surface_eligible"] = opp.surface_eligible
    row["entry_evaluation_eligible"] = bool(policy.is_entry_allowed(days_until))
    row["terminal"] = opp.lifecycle_stage in {LifecycleStage.POST_EVENT, LifecycleStage.INVALIDATED, LifecycleStage.TERMINAL}
    row["policy_version"] = policy.policy_version
    row["policy_source"] = policy.source_by_field
    row["calendar_stage"] = _calendar_stage(row, policy, days_until, status, open_position=open_position)
    row["strategy_stage"] = row["calendar_stage"]
    row["disposition_code"] = _disposition_code(row, status)
    row["disposition_reason"] = row.get("entry_window_reason") or row.get("main_blocker") or row.get("primary_reason")
    if errors:
        row["lifecycle_validation_errors"] = errors

    structure_id = _structure_id_for_row(row, opp_id)
    if structure_id:
        row.setdefault("structure_id", structure_id)
        row.setdefault("current_structure_id", structure_id)
        row.setdefault("structure_version", 1)
        row.setdefault("previous_structure_id", None)
        row.setdefault("structure_changed", False)
        row.setdefault("structure_change_reason", "initial_or_same_structure")
    decision = decide_calendar_opportunity(
        row,
        lifecycle_stage=opp.lifecycle_stage,
        lifecycle_evaluation_state=opp.evaluation_state,
        lifecycle_recommended_action=opp.recommended_action,
        entry_evaluation_eligible=bool(row["entry_evaluation_eligible"]),
        structure_available=bool(structure_id or row.get("possible_spread") or row.get("candidate")),
    )
    row.update(decision.to_dict())
    row["can_enter_daily_opportunity"] = bool(decision.entry_allowed and decision.trade_verdict == Verdict.PASS)
    row["calendar_entry_allowed"] = bool(decision.entry_allowed)
    row["entry_allowed"] = bool(decision.entry_allowed)
    row["action"] = decision.recommended_action
    row["final_verdict"] = decision.trade_verdict
    row.setdefault("row_id", row.get("current_structure_id") if open_position else row.get("opportunity_id"))


def _structure_state(row: dict[str, Any], status: str) -> str | None:
    if status == "DEV_MODE_BUDGET_NOT_SELECTED" or str(row.get("exit_reason") or "") == "DEV_MODE_BUDGET_NOT_SELECTED":
        return EvaluationState.DEFERRED_BUDGET
    if status in {"DATA_NEEDED", "DATE_CONFLICT_REVIEW"}:
        return EvaluationState.DATA_INCOMPLETE
    if status in {"ENTRY_WINDOW_CLOSED", "NO_PRE_EARNINGS_SHORT_EXPIRY", "SHORT_LEG_SPANS_EARNINGS", "SHORT_DTE_TOO_LOW", "FRONT_LEG_TOO_DECAYED"}:
        return EvaluationState.STRUCTURE_UNAVAILABLE
    if row.get("possible_spread") or row.get("candidate") or row.get("front_expiration"):
        return EvaluationState.FULLY_EVALUATED if bool(row.get("entry_allowed") or row.get("calendar_entry_allowed")) else EvaluationState.STRUCTURE_COMPLETE
    return None


def _calendar_stage(row: dict[str, Any], policy: CalendarEvolutionPolicy, dte: int, status: str, *, open_position: bool) -> str:
    if open_position:
        return "OPEN_POSITION"
    if dte < 0:
        return "POST_EVENT"
    if not policy.is_in_discovery_window(dte):
        return "OUTSIDE_DISCOVERY_WINDOW"
    if status == "MONITOR_PRE_WINDOW":
        return "SURFACED_MONITOR" if policy.is_surface_eligible(dte) else "STRUCTURE_BUILDING"
    if status == "ENTRY_WINDOW_OPEN":
        return "ENTRY_WINDOW_OPEN"
    if status == "ENTRY_WINDOW_CLOSING":
        return "ENTRY_WINDOW_CLOSING"
    if 0 <= dte < policy.late_entry_event_dte:
        return "LATE_WINDOW"
    if dte > policy.build_start_event_dte:
        return "DISCOVERED"
    if dte > policy.surface_start_event_dte:
        return "STRUCTURE_BUILDING"
    if dte > policy.ideal_entry_max_event_dte:
        return "SURFACED_MONITOR"
    return "ENTRY_WINDOW_OPEN" if bool(row.get("entry_allowed") or row.get("calendar_entry_allowed")) else "LATE_WINDOW"


def _disposition_code(row: dict[str, Any], status: str) -> str | None:
    if status:
        return status
    return row.get("blocker_code") or row.get("primary_rejection_reason") or row.get("main_blocker")


def _structure_id_for_row(row: dict[str, Any], opportunity_id: str) -> str | None:
    possible = row.get("possible_spread") if isinstance(row.get("possible_spread"), dict) else {}
    structure = row.get("structure") if isinstance(row.get("structure"), dict) else {}
    source = possible or structure or row
    option_type = source.get("option_type")
    strike = source.get("strike")
    front = source.get("short_expiration") or source.get("front_expiration")
    back = source.get("long_expiration") or source.get("back_expiration")
    if option_type and strike is not None and front and back:
        return build_structure_id(opportunity_id, str(option_type).lower(), strike, str(front), str(back))
    return None


def _collapse_parent_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one parent strategy row per ticker+earnings event.

    Structure attempts stay nested in `structure_attempt_summary`; they are not
    separate strategy opportunities.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    passthrough: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        opp_id = str(row.get("opportunity_id") or "")
        if not opp_id:
            passthrough.append(row)
            continue
        grouped.setdefault(opp_id, []).append(row)

    collapsed: list[dict[str, Any]] = []
    for opp_id, group in grouped.items():
        group.sort(key=_parent_preference_key, reverse=True)
        parent = dict(group[0])
        attempts = [_attempt_summary(row) for row in group]
        disposition_counts: dict[str, int] = {}
        for attempt in attempts:
            code = str(attempt.get("disposition_code") or "UNKNOWN")
            disposition_counts[code] = disposition_counts.get(code, 0) + 1
        parent["row_id"] = opp_id
        parent["opportunity_id"] = opp_id
        parent["structure_attempt_summary"] = {
            "attempt_count": len(attempts),
            "current_structure_id": parent.get("current_structure_id"),
            "rejected_attempt_count": sum(1 for attempt in attempts if _is_rejected_attempt(attempt)),
            "disposition_counts": disposition_counts,
            "attempts": attempts,
        }
        parent["duplicate_parent_rows_collapsed"] = max(0, len(group) - 1)
        collapsed.append(parent)
    return collapsed + passthrough


def _parent_preference_key(row: dict[str, Any]) -> tuple[int, int, float]:
    has_structure = 1 if row.get("current_structure_id") or row.get("possible_spread") or row.get("candidate") else 0
    entry = 1 if row.get("entry_allowed") else 0
    try:
        score = float(row.get("score") or 0)
    except Exception:
        score = 0.0
    return has_structure, entry, score


def _attempt_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "structure_id": row.get("current_structure_id") or row.get("structure_id"),
        "disposition_code": row.get("disposition_code"),
        "disposition_reason": row.get("disposition_reason"),
        "evaluation_state": row.get("evaluation_state"),
        "trade_verdict": row.get("trade_verdict"),
        "entry_window_status": row.get("entry_window_status"),
        "blocker_code": row.get("blocker_code"),
    }


def _is_rejected_attempt(attempt: dict[str, Any]) -> bool:
    state = str(attempt.get("evaluation_state") or "")
    verdict = str(attempt.get("trade_verdict") or "")
    return state in {EvaluationState.STRUCTURE_UNAVAILABLE, EvaluationState.ERROR} or verdict in {Verdict.FAIL, Verdict.BLOCKED}


def _row_event_date(row: dict[str, Any]) -> date | None:
    earnings = row.get("earnings") if isinstance(row.get("earnings"), dict) else {}
    quality = row.get("quality_precheck") if isinstance(row.get("quality_precheck"), dict) else {}
    for value in (
        row.get("earnings_date"),
        earnings.get("earnings_date"),
        earnings.get("date"),
        quality.get("earnings_date"),
        quality.get("date"),
    ):
        if not value:
            continue
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
        except Exception:
            continue
    return None


def _by_ticker(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("ticker") or item.get("symbol") or "").upper().strip(): item
        for item in rows
        if isinstance(item, dict) and str(item.get("ticker") or item.get("symbol") or "").strip()
    }


def _raw_candidate_verdict(candidate: dict[str, Any], strategy: dict[str, Any], quality_row: dict[str, Any]) -> str:
    status = str(quality_row.get("entry_window_status") or "")
    if not candidate:
        if status == "MONITOR_PRE_WINDOW":
            return "MONITOR / PRE-WINDOW"
        if status == "DATA_NEEDED":
            return "MONITOR / DATA_NEEDED"
        if status in {"ENTRY_WINDOW_OPEN", "ENTRY_WINDOW_CLOSING"}:
            return f"WATCH / {status}"
        if status:
            return f"NOT_EVALUATED / {status}"
        return "NOT_EVALUATED / NO VALID CALENDAR STRUCTURE"
    action = str(strategy.get("action") or "").upper()
    if strategy.get("is_preferred_setup") or "EARNINGS CALENDAR CANDIDATE" in action:
        return "PASS / POSSIBLE ENTRY SETUP"
    if "URGENT" in action:
        return "WATCH / URGENT MANUAL REVIEW"
    if "MANUAL REVIEW" in action:
        return "WATCH / TIMESTAMP NEEDED"
    if "AVOID" in action or "NOT AN EARNINGS" in action:
        return "FAIL / NOT AN EARNINGS CALENDAR"
    return "WATCH / STRUCTURE FOUND"


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


def _requirements(event: dict[str, Any], quality_row: dict[str, Any], candidate: dict[str, Any], trust: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    rows.append({
        "name": "Upcoming earnings event",
        "status": "PASS" if (event.get("earnings_date") or event.get("date")) else "FAIL",
        "detail": str(event.get("earnings_date") or event.get("date") or "No upcoming earnings event was attached."),
    })
    rows.append({
        "name": "Earnings date trust",
        "status": "PASS" if trust.get("earnings_trust_label") == "multi_source_confirmed" else "WARN",
        "detail": str(trust.get("earnings_trust_reason") or ""),
    })
    for check in (quality_row.get("checks") or [])[:8]:
        if isinstance(check, dict):
            rows.append({
                "name": f"Precheck: {check.get('name') or 'quality'}",
                "status": str(check.get("status") or "WARN"),
                "detail": str(check.get("detail") or ""),
            })
    rows.append({
        "name": "Calendar structure",
        "status": "PASS" if candidate else "WARN",
        "detail": "Candidate structure exists." if candidate else str(quality_row.get("entry_window_reason") or quality_row.get("primary_rejection_reason") or "No current safe structure."),
    })
    return rows


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
    if dte is not None and 1 <= dte <= 14:
        score += 10.0
    if event.get("is_timestamp_confirmed"):
        score += 10.0
    return score


def _score_open_position(item: dict[str, Any]) -> float:
    explicit = _float_or_none(item.get("lifecycle_priority_score"))
    if explicit is not None:
        return explicit
    action = str(item.get("action") or "").upper()
    if "URGENT" in action:
        return 95.0
    if "CUT" in action or "EXIT" in action:
        return 90.0
    if "TAKE PROFIT" in action:
        return 88.0
    if "RECHECK" in action or "EVENT" in action:
        return 78.0
    return 65.0


def _open_structure(item: dict[str, Any]) -> str:
    strike = item.get("strike")
    opt_type = str(item.get("option_type") or "call").upper()
    front = item.get("front_expiration")
    back = item.get("back_expiration")
    return f"{strike if strike is not None else '-'} {opt_type} | short {front or '-'} / long {back or '-'}"


def _open_value_summary(item: dict[str, Any]) -> str:
    current = item.get("current_mid_debit")
    entry = item.get("entry_debit_estimate")
    parts = []
    if current is not None:
        parts.append(f"current debit {float(current):.2f}")
    if entry is not None:
        parts.append(f"entry debit est. {float(entry):.2f}")
    return " | ".join(parts) if parts else "Value unavailable"


def _summary(new_rows: list[dict[str, Any]], open_rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "new_trade_count": len(new_rows),
        "open_trade_count": len(open_rows),
        "pass_count": sum(1 for row in new_rows if row.get("trade_verdict") == Verdict.PASS),
        "watch_count": sum(1 for row in new_rows if row.get("trade_verdict") == Verdict.WATCH),
        "fail_count": sum(1 for row in new_rows if row.get("trade_verdict") in {Verdict.FAIL, Verdict.BLOCKED}),
        "not_evaluated_count": sum(1 for row in new_rows if row.get("trade_verdict") == Verdict.NOT_EVALUATED),
        "has_new_candidates": bool(new_rows),
        "has_open_calendars": bool(open_rows),
    }


def _decision_audit(new_rows: list[dict[str, Any]], open_rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {"invariant_violations": 0}
    for row in new_rows + open_rows:
        state = str(row.get("evaluation_state") or "")
        verdict = str(row.get("trade_verdict") or "")
        counts[state] = counts.get(state, 0) + 1
        counts[verdict] = counts.get(verdict, 0) + 1
        if not row.get("entry_evaluation_eligible") and verdict != Verdict.NOT_EVALUATED and row.get("lifecycle_stage") != LifecycleStage.OPEN_POSITION:
            counts["invariant_violations"] += 1
        if verdict in {Verdict.PASS, Verdict.WATCH, Verdict.NEAR_MISS, Verdict.FAIL} and state != EvaluationState.FULLY_EVALUATED:
            counts["invariant_violations"] += 1
        if not row.get("recommended_action"):
            counts["invariant_violations"] += 1
    return counts


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
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out
