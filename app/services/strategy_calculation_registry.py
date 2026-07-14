"""Trusted allowlist of declarative strategy calculations.

Strategy-definition JSON may reference these IDs, but it may not name Python
modules/functions. Execution remains owned by application code.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


CALCULATION_REGISTRY_VERSION = "34A.calculation_registry.v1"


@dataclass(frozen=True)
class CalculationDefinition:
    calculation_id: str
    display_name: str
    description: str
    input_fields: tuple[str, ...]
    output_fields: tuple[str, ...]
    engine: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_CALCULATIONS: dict[str, CalculationDefinition] = {
    "calendar_debit": CalculationDefinition(
        "calendar_debit",
        "Calendar Debit",
        "Conservative and midpoint debit for a same-strike calendar.",
        ("options.bid", "options.ask", "options.mid"),
        ("options.debit",),
        "options_structure_builder",
    ),
    "calendar_liquidity": CalculationDefinition(
        "calendar_liquidity",
        "Calendar Liquidity",
        "Per-leg spread, open interest, and volume checks.",
        ("options.open_interest", "options.volume", "options.spread_pct"),
        ("data_quality.status",),
        "options_structure_builder",
    ),
    "calendar_assignment_risk": CalculationDefinition(
        "calendar_assignment_risk",
        "Calendar Assignment Risk",
        "Short-leg event and moneyness risk classification.",
        ("options.short_leg_expires_before_earnings", "options.short_leg_spans_earnings"),
        ("data_quality.status",),
        "calendar_decision_service",
    ),
    "iv_relationship": CalculationDefinition(
        "iv_relationship",
        "IV Relationship",
        "Front/back IV relationship for calendar spread quality.",
        ("options.front_iv", "options.back_iv"),
        ("options.iv_difference",),
        "calendar_decision_service",
    ),
}


def calculation_registry() -> dict[str, dict[str, Any]]:
    return {key: value.to_dict() for key, value in sorted(_CALCULATIONS.items())}


def validate_calculation_id(calculation_id: str) -> tuple[bool, str | None]:
    value = str(calculation_id or "").strip()
    if not value:
        return False, "CALCULATION_ID_MISSING"
    if "." in value or ":" in value or "/" in value or "\\" in value:
        return False, "CALCULATION_ID_MUST_BE_ALLOWLIST_TOKEN"
    if value not in _CALCULATIONS:
        return False, "CALCULATION_ID_NOT_ALLOWLISTED"
    return True, None
