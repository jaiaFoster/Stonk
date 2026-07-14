"""Trusted declarative strategy-definition contracts.

Patch 34A introduces a local, versioned schema for strategy definitions.  These
dataclasses are descriptive only: no eval, no dynamic imports, no provider
fetching, and no broker actions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


STRATEGY_DEFINITION_SCHEMA_VERSION = "34A.strategy_definition.v1"


@dataclass(frozen=True)
class ExpirationRecord:
    expiration: str
    dte: int | None
    expiration_type: str
    classification_confidence: str
    provider: str = "unknown"
    source_timestamp: str | None = None
    data_state: str = "COMPLETE"
    rejection_code: str | None = None
    rejection_detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExpirationRequirement:
    role: str
    min_dte: int | None = None
    max_dte: int | None = None
    relation_to_event: str = "any"
    min_days_before_event: int | None = None
    min_days_after_event: int | None = None
    allow_weekly: bool = True
    allow_monthly: bool = True
    allow_quarterly: bool = True
    allow_leaps: bool = False

    def allowed_types(self) -> set[str]:
        allowed: set[str] = set()
        if self.allow_weekly:
            allowed.add("WEEKLY")
        if self.allow_monthly:
            allowed.add("MONTHLY")
        if self.allow_quarterly:
            allowed.add("QUARTERLY")
        if self.allow_leaps:
            allowed.add("LEAPS")
        allowed.add("UNKNOWN")
        return allowed


@dataclass(frozen=True)
class ExpirationPairRule:
    front_role: str = "front"
    back_role: str = "back"
    min_gap_days: int | None = None
    max_gap_days: int | None = None
    event_must_be_between: bool = False
    front_must_expire_before_event: bool = False
    back_must_expire_after_event: bool = False


@dataclass(frozen=True)
class StrikeRule:
    selection_method: str = "nearest_atm"
    target_delta: float | None = None
    max_delta_deviation: float | None = None
    same_strike_as: str | None = None


@dataclass(frozen=True)
class LegRequirement:
    leg_id: str
    role: str
    option_type: str
    expiration_role: str
    position: str
    quantity: int = 1
    strike_rule: StrikeRule = field(default_factory=StrikeRule)


@dataclass(frozen=True)
class StructureTemplate:
    template_id: str
    structure_type: str
    expiration_pair_rule: ExpirationPairRule
    legs: tuple[LegRequirement, ...]
    calculation_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class CandidateGenerationResult:
    strategy_id: str
    raw_events: int = 0
    merged_events: int = 0
    quality_eligible: int = 0
    quality_rejected: int = 0
    optionable_candidates: int = 0
    budget_approved: int = 0
    budget_deferred: int = 0
    chain_sets_requested: int = 0
    chain_sets_acquired: int = 0
    tickers_with_expirations: int = 0
    tickers_with_valid_pairs: int = 0
    valid_pairs: int = 0
    rejected_pairs: int = 0
    terminal_rows: int = 0
    failure_by_code: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StrategyDefinition:
    strategy_id: str
    version: str
    name: str
    schema_version: str
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "version": self.version,
            "name": self.name,
            "schema_version": self.schema_version,
            "raw": self.raw,
        }
