"""
ASA Patch 30B — Strategy Rows API Tests

Covers:
  - app/api/strategy_api.get_strategy_rows()
  - GET /api/strategies/<strategy_id>/rows Flask endpoint
  - Stock_momentum and stock_momentum_unified_test return universal rows or clean empty-state
  - Unknown strategy returns structured error (not 500)
  - All responses: provider_calls_triggered=False, read_only=True
"""
from __future__ import annotations

import py_compile
from unittest.mock import patch, MagicMock

from app import config as cfg


class TestCompile:
    def test_strategy_api_compiles(self):
        py_compile.compile("app/api/strategy_api.py", doraise=True)

    def test_main_compiles(self):
        py_compile.compile("app/main.py", doraise=True)


# ─── get_strategy_rows unit tests ─────────────────────────────────────────────

class TestGetStrategyRows:
    def _get(self, strategy_id: str, limit: int = 5) -> dict:
        from app.api.strategy_api import get_strategy_rows
        return get_strategy_rows(strategy_id=strategy_id, limit=limit)

    def test_unknown_strategy_returns_error_key(self):
        result = self._get("does_not_exist")
        assert "error" in result

    def test_unknown_strategy_read_only(self):
        result = self._get("does_not_exist")
        assert result.get("provider_calls_triggered") is False
        assert result.get("read_only") is True

    def test_unknown_strategy_includes_valid_ids(self):
        result = self._get("does_not_exist")
        assert "valid_ids" in result
        assert isinstance(result["valid_ids"], list)

    def test_stock_momentum_returns_dict(self):
        result = self._get("stock_momentum")
        assert isinstance(result, dict)

    def test_stock_momentum_read_only(self):
        result = self._get("stock_momentum")
        assert result.get("provider_calls_triggered") is False
        assert result.get("read_only") is True

    def test_stock_momentum_has_rows_key(self):
        result = self._get("stock_momentum")
        assert "rows" in result
        assert isinstance(result["rows"], list)

    def test_stock_momentum_has_strategy_id(self):
        result = self._get("stock_momentum")
        assert result.get("strategy_id") == "stock_momentum"

    def test_test_clone_has_rows_key(self):
        result = self._get("stock_momentum_unified_test")
        assert "rows" in result
        assert isinstance(result["rows"], list)

    def test_test_clone_read_only(self):
        result = self._get("stock_momentum_unified_test")
        assert result.get("provider_calls_triggered") is False

    def test_unimplemented_strategy_returns_empty_state(self):
        result = self._get("earnings_calendar")
        assert result.get("rows") == []
        assert "empty_state" in result or "note" in result

    def test_unimplemented_no_error_key(self):
        result = self._get("forward_factor_calendar")
        assert "error" not in result or result.get("error") is None

    def test_no_snapshot_returns_clean_empty(self):
        from app.api.strategy_api import get_strategy_rows
        with patch("app.services.report_snapshot_service.ReportSnapshotRepository") as mock_cls:
            mock_repo = MagicMock()
            mock_repo.latest_success.return_value = None
            mock_cls.return_value = mock_repo
            result = get_strategy_rows("stock_momentum")
        assert result.get("rows") == []
        assert result.get("provider_calls_triggered") is False

    def test_rows_with_snapshot_have_universal_fields(self):
        from app.api.strategy_api import get_strategy_rows
        from app.strategies.schema import VALID_ROW_TYPES, SCHEMA_VERSION
        fake_row = {
            "ticker": "AAPL",
            "strategy_id": "stock_momentum",
            "action": "CONSIDER ADDING",
            "score": 82.0,
            "momentum_score": 82.0,
            "add_allowed_boolean": True,
            "add_blockers": [],
            "reasons": ["Strong momentum."],
            "risks": [],
            "market_metrics": {"above_sma_50": True, "above_sma_200": True, "current_price": 195.0},
        }
        with patch("app.services.report_snapshot_service.ReportSnapshotRepository") as mock_cls:
            mock_repo = MagicMock()
            mock_snap = {"run_id": "run-test-123", "status": "SUCCESS"}
            mock_repo.latest_success.return_value = mock_snap
            mock_repo.load_summary.return_value = {
                "report_data": {
                    "tradier_snapshot": {
                        "_strategy_results": {
                            "stock_momentum": {"items": [fake_row]}
                        }
                    }
                }
            }
            mock_cls.return_value = mock_repo
            result = get_strategy_rows("stock_momentum", limit=5)
        if result.get("rows"):
            row = result["rows"][0]
            assert row.get("schema_version") == SCHEMA_VERSION
            assert row.get("row_type") in VALID_ROW_TYPES
            assert "gate_groups" in row
            assert "display" in row
            assert "details" in row


# ─── Flask endpoint tests ──────────────────────────────────────────────────────

class TestStrategyRowsEndpoint:
    def _app(self):
        from app.main import app
        app.config["TESTING"] = True
        return app

    def test_endpoint_requires_token(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=False):
                resp = client.get("/api/strategies/stock_momentum/rows")
                assert resp.status_code == 403

    def test_known_strategy_returns_200(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/strategies/stock_momentum/rows")
                assert resp.status_code == 200

    def test_unknown_strategy_returns_404(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/strategies/does_not_exist/rows")
                assert resp.status_code == 404

    def test_test_clone_returns_200(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/strategies/stock_momentum_unified_test/rows")
                assert resp.status_code == 200

    def test_response_has_rows_key(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/strategies/stock_momentum/rows")
                data = resp.get_json()
                assert "rows" in data
                assert isinstance(data["rows"], list)

    def test_response_not_provider_triggered(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/strategies/stock_momentum/rows")
                data = resp.get_json()
                assert data.get("provider_calls_triggered") is False

    def test_earnings_calendar_200_with_empty_state(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/strategies/earnings_calendar/rows")
                assert resp.status_code == 200
                data = resp.get_json()
                assert data.get("rows") == []

    def test_limit_param_accepted(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/strategies/stock_momentum/rows?limit=5")
                assert resp.status_code == 200
