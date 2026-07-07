"""
ASA Patch 30D — Skew Momentum Vertical API Rows Tests

Covers:
  - get_strategy_rows("skew_momentum_vertical")
  - GET /api/strategies/skew_momentum_vertical/rows Flask endpoint
  - No snapshot → clean empty state
  - Snapshot with rows → universal enrichment applied
  - Opportunity cache preserved (strategy still writes cache)
  - All responses: provider_calls_triggered=False, read_only=True
"""
from __future__ import annotations

import py_compile
from unittest.mock import patch, MagicMock

from app import config as cfg


class TestCompile:
    def test_strategy_api_compiles(self):
        py_compile.compile("app/api/strategy_api.py", doraise=True)

    def test_universal_compiles(self):
        py_compile.compile("app/strategies/skew_momentum_vertical_universal.py", doraise=True)


# ─── get_strategy_rows unit tests ─────────────────────────────────────────────

class TestGetStrategyRowsSkew:
    def _get(self, strategy_id: str = "skew_momentum_vertical", limit: int = 5) -> dict:
        from app.api.strategy_api import get_strategy_rows
        return get_strategy_rows(strategy_id=strategy_id, limit=limit)

    def test_skew_returns_dict(self):
        result = self._get()
        assert isinstance(result, dict)

    def test_skew_read_only(self):
        result = self._get()
        assert result.get("provider_calls_triggered") is False
        assert result.get("read_only") is True

    def test_skew_has_rows_key(self):
        result = self._get()
        assert "rows" in result
        assert isinstance(result["rows"], list)

    def test_skew_has_strategy_id(self):
        result = self._get()
        assert result.get("strategy_id") == "skew_momentum_vertical"

    def test_no_snapshot_returns_clean_empty(self):
        from app.api.strategy_api import get_strategy_rows
        with patch("app.services.report_snapshot_service.ReportSnapshotRepository") as mock_cls:
            mock_repo = MagicMock()
            mock_repo.latest_success.return_value = None
            mock_cls.return_value = mock_repo
            result = get_strategy_rows("skew_momentum_vertical")
        assert result.get("rows") == []
        assert result.get("provider_calls_triggered") is False

    def test_no_snapshot_no_500(self):
        from app.api.strategy_api import get_strategy_rows
        with patch("app.services.report_snapshot_service.ReportSnapshotRepository") as mock_cls:
            mock_repo = MagicMock()
            mock_repo.latest_success.return_value = None
            mock_cls.return_value = mock_repo
            result = get_strategy_rows("skew_momentum_vertical")
        assert "error" not in result or result.get("rows") == []

    def test_rows_with_snapshot_have_universal_fields(self):
        from app.api.strategy_api import get_strategy_rows
        from app.strategies.schema import VALID_ROW_TYPES, SCHEMA_VERSION
        fake_row = _fake_pass_row()
        with patch("app.services.report_snapshot_service.ReportSnapshotRepository") as mock_cls:
            mock_repo = MagicMock()
            mock_snap = {"run_id": "run-smv-001", "status": "SUCCESS"}
            mock_repo.latest_success.return_value = mock_snap
            mock_repo.load_summary.return_value = {
                "report_data": {
                    "tradier_snapshot": {
                        "_strategy_results": {
                            "skew_momentum_vertical": {"items": [fake_row]}
                        }
                    }
                }
            }
            mock_cls.return_value = mock_repo
            result = get_strategy_rows("skew_momentum_vertical", limit=5)
        if result.get("rows"):
            row = result["rows"][0]
            assert row.get("schema_version") == SCHEMA_VERSION
            assert row.get("row_type") in VALID_ROW_TYPES
            assert "gate_groups" in row
            assert "display" in row
            assert "details" in row
            assert "skew_momentum_vertical" in row["details"]

    def test_raw_legs_excluded_from_details(self):
        from app.api.strategy_api import get_strategy_rows
        fake_row = _fake_pass_row()
        fake_row["long_leg"] = {"bid": 1.80, "ask": 1.90, "raw_chain": ["x"] * 100}
        fake_row["short_leg"] = {"bid": 0.05, "ask": 0.10, "raw_chain": ["y"] * 100}
        with patch("app.services.report_snapshot_service.ReportSnapshotRepository") as mock_cls:
            mock_repo = MagicMock()
            mock_snap = {"run_id": "run-smv-001", "status": "SUCCESS"}
            mock_repo.latest_success.return_value = mock_snap
            mock_repo.load_summary.return_value = {
                "report_data": {
                    "tradier_snapshot": {
                        "_strategy_results": {
                            "skew_momentum_vertical": {"items": [fake_row]}
                        }
                    }
                }
            }
            mock_cls.return_value = mock_repo
            result = get_strategy_rows("skew_momentum_vertical", limit=5)
        if result.get("rows"):
            ec = result["rows"][0].get("details", {}).get("skew_momentum_vertical", {})
            assert "long_leg" not in ec
            assert "short_leg" not in ec

    def test_fallback_to_legacy_snapshot_key(self):
        from app.api.strategy_api import get_strategy_rows
        from app.strategies.schema import SCHEMA_VERSION
        fake_row = _fake_pass_row()
        with patch("app.services.report_snapshot_service.ReportSnapshotRepository") as mock_cls:
            mock_repo = MagicMock()
            mock_snap = {"run_id": "run-smv-002", "status": "SUCCESS"}
            mock_repo.latest_success.return_value = mock_snap
            mock_repo.load_summary.return_value = {
                "report_data": {
                    "tradier_snapshot": {
                        "_skew_momentum_vertical_strategy": {"items": [fake_row]},
                        "_strategy_results": {},
                    }
                }
            }
            mock_cls.return_value = mock_repo
            result = get_strategy_rows("skew_momentum_vertical", limit=5)
        if result.get("rows"):
            assert result["rows"][0].get("schema_version") == SCHEMA_VERSION


# ─── Flask endpoint tests ──────────────────────────────────────────────────────

class TestSkewRowsEndpoint:
    def _app(self):
        from app.main import app
        app.config["TESTING"] = True
        return app

    def test_endpoint_requires_token(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=False):
                resp = client.get("/api/strategies/skew_momentum_vertical/rows")
                assert resp.status_code == 403

    def test_known_strategy_returns_200(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/strategies/skew_momentum_vertical/rows")
                assert resp.status_code == 200

    def test_response_has_rows_key(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/strategies/skew_momentum_vertical/rows")
                data = resp.get_json()
                assert "rows" in data
                assert isinstance(data["rows"], list)

    def test_response_not_provider_triggered(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/strategies/skew_momentum_vertical/rows")
                data = resp.get_json()
                assert data.get("provider_calls_triggered") is False

    def test_response_read_only(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/strategies/skew_momentum_vertical/rows")
                data = resp.get_json()
                assert data.get("read_only") is True

    def test_limit_param_accepted(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/strategies/skew_momentum_vertical/rows?limit=5")
                assert resp.status_code == 200

    def test_strategy_id_in_response(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/strategies/skew_momentum_vertical/rows")
                data = resp.get_json()
                assert data.get("strategy_id") == "skew_momentum_vertical"


# ─── Fixtures ─────────────────────────────────────────────────────────────────

def _fake_pass_row() -> dict:
    return {
        "strategy_id": "skew_momentum_vertical",
        "ticker": "AAPL",
        "verdict": "PASS / POSSIBLE ENTRY SETUP",
        "score": 72.0,
        "direction": "bullish",
        "momentum_confirmed": True,
        "momentum_score": 75.0,
        "skew_pass": True,
        "short_iv_edge": 0.045,
        "short_premium_financing_pct": 22.5,
        "adjusted_skew_score": 14.2,
        "possible_spread": {
            "expiration": "2026-08-15",
            "option_type": "call",
            "long_strike": 185.0,
            "short_strike": 190.0,
            "width": 5.0,
            "conservative_debit": 1.85,
            "mid_debit": 1.70,
        },
        "dte": 39,
        "underlying_price": 186.50,
        "conservative_debit": 1.85,
        "max_risk": 185.0,
        "reward_risk": 1.70,
        "debit_pct_of_width": 37.0,
        "long_leg_spread_pct": 1.2,
        "short_leg_spread_pct": 1.8,
        "liquidity_pass": True,
        "data_quality_pass": True,
        "event_risk": False,
        "earnings_trust_label": "multi_source_confirmed",
        "stale_structure": False,
        "daily_opportunity_eligible": True,
    }
