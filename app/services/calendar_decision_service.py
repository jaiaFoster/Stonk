"""Canonical earnings-calendar decision service.

Patch 33C: this is the sole owner of final calendar trade semantics:
evaluation_state, trade_verdict, recommended_action, entry_allowed, and
decision blockers. It consumes lifecycle classification plus already-computed
structure/fact fields; it does not fetch providers, build structures, persist
rows, or create API payloads.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.models.strategy_opportunity_lifecycle import EvaluationState, LifecycleStage, RecommendedAction, Verdict


@dataclass(frozen=True)
class CalendarDecision:
    evaluation_state: str
    trade_verdict: str
    recommended_action: str
    entry_allowed: bool
    blockers: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    decision_source: str = "calendar_decision_service"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def decide_calendar_opportunity(
    row: dict[str, Any],
    *,
    lifecycle_stage: str,
    lifecycle_evaluation_state: str,
    lifecycle_recommended_action: str,
    entry_evaluation_eligible: bool,
    structure_available: bool,
) -> CalendarDecision:
    """Return the canonical calendar decision for one parent opportunity row."""
    blockers = _blockers(row)
    reasons = _reasons(row)
    legacy_verdict = str(row.get("verdict") or row.get("final_verdict") or "").upper()
    state = _normalize_evaluation_state(row, lifecycle_evaluation_state, structure_available)
    status = str(row.get("entry_window_status") or row.get("disposition_code") or "")

    if lifecycle_stage == LifecycleStage.OPEN_POSITION:
        return CalendarDecision(
            evaluation_state=EvaluationState.FULLY_EVALUATED,
            trade_verdict=Verdict.WATCH,
            recommended_action=RecommendedAction.HOLD,
            entry_allowed=False,
            blockers=blockers,
            reasons=reasons or ["Open calendar position is managed by lifecycle review."],
        )

    if state == EvaluationState.DEFERRED_BUDGET:
        return CalendarDecision(
            evaluation_state=state,
            trade_verdict=Verdict.NOT_EVALUATED,
            recommended_action=RecommendedAction.NONE,
            entry_allowed=False,
            blockers=blockers,
            reasons=reasons or ["Calendar evaluation deferred by provider or dev budget."],
        )

    trust_label = str(row.get("earnings_trust_label") or "")
    if row.get("calendar_entry_allowed") is False and trust_label in {"conflict_do_not_trade", "unknown_research_only"}:
        return CalendarDecision(
            evaluation_state=EvaluationState.DATA_INCOMPLETE,
            trade_verdict=Verdict.NOT_EVALUATED,
            recommended_action=RecommendedAction.NONE,
            entry_allowed=False,
            blockers=blockers or [str(row.get("earnings_trust_reason") or "Earnings date trust does not allow entry.")],
            reasons=reasons,
        )

    if status == "MONITOR_PRE_WINDOW":
        return CalendarDecision(
            evaluation_state=EvaluationState.STRUCTURE_COMPLETE if structure_available else state,
            trade_verdict=Verdict.NOT_EVALUATED,
            recommended_action=RecommendedAction.MONITOR,
            entry_allowed=False,
            blockers=blockers,
            reasons=reasons or ["Calendar opportunity is before the approved entry-evaluation window."],
        )

    if lifecycle_stage in {LifecycleStage.DISCOVERED, LifecycleStage.DEVELOPING, LifecycleStage.SURFACED}:
        pre_entry_state = EvaluationState.STRUCTURE_COMPLETE if structure_available else state
        action = RecommendedAction.MONITOR if lifecycle_stage == LifecycleStage.DISCOVERED else RecommendedAction.PREPARE
        return CalendarDecision(
            evaluation_state=pre_entry_state,
            trade_verdict=Verdict.NOT_EVALUATED,
            recommended_action=action,
            entry_allowed=False,
            blockers=blockers,
            reasons=reasons or ["Calendar opportunity is before the approved entry-evaluation window."],
        )

    if not entry_evaluation_eligible:
        return CalendarDecision(
            evaluation_state=state,
            trade_verdict=Verdict.NOT_EVALUATED,
            recommended_action=RecommendedAction.MONITOR if lifecycle_stage == LifecycleStage.ACTIONABLE else lifecycle_recommended_action,
            entry_allowed=False,
            blockers=blockers,
            reasons=reasons or ["Calendar opportunity is not in the approved entry-evaluation window."],
        )

    if state in {EvaluationState.DATA_INCOMPLETE, EvaluationState.ERROR, EvaluationState.STALE}:
        return CalendarDecision(
            evaluation_state=state,
            trade_verdict=Verdict.NOT_EVALUATED,
            recommended_action=RecommendedAction.REVIEW,
            entry_allowed=False,
            blockers=blockers,
            reasons=reasons or ["Calendar data is incomplete or stale; no entry decision allowed."],
        )

    if state == EvaluationState.STRUCTURE_UNAVAILABLE:
        return CalendarDecision(
            evaluation_state=state,
            trade_verdict=Verdict.NOT_EVALUATED,
            recommended_action=RecommendedAction.NONE,
            entry_allowed=False,
            blockers=blockers or ["No valid calendar structure is available."],
            reasons=reasons,
        )

    if legacy_verdict.startswith("PASS") and not blockers:
        return CalendarDecision(
            evaluation_state=EvaluationState.FULLY_EVALUATED,
            trade_verdict=Verdict.PASS,
            recommended_action=RecommendedAction.ENTER,
            entry_allowed=True,
            blockers=[],
            reasons=reasons or ["Calendar structure passed current measurable entry gates."],
        )

    if legacy_verdict.startswith("WATCH") or legacy_verdict.startswith("MONITOR"):
        return CalendarDecision(
            evaluation_state=EvaluationState.FULLY_EVALUATED if structure_available else state,
            trade_verdict=Verdict.WATCH,
            recommended_action=RecommendedAction.REVIEW,
            entry_allowed=False,
            blockers=blockers,
            reasons=reasons or ["Calendar structure requires manual review before entry."],
        )

    if legacy_verdict.startswith("NEAR"):
        return CalendarDecision(
            evaluation_state=EvaluationState.FULLY_EVALUATED if structure_available else state,
            trade_verdict=Verdict.NEAR_MISS,
            recommended_action=RecommendedAction.MONITOR,
            entry_allowed=False,
            blockers=blockers,
            reasons=reasons or ["Calendar setup is near the entry criteria but not cleanly actionable."],
        )

    if legacy_verdict.startswith("FAIL") or blockers:
        return CalendarDecision(
            evaluation_state=EvaluationState.FULLY_EVALUATED if structure_available else state,
            trade_verdict=Verdict.BLOCKED,
            recommended_action=RecommendedAction.AVOID,
            entry_allowed=False,
            blockers=blockers or [str(row.get("main_blocker") or row.get("primary_reason") or "Calendar entry gate failed.")],
            reasons=reasons,
        )

    return CalendarDecision(
        evaluation_state=state,
        trade_verdict=Verdict.NOT_EVALUATED,
        recommended_action=RecommendedAction.MONITOR,
        entry_allowed=False,
        blockers=blockers,
        reasons=reasons,
    )


def _normalize_evaluation_state(row: dict[str, Any], lifecycle_state: str, structure_available: bool) -> str:
    status = str(row.get("entry_window_status") or row.get("disposition_code") or "")
    if status == "DEV_MODE_BUDGET_NOT_SELECTED" or str(row.get("exit_reason") or "") == "DEV_MODE_BUDGET_NOT_SELECTED":
        return EvaluationState.DEFERRED_BUDGET
    if status in {"DATA_NEEDED", "DATE_CONFLICT_REVIEW"}:
        return EvaluationState.DATA_INCOMPLETE
    if status in {
        "ENTRY_WINDOW_CLOSED",
        "NO_PRE_EARNINGS_SHORT_EXPIRY",
        "SHORT_LEG_SPANS_EARNINGS",
        "SHORT_DTE_TOO_LOW",
        "FRONT_LEG_TOO_DECAYED",
    }:
        return EvaluationState.STRUCTURE_UNAVAILABLE
    if structure_available:
        return EvaluationState.FULLY_EVALUATED
    return lifecycle_state if EvaluationState.is_valid(lifecycle_state) else EvaluationState.NOT_REQUESTED


def _blockers(row: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("main_blocker", "hard_fail_reason", "blocker_detail", "entry_window_reason", "primary_rejection_reason"):
        value = row.get(key)
        if value:
            values.append(str(value))
    for item in row.get("risks") or []:
        if item:
            values.append(str(item))
    return _dedupe(values)


def _reasons(row: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("main_reason", "primary_reason", "disposition_reason"):
        value = row.get(key)
        if value:
            values.append(str(value))
    for item in row.get("reasons") or []:
        if item:
            values.append(str(item))
    return _dedupe(values)


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
    return out
