"""
app/services/strategy_opportunity_lifecycle_service.py — Generic lifecycle validation.

Patch 33A.1: Universal lifecycle kernel. Provides invariant validation,
canonical object construction, and compatibility projection helpers.
Does NOT call any provider. Operates on pre-classified data.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.models.strategy_opportunity_lifecycle import (
    EvaluationState,
    LifecycleClassification,
    LifecycleStage,
    OpportunityClock,
    RecommendedAction,
    StrategyOpportunity,
    Verdict,
)


# ─── Invariant validation ──────────────────────────────────────────────────────

def validate_lifecycle_classification(classification: LifecycleClassification) -> list[str]:
    """Return a list of invariant violation messages (empty = valid)."""
    errors: list[str] = []

    if not LifecycleStage.is_valid(classification.lifecycle_stage):
        errors.append(f"Unknown lifecycle_stage: {classification.lifecycle_stage!r}")

    if not EvaluationState.is_valid(classification.evaluation_state):
        errors.append(f"Unknown evaluation_state: {classification.evaluation_state!r}")

    if not Verdict.is_valid(classification.verdict):
        errors.append(f"Unknown verdict: {classification.verdict!r}")

    if not RecommendedAction.is_valid(classification.recommended_action):
        errors.append(f"Unknown recommended_action: {classification.recommended_action!r}")

    # EXPECTED_MISSING / DEFERRED_BUDGET are non-failure states; verdict must not be FAIL
    if (
        EvaluationState.is_non_failure(classification.evaluation_state)
        and classification.verdict == Verdict.FAIL
    ):
        errors.append(
            f"verdict=FAIL is invalid when evaluation_state={classification.evaluation_state} "
            f"(budget skips and expected-missing data are not strategy failures)"
        )

    # entry_allowed requires surface_eligible
    if classification.entry_allowed and not classification.surface_eligible:
        errors.append("entry_allowed=True requires surface_eligible=True")

    # surface_eligible requires build_eligible
    if classification.surface_eligible and not classification.build_eligible:
        errors.append("surface_eligible=True requires build_eligible=True")

    # POST_EVENT / INVALIDATED / TERMINAL should not be entry_allowed
    if classification.lifecycle_stage in {
        LifecycleStage.POST_EVENT, LifecycleStage.INVALIDATED, LifecycleStage.TERMINAL
    } and classification.entry_allowed:
        errors.append(
            f"entry_allowed=True is invalid when lifecycle_stage={classification.lifecycle_stage}"
        )

    return errors


# ─── Canonical object construction ────────────────────────────────────────────

def build_strategy_opportunity(
    opportunity_id: str,
    strategy_id: str,
    ticker: str,
    classification: LifecycleClassification,
    event_date: date,
    evaluation_date: date | None = None,
    source_rows: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> StrategyOpportunity:
    """Construct a canonical StrategyOpportunity from classification results."""
    today = evaluation_date or date.today()
    days_until_event = (event_date - today).days
    clock = OpportunityClock(
        event_date=event_date,
        days_until_event=days_until_event,
        evaluation_date=today,
    )
    return StrategyOpportunity(
        opportunity_id=opportunity_id,
        strategy_id=strategy_id,
        ticker=ticker,
        lifecycle_stage=classification.lifecycle_stage,
        evaluation_state=classification.evaluation_state,
        verdict=classification.verdict,
        recommended_action=classification.recommended_action,
        clock=clock,
        build_eligible=classification.build_eligible,
        surface_eligible=classification.surface_eligible,
        entry_allowed=classification.entry_allowed,
        source_rows=list(source_rows or []),
        metadata=dict(metadata or {}),
    )


# ─── Compatibility projection ─────────────────────────────────────────────────

def project_to_strategy_row(opportunity: StrategyOpportunity) -> dict[str, Any]:
    """
    Project a StrategyOpportunity back to the flat strategy-row dict format
    consumed by StrategyRowRepository and the existing API surface.

    This enables gradual migration: new lifecycle fields appear alongside
    existing fields without breaking downstream consumers.
    """
    row: dict[str, Any] = {
        "opportunity_id": opportunity.opportunity_id,
        "ticker": opportunity.ticker,
        "strategy_id": opportunity.strategy_id,
        "lifecycle_stage": opportunity.lifecycle_stage,
        "evaluation_state": opportunity.evaluation_state,
        "verdict": opportunity.verdict,
        "recommended_action": opportunity.recommended_action,
        "build_eligible": opportunity.build_eligible,
        "surface_eligible": opportunity.surface_eligible,
        "entry_allowed": opportunity.entry_allowed,
        "event_date": opportunity.clock.event_date.isoformat(),
        "days_until_event": opportunity.clock.days_until_event,
        "evaluation_date": opportunity.clock.evaluation_date.isoformat(),
    }
    row.update(opportunity.metadata)
    return row


def summarize_lifecycle_batch(
    opportunities: list[StrategyOpportunity],
) -> dict[str, Any]:
    """Return a summary dict suitable for the /api/dev/strategy-lifecycle endpoint."""
    by_stage: dict[str, int] = {}
    by_state: dict[str, int] = {}
    by_verdict: dict[str, int] = {}
    entry_allowed_count = 0
    surface_eligible_count = 0

    for opp in opportunities:
        by_stage[opp.lifecycle_stage] = by_stage.get(opp.lifecycle_stage, 0) + 1
        by_state[opp.evaluation_state] = by_state.get(opp.evaluation_state, 0) + 1
        by_verdict[opp.verdict] = by_verdict.get(opp.verdict, 0) + 1
        if opp.entry_allowed:
            entry_allowed_count += 1
        if opp.surface_eligible:
            surface_eligible_count += 1

    return {
        "total": len(opportunities),
        "by_lifecycle_stage": by_stage,
        "by_evaluation_state": by_state,
        "by_verdict": by_verdict,
        "entry_allowed_count": entry_allowed_count,
        "surface_eligible_count": surface_eligible_count,
    }
