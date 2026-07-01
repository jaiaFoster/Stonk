"""Shared strategy result types and gradual Strategy Interface v1."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.models.strategy_opportunity_models import StrategyOpportunity
from app.services.strategy_opportunity_normalizer import normalize_legacy_strategy_row


@dataclass(slots=True)
class StrategyResult:
    """Legacy report-friendly strategy output. Kept unchanged for compatibility."""

    name: str
    ticker: str
    action: str
    score: float | None = None
    confidence: str | None = None
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    next_check: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class StrategyV1(Protocol):
    id: str
    name: str
    version: str

    def required_data(self, context: Any) -> Any: ...
    def scan(self, context: Any, market_snapshot: Any) -> Any: ...
    def normalize_result(self, result: Any) -> list[StrategyOpportunity]: ...
    def can_enter_daily_opportunity(self, opportunity: StrategyOpportunity) -> bool: ...
    def lifecycle_intent(self, opportunity: StrategyOpportunity) -> str | None: ...


class LegacyStrategyAdapterV1:
    """Non-invasive v1 contract over existing registry adapters."""

    def __init__(self, plugin: Any):
        self.plugin = plugin
        self.id = plugin.strategy_id
        self.name = plugin.strategy_label
        self.version = plugin.version

    def required_data(self, context: Any) -> Any:
        universe = self.plugin.build_universe(context)
        return self.plugin.data_requirements(context, universe)

    def scan(self, context: Any, market_snapshot: Any) -> Any:
        if not callable(market_snapshot):
            raise TypeError("Legacy adapter scan requires existing evaluator callable")
        return market_snapshot()

    def normalize_result(self, result: Any) -> list[StrategyOpportunity]:
        if not isinstance(result, dict):
            return []
        rows = result.get("rows") or result.get("items") or result.get("new_trade_rows") or []
        return [normalize_legacy_strategy_row(self.id, row) for row in rows if isinstance(row, dict)]

    def can_enter_daily_opportunity(self, opportunity: StrategyOpportunity) -> bool:
        if self.id == "forward_factor_calendar":
            return False
        return opportunity.can_enter_daily_opportunity and opportunity.verdict_tier >= 80

    def lifecycle_intent(self, opportunity: StrategyOpportunity) -> str | None:
        return str(opportunity.raw.get("lifecycle_intent") or "") or None
