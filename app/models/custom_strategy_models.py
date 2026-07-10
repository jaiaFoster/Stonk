"""Typed data contracts for custom user-authored strategy definitions.

ASA Patch 31B — schema_version 31B.v1.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

CUSTOM_STRATEGY_SCHEMA_VERSION = "31B.v1"

# Allowed status transitions for custom strategy definitions.
ALLOWED_STATUSES = frozenset({"draft", "active", "archived"})

# Logical operators for condition groups.
ALLOWED_LOGIC_OPERATORS = frozenset({"AND", "OR"})

# Output signals that a custom strategy may emit.
ALLOWED_SIGNALS = frozenset({"WATCH", "ENTRY_CANDIDATE", "MONITOR", "SKIP"})


@dataclass
class CustomStrategyCondition:
    """One leaf-level filter condition."""
    field_id: str
    operator: str
    value: Any
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CustomStrategyConditionGroup:
    """A group of conditions joined by AND or OR."""
    logic: str
    conditions: list[dict[str, Any]] = field(default_factory=list)
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CustomStrategyOutput:
    """Signal description emitted when all conditions match."""
    signal: str = "WATCH"
    label: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CustomStrategyRisk:
    """Optional risk/sizing guidance — informational only, never executable."""
    max_position_size_pct: float | None = None
    max_concurrent_positions: int | None = None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CustomStrategyDefinition:
    """Canonical persisted custom strategy definition.

    Hard constraints (31B):
    - No broker writes. No trade execution. No market-data provider calls.
    - Fields must reference only catalog-exposed field IDs.
    - Operators must be restricted to the field's declared operator set.
    - Always read-only against providers; never modifies positions.
    """
    definition_id: str
    owner_id: str
    name: str
    description: str
    status: str
    definition_version: int
    universe: list[str]
    conditions: list[dict[str, Any]]
    output: dict[str, Any]
    risk: dict[str, Any]
    created_at: str
    updated_at: str
    schema_version: str = CUSTOM_STRATEGY_SCHEMA_VERSION

    @classmethod
    def new(
        cls,
        owner_id: str,
        name: str,
        description: str = "",
        universe: list[str] | None = None,
        conditions: list[dict[str, Any]] | None = None,
        output: dict[str, Any] | None = None,
        risk: dict[str, Any] | None = None,
    ) -> "CustomStrategyDefinition":
        now = datetime.now(timezone.utc).isoformat()
        return cls(
            definition_id=str(uuid.uuid4()),
            owner_id=owner_id,
            name=name,
            description=description,
            status="draft",
            definition_version=1,
            universe=universe or [],
            conditions=conditions or [],
            output=output or {"signal": "WATCH", "label": "", "notes": ""},
            risk=risk or {},
            created_at=now,
            updated_at=now,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
