"""
ASA Patch 30E — Forward Factor Calendar API Rows Tests

Covers:
  - GET /api/strategies/forward_factor_calendar/rows endpoint
  - No provider calls triggered (read_only=True)
  - Empty-state when no snapshot
  - Rows list returned
  - Universal row enrichment applied
  - daily_opportunity.eligible=False in returned rows
  - dry_run=True in response
  - Row count bounded
  - Compile check
"""
from __future__ import annotations

import json
import py_compile
from unittest.mock import MagicMock, patch


class TestCompile:
    def test_strategy_api_compiles(self):
        py_compile.compile("app/api/strategy_api.py", doraise=True)

    def test_forward_factor_universal_compiles(self):
        py_compile.compile("app/strategies/forward_factor_universal.py", doraise=True)


# ─── Empty-state (no snapshot) ────────────────────────────────────────────────

class TestEmptyState:
    def _get_rows(self, strategy_id: str = "forward_factor_calendar", limit: int = 20) -> dict:
        from app.api.strategy_api import get_strategy_rows
        with patch("app.services.report_snapshot_service.ReportSnapshotRepository") as MockRepo:
            MockRepo.return_value.latest_success.return_value = None
            return get_strategy_rows(strategy_id, limit=limit)

    def test_empty_state_returns_dict(self):
        assert isinstance(self._get_rows(), dict)

    def test_empty_state_read_only(self):
        result = self._get_rows()
        assert result.get("read_only") is True

    def test_empty_state_provider_calls_false(self):
        result = self._get_rows()
        assert result.get("provider_calls_triggered") is False

    def test_empty_state_rows_is_list(self):
        result = self._get_rows()
        assert isinstance(result.get("rows", []), list)

    def test_empty_state_row_count_zero(self):
        result = self._get_rows()
        assert result.get("row_count", 0) == 0


# ─── Live rows from snapshot ──────────────────────────────────────────────────

class TestLiveRows:
    def _snapshot(self, rows: list) -> dict:
        return {
            "run_id": "run-ff-test-001",
            "full_summary_blob": None,
            "raw_provider_blob": None,
        }

    def _get_rows_with_data(self, ff_rows: list, limit: int = 20) -> dict:
        from app.api.strategy_api import get_strategy_rows
        fake_snapshot = {
            "run_id": "run-ff-test-001",
        }
        fake_summary = {
            "report_data": {
                "tradier_snapshot": {
                    "_forward_factor_strategy": {
                        "items": ff_rows,
                    }
                }
            }
        }
        with patch("app.services.report_snapshot_service.ReportSnapshotRepository") as MockRepo:
            instance = MockRepo.return_value
            instance.latest_success.return_value = fake_snapshot
            instance.load_summary.return_value = fake_summary
            return get_strategy_rows("forward_factor_calendar", limit=limit)

    def test_returns_dict(self):
        result = self._get_rows_with_data(_ff_rows())
        assert isinstance(result, dict)

    def test_read_only_true(self):
        result = self._get_rows_with_data(_ff_rows())
        assert result.get("read_only") is True

    def test_provider_calls_false(self):
        result = self._get_rows_with_data(_ff_rows())
        assert result.get("provider_calls_triggered") is False

    def test_strategy_id_is_forward_factor_calendar(self):
        result = self._get_rows_with_data(_ff_rows())
        assert result.get("strategy_id") == "forward_factor_calendar"

    def test_rows_is_list(self):
        result = self._get_rows_with_data(_ff_rows())
        assert isinstance(result.get("rows"), list)

    def test_row_count_matches(self):
        rows = _ff_rows()
        result = self._get_rows_with_data(rows)
        assert result.get("row_count") == len(rows)

    def test_dry_run_flag_true(self):
        result = self._get_rows_with_data(_ff_rows())
        assert result.get("dry_run") is True

    def test_rows_have_strategy_id(self):
        result = self._get_rows_with_data(_ff_rows())
        for row in result.get("rows", []):
            assert row.get("strategy_id") == "forward_factor_calendar"

    def test_rows_have_schema_version(self):
        from app.strategies.schema import SCHEMA_VERSION
        result = self._get_rows_with_data(_ff_rows())
        for row in result.get("rows", []):
            assert row.get("schema_version") == SCHEMA_VERSION

    def test_rows_have_details_forward_factor(self):
        result = self._get_rows_with_data(_ff_rows())
        for row in result.get("rows", []):
            assert "details" in row
            assert "forward_factor" in row["details"]

    def test_rows_daily_opportunity_not_eligible(self):
        result = self._get_rows_with_data(_ff_rows())
        for row in result.get("rows", []):
            assert row["daily_opportunity"]["eligible"] is False

    def test_rows_have_gate_groups(self):
        result = self._get_rows_with_data(_ff_rows())
        for row in result.get("rows", []):
            assert isinstance(row.get("gate_groups"), dict)

    def test_limit_respected(self):
        many_rows = _ff_rows() * 10
        result = self._get_rows_with_data(many_rows, limit=5)
        assert len(result.get("rows", [])) <= 5

    def test_source_run_id_present(self):
        result = self._get_rows_with_data(_ff_rows())
        assert result.get("source_run_id") == "run-ff-test-001"

    def test_schema_version_in_response_when_rows_present(self):
        from app.strategies.schema import SCHEMA_VERSION
        result = self._get_rows_with_data(_ff_rows())
        if result.get("rows"):
            assert result.get("schema_version") == SCHEMA_VERSION

    def test_non_dict_rows_skipped(self):
        mixed = [_ff_row("AAPL"), "not_a_dict", None, _ff_row("MSFT")]
        result = self._get_rows_with_data(mixed)
        assert result.get("row_count") == 2


# ─── Rows via _forward_factor_strategy.rows key ───────────────────────────────

class TestRowsKeyFallback:
    def _get_rows_via_rows_key(self, ff_rows: list) -> dict:
        from app.api.strategy_api import get_strategy_rows
        fake_snapshot = {"run_id": "run-ff-rows-001"}
        fake_summary = {
            "report_data": {
                "tradier_snapshot": {
                    "_forward_factor_strategy": {
                        "rows": ff_rows,
                    }
                }
            }
        }
        with patch("app.services.report_snapshot_service.ReportSnapshotRepository") as MockRepo:
            instance = MockRepo.return_value
            instance.latest_success.return_value = fake_snapshot
            instance.load_summary.return_value = fake_summary
            return get_strategy_rows("forward_factor_calendar")

    def test_rows_key_returns_rows(self):
        result = self._get_rows_via_rows_key(_ff_rows())
        assert result.get("row_count") > 0


# ─── Fixtures ─────────────────────────────────────────────────────────────────

def _ff_row(ticker: str = "AAPL") -> dict:
    return {
        "ticker": ticker,
        "strategy_id": "forward_factor_calendar",
        "verdict": "PASS / FF_HIGH",
        "score": 0.75,
        "forward_factor": 0.32,
        "front_dte": 14,
        "back_dte": 42,
        "front_expiration": "2026-07-18",
        "back_expiration": "2026-08-15",
        "front_ex_earnings_iv": 0.28,
        "back_ex_earnings_iv": 0.22,
        "earnings_contaminated": False,
        "liquidity_pass": True,
        "liquidity_status": "PASS",
        "structure_status": "COMPLETE",
        "data_eligibility": {"eligible": True},
        "conservative_debit": 1.20,
        "debit_at_risk": 120.0,
    }


def _ff_rows(n: int = 3) -> list:
    tickers = ["AAPL", "MSFT", "NVDA", "TSLA", "GOOGL"]
    return [_ff_row(tickers[i % len(tickers)]) for i in range(n)]
