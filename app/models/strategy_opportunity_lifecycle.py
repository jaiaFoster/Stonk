"""
app/models/strategy_opportunity_lifecycle.py — Generic Strategy Opportunity Lifecycle models.

Patch 33A.1: Universal lifecycle kernel. Earnings calendar is the first migrated strategy.
Three independent state dimensions: lifecycle stage, evaluation state, verdict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


class LifecycleStage:
    """Generic opportunity lifecycle stages (strategy-agnostic)."""
    OUTSIDE_WINDOW = "OUTSIDE_WINDOW"
    DISCOVERED = "DISCOVERED"
    DEVELOPING = "DEVELOPING"
    SURFACED = "SURFACED"
    ACTIONABLE = "ACTIONABLE"
    OPEN_POSITION = "OPEN_POSITION"
    POST_EVENT = "POST_EVENT"
    INVALIDATED = "INVALIDATED"
    TERMINAL = "TERMINAL"

    _ALL = {
        OUTSIDE_WINDOW, DISCOVERED, DEVELOPING, SURFACED, ACTIONABLE,
        OPEN_POSITION, POST_EVENT, INVALIDATED, TERMINAL,
    }

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in cls._ALL

    @classmethod
    def is_active(cls, value: str) -> bool:
        return value in {cls.DISCOVERED, cls.DEVELOPING, cls.SURFACED, cls.ACTIONABLE, cls.OPEN_POSITION}


class EvaluationState:
    """State of the structure evaluation process."""
    NOT_REQUESTED = "NOT_REQUESTED"
    EXPECTED_MISSING = "EXPECTED_MISSING"
    DEFERRED_BUDGET = "DEFERRED_BUDGET"
    DATA_INCOMPLETE = "DATA_INCOMPLETE"
    BUILDING = "BUILDING"
    STRUCTURE_COMPLETE = "STRUCTURE_COMPLETE"
    STRUCTURE_UNAVAILABLE = "STRUCTURE_UNAVAILABLE"
    FULLY_EVALUATED = "FULLY_EVALUATED"
    STALE = "STALE"
    ERROR = "ERROR"

    _ALL = {
        NOT_REQUESTED, EXPECTED_MISSING, DEFERRED_BUDGET, DATA_INCOMPLETE,
        BUILDING, STRUCTURE_COMPLETE, STRUCTURE_UNAVAILABLE, FULLY_EVALUATED,
        STALE, ERROR,
    }

    _NON_FAILURE_STATES = {NOT_REQUESTED, EXPECTED_MISSING, DEFERRED_BUDGET}

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in cls._ALL

    @classmethod
    def is_non_failure(cls, value: str) -> bool:
        """Budget skips and expected-missing data are not strategy failures."""
        return value in cls._NON_FAILURE_STATES


class Verdict:
    """Final evaluation verdict for an opportunity."""
    NOT_EVALUATED = "NOT_EVALUATED"
    PASS = "PASS"
    WATCH = "WATCH"
    NEAR_MISS = "NEAR_MISS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"

    _ALL = {NOT_EVALUATED, PASS, WATCH, NEAR_MISS, FAIL, BLOCKED}

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in cls._ALL


class RecommendedAction:
    """Universal action vocabulary."""
    NONE = "NONE"
    MONITOR = "MONITOR"
    PREPARE = "PREPARE"
    ENTER = "ENTER"
    ADD = "ADD"
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    EXIT = "EXIT"
    AVOID = "AVOID"
    REVIEW = "REVIEW"

    _ALL = {NONE, MONITOR, PREPARE, ENTER, ADD, HOLD, REDUCE, EXIT, AVOID, REVIEW}

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in cls._ALL


@dataclass(frozen=True)
class OpportunityClock:
    """Time-based coordinates for a lifecycle opportunity."""
    event_date: date
    days_until_event: int
    evaluation_date: date

    @property
    def event_has_passed(self) -> bool:
        return self.days_until_event < 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_date": self.event_date.isoformat(),
            "days_until_event": self.days_until_event,
            "evaluation_date": self.evaluation_date.isoformat(),
            "event_has_passed": self.event_has_passed,
        }


@dataclass
class StrategyOpportunity:
    """
    Canonical lifecycle record for a single opportunity instance.

    opportunity_id is stable across structure changes; it identifies the
    ticker+event pairing. Structure identity is separate.
    """
    opportunity_id: str
    strategy_id: str
    ticker: str
    lifecycle_stage: str
    evaluation_state: str
    verdict: str
    recommended_action: str
    clock: OpportunityClock
    build_eligible: bool = False
    surface_eligible: bool = False
    entry_allowed: bool = False
    source_rows: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "strategy_id": self.strategy_id,
            "ticker": self.ticker,
            "lifecycle_stage": self.lifecycle_stage,
            "evaluation_state": self.evaluation_state,
            "verdict": self.verdict,
            "recommended_action": self.recommended_action,
            "build_eligible": self.build_eligible,
            "surface_eligible": self.surface_eligible,
            "entry_allowed": self.entry_allowed,
            "clock": self.clock.to_dict(),
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class LifecycleClassification:
    """Result of classifying an opportunity into lifecycle dimensions."""
    lifecycle_stage: str
    evaluation_state: str
    verdict: str
    recommended_action: str
    build_eligible: bool
    surface_eligible: bool
    entry_allowed: bool
    classification_reason: str
    classification_source: str = "lifecycle_kernel"

    def to_dict(self) -> dict[str, Any]:
        return {
            "lifecycle_stage": self.lifecycle_stage,
            "evaluation_state": self.evaluation_state,
            "verdict": self.verdict,
            "recommended_action": self.recommended_action,
            "build_eligible": self.build_eligible,
            "surface_eligible": self.surface_eligible,
            "entry_allowed": self.entry_allowed,
            "classification_reason": self.classification_reason,
            "classification_source": self.classification_source,
        }
