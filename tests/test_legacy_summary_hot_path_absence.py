"""TKT-LEGACY-SUMMARY-DEPRECATION-CLOSEOUT — verify legacy summary is NOT called on hot paths.

When the strategy row store has data the new row-store-based endpoints must serve
results without touching load_summary(full=True).  The legacy summary is only a
last-resort fallback for deployments that have not yet accumulated row-store data.

Endpoints under test:
- build_open_positions_response()  → open_positions_api
- build_daily_opportunity_response() → daily_opportunity_api
- get_strategy_rows()              → strategy_api (row-store primary path)

Invariants:
1. source == "strategy_row_store"  when row store has rows.
2. load_summary() is NEVER called when the row store has rows.
3. source == "legacy_snapshot_fallback" when row store is empty.
"""
from __future__ import annotations

import sys
import types
import unittest.mock as mock

# ── pyo3 panic guard ──────────────────────────────────────────────────────────
_rh_stub = types.ModuleType("robin_stocks")
_rh_stub.robinhood = types.ModuleType("robin_stocks.robinhood")
sys.modules.setdefault("robin_stocks", _rh_stub)
sys.modules.setdefault("robin_stocks.robinhood", _rh_stub.robinhood)

import pytest


def _lifecycle_row(ticker: str = "SBUX") -> dict:
    return {
        "row_id": f"{ticker}-lc-1",
        "ticker": ticker,
        "row_type": "lifecycle_check",
        "verdict": "HOLD / MONITOR",
        "action_type": "active_calendar",
        "decision_class": "lifecycle",
        "eligibility_status": "eligible",
        "option_type": "call",
        "front_expiration": "2026-08-21",
        "back_expiration": "2026-09-18",
        "strike": 95.0,
        "structure_summary": {
            "structure_type": "calendar_spread",
            "option_type": "call",
            "strike": 95.0,
            "front_expiration": "2026-08-21",
            "back_expiration": "2026-09-18",
        },
    }


def _do_row(ticker: str = "AAPL") -> dict:
    return {
        "row_id": f"{ticker}-do-1",
        "ticker": ticker,
        "row_type": "opportunity",
        "verdict": "PASS / ENTRY WINDOW OPEN",
        "action_type": "calendar_entry",
        "daily_opportunity_eligible": True,
        "decision_class": "entry",
    }


class TestOpenPositionsHotPath:
    """build_open_positions_response must not call load_summary when row store has rows."""

    def _call(self, rows: list) -> tuple[dict, mock.MagicMock]:
        load_summary_mock = mock.MagicMock(side_effect=AssertionError("load_summary called on hot path"))
        stub_repo = mock.MagicMock()
        stub_repo.read_latest.return_value = {"run_id": "run-123", "rows": rows, "row_count": len(rows)}
        with mock.patch("app.services.strategy_row_repository.StrategyRowRepository", return_value=stub_repo):
            from app.api.open_positions_api import build_open_positions_response
            response = build_open_positions_response()
        return response, load_summary_mock

    def test_row_store_source_when_lifecycle_rows_present(self):
        rows = [_lifecycle_row("SBUX")]
        response, _ = self._call(rows)
        assert response.get("source") == "strategy_row_store", (
            f"Expected source='strategy_row_store', got {response.get('source')!r}"
        )

    def test_load_summary_not_called_when_lifecycle_rows_present(self):
        """Validate that load_summary is never invoked when row store has lifecycle rows."""
        rows = [_lifecycle_row("SBUX")]
        load_summary_spy = mock.MagicMock()
        stub_repo = mock.MagicMock()
        stub_repo.read_latest.return_value = {"run_id": "run-123", "rows": rows, "row_count": 1}
        stub_snapshot_repo = mock.MagicMock()
        stub_snapshot_repo.load_summary = load_summary_spy

        with (
            mock.patch("app.services.strategy_row_repository.StrategyRowRepository", return_value=stub_repo),
            mock.patch("app.services.report_snapshot_service.ReportSnapshotRepository", return_value=stub_snapshot_repo),
        ):
            from app.api.open_positions_api import build_open_positions_response
            response = build_open_positions_response()

        load_summary_spy.assert_not_called()

    def test_fallback_used_false_when_row_store_primary(self):
        rows = [_lifecycle_row("SBUX")]
        response, _ = self._call(rows)
        assert response.get("fallback_used") is False

    def test_active_calendar_count_from_row_store(self):
        rows = [_lifecycle_row("SBUX")]
        response, _ = self._call(rows)
        assert response.get("active_calendar_count") >= 1

    def test_legacy_fallback_used_when_row_store_empty(self):
        """When row store has no lifecycle rows, fallback to legacy is expected."""
        stub_repo = mock.MagicMock()
        stub_repo.read_latest.return_value = {"run_id": None, "rows": [], "row_count": 0}
        stub_snapshot_repo = mock.MagicMock()
        stub_snapshot_repo.latest_success.return_value = None  # no snapshot either
        with (
            mock.patch("app.services.strategy_row_repository.StrategyRowRepository", return_value=stub_repo),
            mock.patch("app.services.report_snapshot_service.ReportSnapshotRepository", return_value=stub_snapshot_repo),
        ):
            from app.api.open_positions_api import build_open_positions_response
            response = build_open_positions_response()
        # Either empty_state or legacy_snapshot_fallback — NOT strategy_row_store.
        assert response.get("source") != "strategy_row_store"


class TestDailyOpportunityHotPath:
    """build_daily_opportunity_response must not call load_summary when row store has rows."""

    def test_load_summary_not_called_when_row_store_has_eligible_rows(self):
        rows = [_do_row("AAPL")]
        load_summary_spy = mock.MagicMock()
        stub_row_repo = mock.MagicMock()
        stub_row_repo.read_latest.return_value = {"run_id": "run-do-1", "rows": rows, "row_count": 1}
        stub_snapshot_repo = mock.MagicMock()
        stub_snapshot_repo.load_summary = load_summary_spy

        with (
            mock.patch("app.services.strategy_row_repository.StrategyRowRepository", return_value=stub_row_repo),
            mock.patch("app.services.report_snapshot_service.ReportSnapshotRepository", return_value=stub_snapshot_repo),
        ):
            from app.api.daily_opportunity_api import build_daily_opportunity_response
            response = build_daily_opportunity_response()

        load_summary_spy.assert_not_called()

    def test_source_is_row_store_when_eligible_rows_present(self):
        rows = [_do_row("AAPL")]
        stub_row_repo = mock.MagicMock()
        stub_row_repo.read_latest.return_value = {"run_id": "run-do-2", "rows": rows, "row_count": 1}
        with mock.patch("app.services.strategy_row_repository.StrategyRowRepository", return_value=stub_row_repo):
            from app.api.daily_opportunity_api import build_daily_opportunity_response
            response = build_daily_opportunity_response()
        assert response.get("source") == "strategy_row_store"

    def test_fallback_to_legacy_when_row_store_empty(self):
        stub_row_repo = mock.MagicMock()
        stub_row_repo.read_latest.return_value = {"run_id": None, "rows": [], "row_count": 0}
        stub_snapshot_repo = mock.MagicMock()
        stub_snapshot_repo.latest_success.return_value = None
        with (
            mock.patch("app.services.strategy_row_repository.StrategyRowRepository", return_value=stub_row_repo),
            mock.patch("app.services.report_snapshot_service.ReportSnapshotRepository", return_value=stub_snapshot_repo),
        ):
            from app.api.daily_opportunity_api import build_daily_opportunity_response
            response = build_daily_opportunity_response()
        assert response.get("source") != "strategy_row_store"


class TestStrategyRowsApiHotPath:
    """get_strategy_rows() must not call load_summary when the row store has data."""

    def test_load_summary_not_called_when_row_store_has_rows(self):
        rows = [
            {
                "row_id": "ec-row-1",
                "ticker": "SBUX",
                "verdict": "HOLD / MONITOR",
                "strategy_id": "earnings_calendar",
                "normalization_status": "ok",
            }
        ]
        load_summary_spy = mock.MagicMock()
        stub_row_repo = mock.MagicMock()
        stub_row_repo.read_latest.return_value = {"run_id": "run-ec-1", "rows": rows, "row_count": 1}
        stub_snapshot_repo = mock.MagicMock()
        stub_snapshot_repo.load_summary = load_summary_spy

        with (
            mock.patch("app.services.strategy_row_repository.StrategyRowRepository", return_value=stub_row_repo),
            mock.patch("app.services.report_snapshot_service.ReportSnapshotRepository", return_value=stub_snapshot_repo),
        ):
            from app.api.strategy_api import get_strategy_rows
            response = get_strategy_rows("earnings_calendar", limit=20)

        load_summary_spy.assert_not_called()

    def test_source_is_row_store_when_rows_present(self):
        rows = [
            {
                "row_id": "ec-row-2",
                "ticker": "SBUX",
                "verdict": "HOLD / MONITOR",
                "strategy_id": "earnings_calendar",
                "normalization_status": "ok",
            }
        ]
        stub_row_repo = mock.MagicMock()
        stub_row_repo.read_latest.return_value = {"run_id": "run-ec-2", "rows": rows, "row_count": 1}
        with mock.patch("app.services.strategy_row_repository.StrategyRowRepository", return_value=stub_row_repo):
            from app.api.strategy_api import get_strategy_rows
            response = get_strategy_rows("earnings_calendar", limit=20)
        assert response.get("source") == "strategy_row_store"

    def test_unknown_strategy_id_fails_closed(self):
        from app.api.strategy_api import get_strategy_rows
        response = get_strategy_rows("__invalid_strategy__")
        assert "error" in response
        assert response.get("rows") == []
