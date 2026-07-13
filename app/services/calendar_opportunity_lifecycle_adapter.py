"""
app/services/calendar_opportunity_lifecycle_adapter.py — Calendar lifecycle classifier.

Patch 33A.1: Earnings-calendar-specific lifecycle classification.
Maps event DTE to lifecycle stage, evaluation state, verdict, and action.
Builds stable opportunity_id from ticker + earnings_date.
Does NOT call any provider.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.models.calendar_evolution_policy import CalendarEvolutionPolicy
from app.models.strategy_opportunity_lifecycle import (
    EvaluationState,
    LifecycleClassification,
    LifecycleStage,
    RecommendedAction,
    StrategyOpportunity,
    Verdict,
)
from app.services.strategy_opportunity_lifecycle_service import (
    build_strategy_opportunity,
    validate_lifecycle_classification,
)

STRATEGY_ID = "earnings_calendar"


# ─── Opportunity identity ─────────────────────────────────────────────────────

def build_opportunity_id(ticker: str, earnings_date: date | str) -> str:
    """
    Build stable parent opportunity_id.
    Format: earnings_calendar:<TICKER>:<YYYY-MM-DD>
    Stable across structure changes — only ticker + event date matter.
    """
    date_str = earnings_date.isoformat() if isinstance(earnings_date, date) else str(earnings_date)[:10]
    return f"{STRATEGY_ID}:{ticker.upper()}:{date_str}"


def build_structure_id(
    opportunity_id: str,
    option_type: str,
    strike: float | str,
    front_expiration: str,
    back_expiration: str,
) -> str:
    """Structure identity is separate from opportunity identity."""
    return f"{opportunity_id}:{option_type}:{strike}:{front_expiration}:{back_expiration}"


# ─── Lifecycle classification ─────────────────────────────────────────────────

def classify_calendar_opportunity(
    days_until_event: int,
    policy: CalendarEvolutionPolicy,
    has_structure: bool = False,
    structure_evaluation_state: str | None = None,
    has_open_position: bool = False,
) -> LifecycleClassification:
    """
    Classify an earnings-calendar opportunity into lifecycle dimensions.

    Args:
        days_until_event: Calendar days until the earnings event (negative = past).
        policy: CalendarEvolutionPolicy defining all timing thresholds.
        has_structure: Whether a valid expiration pair/structure was built.
        structure_evaluation_state: Override evaluation state (e.g., DEFERRED_BUDGET).
        has_open_position: Whether a position is currently held.

    Returns:
        LifecycleClassification with all three state dimensions populated.
    """
    # ── Open position ──────────────────────────────────────────────────────────
    if has_open_position:
        return LifecycleClassification(
            lifecycle_stage=LifecycleStage.OPEN_POSITION,
            evaluation_state=EvaluationState.FULLY_EVALUATED,
            verdict=Verdict.PASS,
            recommended_action=RecommendedAction.HOLD,
            build_eligible=True,
            surface_eligible=True,
            entry_allowed=False,
            classification_reason="active_open_position",
        )

    # ── Post-event ─────────────────────────────────────────────────────────────
    if days_until_event < 0:
        return LifecycleClassification(
            lifecycle_stage=LifecycleStage.POST_EVENT,
            evaluation_state=EvaluationState.NOT_REQUESTED,
            verdict=Verdict.NOT_EVALUATED,
            recommended_action=RecommendedAction.NONE,
            build_eligible=False,
            surface_eligible=False,
            entry_allowed=False,
            classification_reason="event_has_passed",
        )

    # ── Outside discovery window ───────────────────────────────────────────────
    if days_until_event > policy.discovery_end_event_dte:
        return LifecycleClassification(
            lifecycle_stage=LifecycleStage.OUTSIDE_WINDOW,
            evaluation_state=EvaluationState.NOT_REQUESTED,
            verdict=Verdict.NOT_EVALUATED,
            recommended_action=RecommendedAction.NONE,
            build_eligible=False,
            surface_eligible=False,
            entry_allowed=False,
            classification_reason=f"days_until_event={days_until_event} > discovery_end={policy.discovery_end_event_dte}",
        )

    # ── Within discovery window — determine build/surface eligibility ──────────
    build_eligible = policy.is_build_eligible(days_until_event)
    surface_eligible = policy.is_surface_eligible(days_until_event)
    entry_allowed = policy.is_entry_allowed(days_until_event) and surface_eligible

    # ── Lifecycle stage ───────────────────────────────────────────────────────
    if days_until_event > policy.build_start_event_dte:
        # 25–35 DTE: early discovery, structure not yet attempted
        lifecycle_stage = LifecycleStage.DISCOVERED
    elif days_until_event > policy.surface_start_event_dte:
        # 15–24 DTE: structure building phase
        lifecycle_stage = LifecycleStage.DEVELOPING
    elif days_until_event > policy.ideal_entry_max_event_dte:
        # 13–14 DTE: surfaced, approaching entry window
        lifecycle_stage = LifecycleStage.SURFACED
    elif days_until_event >= policy.late_entry_event_dte:
        # 4–12 DTE: actionable entry window
        lifecycle_stage = LifecycleStage.ACTIONABLE
    else:
        # 0–3 DTE: too late / closing
        lifecycle_stage = LifecycleStage.ACTIONABLE

    # ── Evaluation state ──────────────────────────────────────────────────────
    if structure_evaluation_state and EvaluationState.is_valid(structure_evaluation_state):
        eval_state = structure_evaluation_state
    elif not build_eligible:
        # 25–35 DTE: structure building not yet started (by design)
        eval_state = EvaluationState.EXPECTED_MISSING
    elif has_structure:
        eval_state = EvaluationState.STRUCTURE_COMPLETE if surface_eligible else EvaluationState.BUILDING
    else:
        eval_state = EvaluationState.STRUCTURE_UNAVAILABLE if surface_eligible else EvaluationState.BUILDING

    # ── Verdict ───────────────────────────────────────────────────────────────
    if EvaluationState.is_non_failure(eval_state):
        verdict = Verdict.NOT_EVALUATED
    elif not surface_eligible:
        verdict = Verdict.NOT_EVALUATED
    elif has_structure and entry_allowed:
        verdict = Verdict.PASS
    elif has_structure:
        verdict = Verdict.WATCH
    else:
        verdict = Verdict.NOT_EVALUATED

    # ── Recommended action ────────────────────────────────────────────────────
    if eval_state == EvaluationState.EXPECTED_MISSING:
        recommended_action = RecommendedAction.MONITOR
    elif lifecycle_stage == LifecycleStage.DISCOVERED:
        recommended_action = RecommendedAction.MONITOR
    elif lifecycle_stage == LifecycleStage.DEVELOPING:
        recommended_action = RecommendedAction.PREPARE
    elif lifecycle_stage == LifecycleStage.SURFACED:
        recommended_action = RecommendedAction.PREPARE
    elif lifecycle_stage == LifecycleStage.ACTIONABLE and entry_allowed and has_structure:
        recommended_action = RecommendedAction.ENTER
    elif lifecycle_stage == LifecycleStage.ACTIONABLE:
        recommended_action = RecommendedAction.MONITOR
    else:
        recommended_action = RecommendedAction.NONE

    reason = (
        f"days_until_event={days_until_event} "
        f"build_eligible={build_eligible} surface_eligible={surface_eligible} "
        f"entry_allowed={entry_allowed} has_structure={has_structure}"
    )

    return LifecycleClassification(
        lifecycle_stage=lifecycle_stage,
        evaluation_state=eval_state,
        verdict=verdict,
        recommended_action=recommended_action,
        build_eligible=build_eligible,
        surface_eligible=surface_eligible,
        entry_allowed=entry_allowed,
        classification_reason=reason,
    )


# ─── Top-level adapter ────────────────────────────────────────────────────────

def build_calendar_lifecycle_opportunity(
    ticker: str,
    earnings_date: date | str,
    days_until_event: int,
    policy: CalendarEvolutionPolicy,
    has_structure: bool = False,
    structure_evaluation_state: str | None = None,
    has_open_position: bool = False,
    evaluation_date: date | None = None,
    source_rows: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[StrategyOpportunity, list[str]]:
    """
    Build a fully-validated StrategyOpportunity for an earnings-calendar ticker.

    Returns (opportunity, validation_errors).
    validation_errors is empty when all invariants pass.
    """
    if isinstance(earnings_date, str):
        from datetime import datetime
        earnings_date = datetime.strptime(earnings_date[:10], "%Y-%m-%d").date()

    opportunity_id = build_opportunity_id(ticker, earnings_date)
    classification = classify_calendar_opportunity(
        days_until_event=days_until_event,
        policy=policy,
        has_structure=has_structure,
        structure_evaluation_state=structure_evaluation_state,
        has_open_position=has_open_position,
    )
    errors = validate_lifecycle_classification(classification)
    opportunity = build_strategy_opportunity(
        opportunity_id=opportunity_id,
        strategy_id=STRATEGY_ID,
        ticker=ticker,
        classification=classification,
        event_date=earnings_date,
        evaluation_date=evaluation_date,
        source_rows=source_rows,
        metadata=metadata or {},
    )
    return opportunity, errors


def lifecycle_rows_from_discovery(
    quality_filter_result: dict[str, Any],
    policy: CalendarEvolutionPolicy,
    evaluation_date: date | None = None,
) -> list[dict[str, Any]]:
    """
    Generate lifecycle monitor rows for all opportunities in a quality-filter result.

    This is the bridge between the discovery quality filter (which classifies
    ticker-level support/optionability) and the lifecycle kernel.

    Early-stage opportunities (25–35 DTE) are returned as DISCOVERED/EXPECTED_MISSING
    monitor rows so they persist and don't disappear from the API surface.
    """
    rows: list[dict[str, Any]] = []
    today = evaluation_date or date.today()

    for item in quality_filter_result.get("items") or []:
        ticker = str(item.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        event = item.get("event") or item
        earnings_date_str = (
            event.get("earnings_date") or event.get("date") or item.get("earnings_date")
        )
        if not earnings_date_str:
            continue
        try:
            from datetime import datetime
            earnings_date = datetime.strptime(str(earnings_date_str)[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue

        days_until = (earnings_date - today).days
        if not policy.is_in_discovery_window(days_until):
            continue

        has_structure = bool(
            item.get("front_expiration") and item.get("back_expiration")
        )
        structure_eval_state = None
        if item.get("exit_stage") in {"DEV_MODE_BUDGET_NOT_SELECTED", "QUALITY_FILTER_BUDGET_NOT_SELECTED"}:
            structure_eval_state = EvaluationState.DEFERRED_BUDGET

        opportunity, val_errors = build_calendar_lifecycle_opportunity(
            ticker=ticker,
            earnings_date=earnings_date,
            days_until_event=days_until,
            policy=policy,
            has_structure=has_structure,
            structure_evaluation_state=structure_eval_state,
            evaluation_date=today,
            metadata={
                "source": "quality_filter_lifecycle_bridge",
                "passes_precheck": bool(item.get("passes_precheck")),
            },
        )
        if val_errors:
            opportunity.metadata["lifecycle_validation_errors"] = val_errors
        row = opportunity.to_dict()
        row["row_type"] = "lifecycle_monitor"
        row["strategy"] = STRATEGY_ID
        rows.append(row)

    return rows
