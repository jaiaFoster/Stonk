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
from app.services.calendar_opportunity_lifecycle_adapter import (
    build_calendar_lifecycle_opportunity,
    build_opportunity_id,
    build_structure_id,
)


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
    engine["calendar_row_reconciliation"] = build_calendar_row_reconciliation(engine)
    return engine


def build_calendar_row_reconciliation(
    engine: dict[str, Any] | None,
    *,
    persisted_rows: int | None = None,
    api_visible_rows: int | None = None,
    history_rows: int | None = None,
    journal_rows: int | None = None,
) -> dict[str, Any]:
    engine = engine or {}
    generated = sum(len(engine.get(key) or []) for key in ("new_trade_rows", "open_trade_rows", "blocked_rows"))
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
        "generated_rows": generated,
        "normalized_rows": generated - invalid,
        "duplicate_rows": duplicates,
        "invalid_rows": invalid,
        "persisted_rows": persisted_rows,
        "api_rows": api_visible_rows,
        "daily_opportunity_rows": daily_visible,
        "history_rows": history_rows,
        "journal_rows": journal_rows,
        "excluded_rows_by_reason": excluded,
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
    row["evaluation_state"] = opp.evaluation_state
    row["trade_verdict"] = _trade_verdict(row, opp.verdict, status)
    row["recommended_action"] = opp.recommended_action
    row["build_eligible"] = opp.build_eligible
    row["surface_eligible"] = opp.surface_eligible
    row["entry_evaluation_eligible"] = bool(policy.is_entry_allowed(days_until))
    row["entry_allowed"] = bool(row.get("calendar_entry_allowed") or opp.entry_allowed)
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
    row.setdefault("row_id", row.get("structure_id") or row.get("opportunity_id"))


def _structure_state(row: dict[str, Any], status: str) -> str | None:
    if status in {"DATA_NEEDED", "DATE_CONFLICT_REVIEW"}:
        return EvaluationState.DATA_INCOMPLETE
    if status in {"ENTRY_WINDOW_CLOSED", "NO_PRE_EARNINGS_SHORT_EXPIRY", "SHORT_LEG_SPANS_EARNINGS", "SHORT_DTE_TOO_LOW", "FRONT_LEG_TOO_DECAYED"}:
        return EvaluationState.STRUCTURE_UNAVAILABLE
    if row.get("possible_spread") or row.get("candidate") or row.get("front_expiration"):
        return EvaluationState.FULLY_EVALUATED if bool(row.get("entry_allowed") or row.get("calendar_entry_allowed")) else EvaluationState.STRUCTURE_COMPLETE
    return None


def _trade_verdict(row: dict[str, Any], default: str, status: str) -> str:
    verdict = str(row.get("verdict") or default or Verdict.NOT_EVALUATED).upper()
    if verdict.startswith("FAIL"):
        return Verdict.BLOCKED if status else Verdict.FAIL
    if verdict.startswith("PASS"):
        return Verdict.PASS
    if verdict.startswith("WATCH"):
        return Verdict.WATCH
    if verdict.startswith("NEAR"):
        return Verdict.NEAR_MISS
    return default or Verdict.NOT_EVALUATED


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
