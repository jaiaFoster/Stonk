"""Typed data contracts for the custom-strategy field catalog."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


CATALOG_SCHEMA_VERSION = "31A.v1"


@dataclass(frozen=True)
class StrategyFieldDefinition:
    field_id: str
    display_name: str
    description: str
    category: str
    value_type: str
    nullable: bool
    source_domain: str
    source_path: str
    availability_stage: str
    supported_asset_types: tuple[str, ...]
    supported_strategy_types: tuple[str, ...]
    allowed_uses: tuple[str, ...]
    allowed_operators: tuple[str, ...]
    requirement_types: tuple[str, ...] = ()
    unit: str = ""
    enum_values: tuple[str, ...] = ()
    default_missing_behavior: str = "fail_gate"
    requires_market_data: bool = False
    requires_options_data: bool = False
    requires_earnings_data: bool = False
    requires_broker_data: bool = False
    provider_cost_class: str = "none"
    sensitivity: str = "public_market_data"
    deprecated: bool = False
    examples: tuple[Any, ...] = field(default_factory=tuple)
    schema_version: str = CATALOG_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
