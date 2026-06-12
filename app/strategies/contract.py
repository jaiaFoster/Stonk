"""Lightweight local strategy plugin contract."""

from __future__ import annotations

from typing import Any, Protocol

from app.models.market_data_models import StrategyDataRequirement, StrategyDisplayMetadata, StrategyResult


class StrategyPlugin(Protocol):
    strategy_id: str
    strategy_label: str
    version: str
    display_metadata: StrategyDisplayMetadata

    def is_enabled(self) -> bool: ...
    def build_universe(self, context: Any) -> list[str]: ...
    def data_requirements(self, context: Any, universe: list[str]) -> StrategyDataRequirement: ...
    def normalize_result(self, raw: dict[str, Any], context: Any) -> StrategyResult: ...
