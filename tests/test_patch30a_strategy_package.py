"""
ASA Patch 30A — Strategy Package (app/strategies/) Tests

Covers the new package-level modules:
  - app/strategies/schema.py  (UniversalStrategyRow TypedDict, constants)
  - app/strategies/gates.py   (VALID_GATE_STATUSES, helpers)
  - app/strategies/normalization.py  (normalize_stock_momentum_row, normalize_legacy_row)
"""
from __future__ import annotations

import py_compile
from typing import Any


# ─── Compile guards ───────────────────────────────────────────────────────────

class TestCompile:
    def test_schema_compiles(self):
        py_compile.compile("app/strategies/schema.py", doraise=True)

    def test_gates_compiles(self):
        py_compile.compile("app/strategies/gates.py", doraise=True)

    def test_normalization_compiles(self):
        py_compile.compile("app/strategies/normalization.py", doraise=True)

    def test_test_clone_compiles(self):
        py_compile.compile("app/strategies/test_stock_momentum_unified.py", doraise=True)

    def test_registry_compiles(self):
        py_compile.compile("app/strategies/registry.py", doraise=True)

    def test_strategy_api_compiles(self):
        py_compile.compile("app/api/strategy_api.py", doraise=True)


# ─── Schema constants ─────────────────────────────────────────────────────────

class TestSchemaConstants:
    def test_schema_version_is_string(self):
        from app.strategies.schema import SCHEMA_VERSION
        assert isinstance(SCHEMA_VERSION, str) and SCHEMA_VERSION

    def test_valid_row_types_is_frozenset(self):
        from app.strategies.schema import VALID_ROW_TYPES
        assert isinstance(VALID_ROW_TYPES, frozenset)

    def test_valid_row_types_contains_expected(self):
        from app.strategies.schema import VALID_ROW_TYPES
        for rt in ("new_candidate", "rejected_candidate", "observation", "test_candidate"):
            assert rt in VALID_ROW_TYPES, f"{rt!r} missing from VALID_ROW_TYPES"

    def test_required_core_fields_is_tuple(self):
        from app.strategies.schema import REQUIRED_CORE_FIELDS
        assert isinstance(REQUIRED_CORE_FIELDS, tuple)

    def test_required_core_fields_contains_essential(self):
        from app.strategies.schema import REQUIRED_CORE_FIELDS
        for f in ("strategy_id", "ticker", "verdict", "gates", "metrics", "schema_version"):
            assert f in REQUIRED_CORE_FIELDS, f"{f!r} missing from REQUIRED_CORE_FIELDS"

    def test_universal_strategy_row_is_typed_dict(self):
        from app.strategies.schema import UniversalStrategyRow
        # TypedDicts are callable as constructors
        row: Any = UniversalStrategyRow(
            strategy_id="test",
            ticker="AAPL",
            row_type="observation",
            verdict="PASS",
        )
        assert row["strategy_id"] == "test"
        assert row["ticker"] == "AAPL"

    def test_universal_strategy_row_allows_partial_construction(self):
        from app.strategies.schema import UniversalStrategyRow
        # total=False means no required keys at runtime
        row = UniversalStrategyRow()
        assert isinstance(row, dict)


# ─── Gate helpers ─────────────────────────────────────────────────────────────

class TestGatesModule:
    def test_valid_gate_statuses_frozenset(self):
        from app.strategies.gates import VALID_GATE_STATUSES
        assert isinstance(VALID_GATE_STATUSES, frozenset)

    def test_valid_gate_statuses_contains_core_six(self):
        from app.strategies.gates import VALID_GATE_STATUSES
        for s in ("pass", "watch", "fail", "unknown", "skipped", "dry_run"):
            assert s in VALID_GATE_STATUSES

    def test_make_gate_re_exported(self):
        from app.strategies.gates import make_gate
        g = make_gate("Liquidity", "pass", reason="OI ok")
        assert g["status"] == "pass"
        assert g["blocking"] is False

    def test_summarize_gates_re_exported(self):
        from app.strategies.gates import make_gate, summarize_gates
        gates = [make_gate("A", "pass"), make_gate("B", "fail")]
        s = summarize_gates(gates)
        assert s["fail_count"] == 1
        assert s["pass_count"] == 1

    def test_make_gate_group_structure(self):
        from app.strategies.gates import make_gate, make_gate_group
        gates = [make_gate("A", "pass"), make_gate("B", "watch")]
        group = make_gate_group("quality", gates)
        assert group["group"] == "quality"
        assert group["gates"] == gates
        assert "summary" in group
        assert group["summary"]["total"] == 2

    def test_get_failed_gates_from_list(self):
        from app.strategies.gates import get_failed_gates, make_gate
        gates = [make_gate("A", "pass"), make_gate("B", "fail"), make_gate("C", "watch")]
        failed = get_failed_gates(gates)
        assert len(failed) == 1
        assert failed[0]["status"] == "fail"

    def test_get_failed_gates_from_dict(self):
        from app.strategies.gates import get_failed_gates, make_gate
        gates = {
            "a": make_gate("A", "pass"),
            "b": make_gate("B", "fail"),
        }
        failed = get_failed_gates(gates)
        assert len(failed) == 1

    def test_get_watch_gates_from_list(self):
        from app.strategies.gates import get_watch_gates, make_gate
        gates = [make_gate("A", "pass"), make_gate("B", "watch")]
        watched = get_watch_gates(gates)
        assert len(watched) == 1
        assert watched[0]["status"] == "watch"

    def test_get_watch_gates_empty_if_none(self):
        from app.strategies.gates import get_watch_gates
        assert get_watch_gates([]) == []

    def test_get_failed_gates_empty_if_none(self):
        from app.strategies.gates import get_failed_gates
        assert get_failed_gates([]) == []

    def test_normalize_gate_status_re_exported(self):
        from app.strategies.gates import normalize_gate_status
        assert normalize_gate_status("PASS") == "pass"
        assert normalize_gate_status("fail") == "fail"
        assert normalize_gate_status("warn") == "watch"


# ─── Normalization module ──────────────────────────────────────────────────────

class TestNormalizationModule:
    def _sm_row(self, action: str = "CONSIDER ADDING", ticker: str = "AAPL") -> dict:
        return {"ticker": ticker, "action": action, "score": 75.0}

    def test_normalize_stock_momentum_row_returns_dict(self):
        from app.strategies.normalization import normalize_stock_momentum_row
        result = normalize_stock_momentum_row(self._sm_row())
        assert isinstance(result, dict)

    def test_normalize_stock_momentum_row_has_strategy_id(self):
        from app.strategies.normalization import normalize_stock_momentum_row
        result = normalize_stock_momentum_row(self._sm_row())
        assert result["strategy_id"] == "stock_momentum"

    def test_normalize_stock_momentum_row_has_schema_version(self):
        from app.strategies.normalization import normalize_stock_momentum_row
        from app.strategies.schema import SCHEMA_VERSION
        result = normalize_stock_momentum_row(self._sm_row())
        assert result.get("schema_version") == SCHEMA_VERSION

    def test_normalize_stock_momentum_row_has_row_type(self):
        from app.strategies.normalization import normalize_stock_momentum_row
        from app.strategies.schema import VALID_ROW_TYPES
        result = normalize_stock_momentum_row(self._sm_row("CONSIDER ADDING"))
        assert result.get("row_type") in VALID_ROW_TYPES

    def test_consider_adding_is_new_candidate(self):
        from app.strategies.normalization import normalize_stock_momentum_row
        result = normalize_stock_momentum_row(self._sm_row("CONSIDER ADDING"))
        assert result.get("row_type") == "new_candidate"

    def test_avoid_is_rejected_candidate(self):
        from app.strategies.normalization import normalize_stock_momentum_row
        result = normalize_stock_momentum_row(self._sm_row("AVOID / WEAK TREND"))
        assert result.get("row_type") == "rejected_candidate"

    def test_watch_is_observation(self):
        from app.strategies.normalization import normalize_stock_momentum_row
        result = normalize_stock_momentum_row(self._sm_row("WATCH / CONFIRM TREND"))
        assert result.get("row_type") == "observation"

    def test_does_not_mutate_original(self):
        from app.strategies.normalization import normalize_stock_momentum_row
        orig = self._sm_row()
        orig_copy = dict(orig)
        normalize_stock_momentum_row(orig)
        assert orig == orig_copy  # original unchanged

    def test_normalize_legacy_row_returns_dict(self):
        from app.strategies.normalization import normalize_legacy_row
        row = {"ticker": "MSFT", "verdict": "PASS — momentum strong"}
        result = normalize_legacy_row(row, "earnings_calendar")
        assert isinstance(result, dict)
        assert result.get("strategy_id") == "earnings_calendar"

    def test_normalize_legacy_row_pass_verdict_new_candidate(self):
        from app.strategies.normalization import normalize_legacy_row
        row = {"ticker": "MSFT", "verdict": "PASS — entry confirmed"}
        result = normalize_legacy_row(row, "skew_momentum_vertical")
        assert result.get("row_type") == "new_candidate"

    def test_normalize_legacy_row_fail_verdict_rejected(self):
        from app.strategies.normalization import normalize_legacy_row
        row = {"ticker": "TSLA", "verdict": "FAIL — illiquid"}
        result = normalize_legacy_row(row, "earnings_calendar")
        assert result.get("row_type") == "rejected_candidate"

    def test_normalize_rows_list(self):
        from app.strategies.normalization import normalize_rows
        rows = [self._sm_row("CONSIDER ADDING"), self._sm_row("AVOID / WEAK TREND")]
        results = normalize_rows(rows, "stock_momentum")
        assert len(results) == 2
        assert all(r.get("schema_version") for r in results)

    def test_normalize_rows_empty_list(self):
        from app.strategies.normalization import normalize_rows
        assert normalize_rows([], "stock_momentum") == []

    def test_ff_dry_run_enforced_in_normalization(self):
        from app.strategies.normalization import normalize_legacy_row
        row = {"ticker": "AAPL", "verdict": "PASS — FF signal"}
        result = normalize_legacy_row(row, "forward_factor_calendar")
        assert result.get("can_trade_live") is False
        assert result.get("dry_run") is True

    def test_ff_daily_opportunity_false(self):
        from app.strategies.normalization import normalize_legacy_row
        row = {"ticker": "AAPL", "verdict": "PASS — FF signal"}
        result = normalize_legacy_row(row, "forward_factor_calendar")
        assert result.get("daily_opportunity_eligible") is False
