"""
ASA Patch 30A — StockMomentumUnifiedTest Clone Tests

Covers app/strategies/test_stock_momentum_unified.py.
Verifies CAVEMAN MODE constraints: is_enabled()=False, no broker writes,
no Daily Opportunity, universal row output shape.
"""
from __future__ import annotations

import py_compile
from typing import Any


class TestCompile:
    def test_clone_compiles(self):
        py_compile.compile("app/strategies/test_stock_momentum_unified.py", doraise=True)


class TestStockMomentumUnifiedTestClone:
    def _clone(self):
        from app.strategies.test_stock_momentum_unified import StockMomentumUnifiedTest
        return StockMomentumUnifiedTest()

    def _raw_row(self, action: str = "CONSIDER ADDING", ticker: str = "AAPL") -> dict:
        return {"ticker": ticker, "action": action, "score": 80.0, "verdict": action}

    def test_strategy_id(self):
        assert self._clone().strategy_id == "stock_momentum_unified_test"

    def test_strategy_name_set(self):
        clone = self._clone()
        assert isinstance(clone.strategy_name, str) and clone.strategy_name

    def test_is_enabled_false(self):
        assert self._clone().is_enabled() is False

    def test_row_schema_version(self):
        from app.strategies.schema import SCHEMA_VERSION
        assert self._clone().row_schema_version() == SCHEMA_VERSION

    def test_normalize_rows_empty(self):
        assert self._clone().normalize_rows([]) == []

    def test_normalize_rows_skips_non_dicts(self):
        result = self._clone().normalize_rows(["not a dict", 42, None])  # type: ignore[arg-type]
        assert result == []

    def test_normalize_rows_returns_list_of_dicts(self):
        rows = self._clone().normalize_rows([self._raw_row()])
        assert isinstance(rows, list)
        assert all(isinstance(r, dict) for r in rows)

    def test_normalized_row_has_strategy_id(self):
        rows = self._clone().normalize_rows([self._raw_row()])
        assert rows[0]["strategy_id"] == "stock_momentum_unified_test"

    def test_normalized_row_has_schema_version(self):
        from app.strategies.schema import SCHEMA_VERSION
        rows = self._clone().normalize_rows([self._raw_row()])
        assert rows[0].get("schema_version") == SCHEMA_VERSION

    def test_normalized_row_has_row_type(self):
        from app.strategies.schema import VALID_ROW_TYPES
        rows = self._clone().normalize_rows([self._raw_row("CONSIDER ADDING")])
        assert rows[0].get("row_type") in VALID_ROW_TYPES

    def test_consider_adding_becomes_new_candidate(self):
        rows = self._clone().normalize_rows([self._raw_row("CONSIDER ADDING")])
        assert rows[0]["row_type"] == "new_candidate"

    def test_avoid_becomes_rejected_candidate(self):
        rows = self._clone().normalize_rows([self._raw_row("AVOID / WEAK TREND")])
        assert rows[0]["row_type"] == "rejected_candidate"

    def test_normalized_row_daily_opportunity_eligible_false(self):
        rows = self._clone().normalize_rows([self._raw_row("CONSIDER ADDING")])
        # Test clone rows must not be DO-eligible (strategy_id != "stock_momentum").
        row = rows[0]
        assert row.get("daily_opportunity_eligible") is False

    def test_test_rows_respects_limit(self):
        raw = [self._raw_row(ticker=f"T{i}") for i in range(30)]
        rows = self._clone().test_rows(raw, limit=5)
        assert len(rows) <= 5

    def test_test_rows_returns_list(self):
        rows = self._clone().test_rows([self._raw_row()])
        assert isinstance(rows, list)

    def test_original_row_not_mutated(self):
        orig = self._raw_row()
        orig_copy = dict(orig)
        self._clone().normalize_rows([orig])
        assert orig == orig_copy


class TestCavemanModeTestClone:
    def test_test_clone_disabled_in_enabled_strategies(self):
        from app.strategies.registry import enabled_strategies
        ids = [s.strategy_id for s in enabled_strategies()]
        assert "stock_momentum_unified_test" not in ids

    def test_test_clone_can_trade_live_false(self):
        from app.strategies.test_stock_momentum_unified import StockMomentumUnifiedTest
        clone = StockMomentumUnifiedTest()
        row = clone.normalize_rows([{"ticker": "AAPL", "action": "CONSIDER ADDING"}])
        assert row[0].get("can_trade_live") is False

    def test_test_clone_not_in_production_spec_registry(self):
        # Stock momentum unified test is in STRATEGY_SPEC_REGISTRY but
        # NOT in the original STRATEGY_SPECS from strategy_spec_registry.
        from app.services.strategy_spec_registry import STRATEGY_SPECS
        assert "stock_momentum_unified_test" not in STRATEGY_SPECS
