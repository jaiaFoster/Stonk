"""Universal strategy row schema — TypedDict definition and field contracts.

This module defines the canonical shape of a universal strategy row. The TypedDict
is intentionally total=False (all fields optional) so callers can build rows
incrementally without runtime errors from missing keys.
"""

from __future__ import annotations

from typing import Any, TypedDict

SCHEMA_VERSION: str = "30A.v2"

VALID_ROW_TYPES: frozenset[str] = frozenset({
    "new_candidate",
    "open_position",
    "lifecycle_check",
    "observation",
    "rejected_candidate",
    "test_candidate",
})

REQUIRED_CORE_FIELDS: tuple[str, ...] = (
    "strategy_id",
    "ticker",
    "row_type",
    "verdict",
    "friendly_verdict",
    "primary_reason",
    "gates",
    "metrics",
    "data_quality",
    "daily_opportunity_eligible",
    "journal_eligible",
    "observation_key",
    "schema_version",
)


class UniversalStrategyRow(TypedDict, total=False):
    """Canonical shape for a single strategy candidate/observation row.

    All fields are optional at declaration time so partial construction is safe.
    normalize_stock_momentum_row() and normalize_legacy_row() produce fully-populated
    instances that satisfy REQUIRED_CORE_FIELDS.
    """

    strategy_id: str
    strategy_name: str
    strategy_family: str
    strategy_goal: str
    ticker: str
    row_type: str               # one of VALID_ROW_TYPES
    verdict: str                # raw strategy verdict string
    friendly_verdict: str       # human-readable verdict label
    primary_reason: str         # brief pass/fail reason
    gates: dict[str, Any]       # gate_id → gate dict (from make_gate)
    metrics: dict[str, Any]     # key numerics for this row
    data_quality: dict[str, Any]
    daily_opportunity_eligible: bool
    daily_opportunity_reason: str
    journal_eligible: bool
    observation_key: str
    observation_refs: list[str]
    can_trade_live: bool
    dry_run: bool
    schema_version: str
