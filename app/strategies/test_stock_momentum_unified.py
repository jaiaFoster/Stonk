"""Test clone of Stock Momentum that emits universal strategy rows.

This module is a non-production, read-only strategy variant used to validate
the universal row format against real stock momentum data. It does NOT:
  - enter Daily Opportunity
  - write to brokers or any external service
  - replace or modify the production stock_momentum strategy

CAVEMAN MODE: is_enabled() always returns False so this strategy never runs
in the regular pipeline. It can only be invoked explicitly from developer tools.
"""

from __future__ import annotations

from typing import Any

from app.strategies.normalization import normalize_stock_momentum_row
from app.strategies.schema import SCHEMA_VERSION

STRATEGY_ID = "stock_momentum_unified_test"
STRATEGY_NAME = "Stock Momentum Unified (Test Clone)"
STRATEGY_VERSION = "30A.v1"


class StockMomentumUnifiedTest:
    """Test clone of StockMomentumStrategy that emits UniversalStrategyRow dicts.

    This class satisfies the minimum shape expected by developer snapshot tools
    and the /api/strategies/test-rows endpoint. It re-uses production stock_momentum
    row data and re-normalizes it through the universal row schema.
    """

    strategy_id: str = STRATEGY_ID
    strategy_name: str = STRATEGY_NAME
    version: str = STRATEGY_VERSION

    def is_enabled(self) -> bool:
        return False

    def normalize_rows(self, raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Normalize production stock_momentum rows into universal schema."""
        return [
            normalize_stock_momentum_row(row, strategy_id=self.strategy_id)
            for row in (raw_rows or [])
            if isinstance(row, dict)
        ]

    def test_rows(self, raw_rows: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
        """Return normalized test rows capped at `limit`."""
        return self.normalize_rows(raw_rows)[:max(1, int(limit or 20))]

    def row_schema_version(self) -> str:
        return SCHEMA_VERSION
