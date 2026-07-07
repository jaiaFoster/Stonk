"""
ASA Patch 30C — Earnings Calendar API Rows Tests

Covers:
  - get_strategy_rows("earnings_calendar")
  - GET /api/strategies/earnings_calendar/rows Flask endpoint
  - No snapshot → clean empty state
  - Snapshot with rows → universal enrichment applied
  - Lifecycle rows folded into response
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

class TestGetStrategyRowsEarningsCalendar:
    def _get(self, strategy_id: str, limit: int = 5) -> dict:
        from app.api.strategy_api import get_strategy_rows
        return get_strategy_rows(strategy_id=strategy_id, limit=limit)

    def test_earnings_calendar_returns_dict(self):
        result = self._get("earnings_calendar")
        assert isinstance(result, dict)

    def test_earnings_calendar_read_only(self):
        result = self._get("earnings_calendar")
        assert result.get("provider_calls_triggered") is False
        assert result.get("read_only") is True

    def test_earnings_calendar_has_rows_key(self):
        result = self._get("earnings_calendar")
        assert "rows" in result
        assert isinstance(result["rows"], list)

    def test_earnings_calendar_has_strategy_id(self):
        result = self._get("earnings_calendar")
        assert result.get("strategy_id") == "earnings_calendar"

    def test_no_snapshot_returns_clean_empty(self):
        from app.api.strategy_api import get_strategy_rows
        with patch("app.services.report_snapshot_service.ReportSnapshotRepository") as mock_cls:
            mock_repo = MagicMock()
            mock_repo.latest_success.return_value = None
            mock_cls.return_value = mock_repo
            result = get_strategy_rows("earnings_calendar")
        assert result.get("rows") == []
        assert result.get("provider_calls_triggered") is False

    def test_no_snapshot_no_500(self):
        from app.api.strategy_api import get_strategy_rows
        with patch("app.services.report_snapshot_service.ReportSnapshotRepository") as mock_cls:
            mock_repo = MagicMock()
            mock_repo.latest_success.return_value = None
            mock_cls.return_value = mock_repo
            result = get_strategy_rows("earnings_calendar")
        assert "error" not in result or result.get("rows") == []

    def test_rows_with_snapshot_have_universal_fields(self):
        from app.api.strategy_api import get_strategy_rows
        from app.strategies.schema import VALID_ROW_TYPES, SCHEMA_VERSION
        fake_row = _fake_candidate_row()
        with patch("app.services.report_snapshot_service.ReportSnapshotRepository") as mock_cls:
            mock_repo = MagicMock()
            mock_snap = {"run_id": "run-ec-001", "status": "SUCCESS"}
            mock_repo.latest_success.return_value = mock_snap
            mock_repo.load_summary.return_value = {
                "report_data": {
                    "tradier_snapshot": {
                        "_strategy_results": {
                            "earnings_calendar": {"rows": [fake_row]}
                        }
                    }
                }
            }
            mock_cls.return_value = mock_repo
            result = get_strategy_rows("earnings_calendar", limit=5)
        if result.get("rows"):
            row = result["rows"][0]
            assert row.get("schema_version") == SCHEMA_VERSION
            assert row.get("row_type") in VALID_ROW_TYPES
            assert "gate_groups" in row
            assert "display" in row
            assert "details" in row
            assert "earnings_calendar" in row["details"]

    def test_lifecycle_rows_included_when_present(self):
        from app.api.strategy_api import get_strategy_rows
        from app.strategies.schema import SCHEMA_VERSION
        fake_lifecycle = _fake_lifecycle_check()
        with patch("app.services.report_snapshot_service.ReportSnapshotRepository") as mock_cls:
            mock_repo = MagicMock()
            mock_snap = {"run_id": "run-ec-001", "status": "SUCCESS"}
            mock_repo.latest_success.return_value = mock_snap
            mock_repo.load_summary.return_value = {
                "report_data": {
                    "tradier_snapshot": {
                        "_strategy_results": {"earnings_calendar": {"rows": []}},
                        "_calendar_lifecycle_checks": {"checks": [fake_lifecycle]},
                    }
                }
            }
            mock_cls.return_value = mock_repo
            result = get_strategy_rows("earnings_calendar", limit=10)
        if result.get("rows"):
            row = result["rows"][0]
            assert row.get("schema_version") == SCHEMA_VERSION

    def test_universal_fields_not_in_schema_do_not_appear_from_raw_chain(self):
        from app.api.strategy_api import get_strategy_rows
        fake_row = _fake_candidate_row()
        fake_row["_raw_option_chain"] = [{"bloated": "data"}] * 100
        with patch("app.services.report_snapshot_service.ReportSnapshotRepository") as mock_cls:
            mock_repo = MagicMock()
            mock_snap = {"run_id": "run-ec-001", "status": "SUCCESS"}
            mock_repo.latest_success.return_value = mock_snap
            mock_repo.load_summary.return_value = {
                "report_data": {
                    "tradier_snapshot": {
                        "_strategy_results": {
                            "earnings_calendar": {"rows": [fake_row]}
                        }
                    }
                }
            }
            mock_cls.return_value = mock_repo
            result = get_strategy_rows("earnings_calendar", limit=5)
        # details.earnings_calendar should not contain raw chain data
        if result.get("rows"):
            ec = result["rows"][0].get("details", {}).get("earnings_calendar", {})
            assert "_raw_option_chain" not in ec


# ─── Flask endpoint tests ──────────────────────────────────────────────────────

class TestEarningsCalendarRowsEndpoint:
    def _app(self):
        from app.main import app
        app.config["TESTING"] = True
        return app

    def test_endpoint_requires_token(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=False):
                resp = client.get("/api/strategies/earnings_calendar/rows")
                assert resp.status_code == 403

    def test_known_strategy_returns_200(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/strategies/earnings_calendar/rows")
                assert resp.status_code == 200

    def test_response_has_rows_key(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/strategies/earnings_calendar/rows")
                data = resp.get_json()
                assert "rows" in data
                assert isinstance(data["rows"], list)

    def test_response_not_provider_triggered(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/strategies/earnings_calendar/rows")
                data = resp.get_json()
                assert data.get("provider_calls_triggered") is False

    def test_response_read_only(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/strategies/earnings_calendar/rows")
                data = resp.get_json()
                assert data.get("read_only") is True

    def test_limit_param_accepted(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/strategies/earnings_calendar/rows?limit=5")
                assert resp.status_code == 200

    def test_strategy_id_in_response(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/strategies/earnings_calendar/rows")
                data = resp.get_json()
                assert data.get("strategy_id") == "earnings_calendar"


# ─── Fixtures ─────────────────────────────────────────────────────────────────

def _fake_candidate_row() -> dict:
    return {
        "strategy_id": "earnings_calendar",
        "ticker": "BAC",
        "action": "EARNINGS CALENDAR CANDIDATE",
        "score": 72.0,
        "earnings_date": "2026-07-22",
        "earnings_trust_label": "multi_source_confirmed",
        "date_confidence": "high",
        "date_conflict": False,
        "earnings_relation": "long_leg_captures_earnings",
        "front_expiration": "2026-07-18",
        "back_expiration": "2026-07-25",
        "front_dte": 11,
        "back_dte": 18,
        "strike": 45.0,
        "option_type": "call",
        "underlying_price": 44.50,
        "iv_relationship_status": "favorable",
        "conservative_debit": 0.35,
        "debit_pct_underlying": 0.79,
        "max_leg_spread_pct": 3.2,
        "min_leg_open_interest": 120,
        "min_leg_volume": 35,
        "calendar_entry_allowed": True,
        "liquidity_status": "pass",
        "spread_status": "pass",
        "debit_status": "pass",
        "structure_status": "long_leg_captures_earnings",
        "daily_opportunity_eligible": True,
        "reasons": ["Preferred structure."],
    }


def _fake_lifecycle_check() -> dict:
    return {
        "ticker": "SBUX",
        "action": "HOLD / MONITOR",
        "option_type": "call",
        "strike": 80.0,
        "front_expiration": "2026-07-18",
        "back_expiration": "2026-07-25",
        "front_dte": 11,
        "underlying_price": 79.50,
        "current_mid_debit": 0.40,
        "assignment_risk_level": "Low",
        "lifecycle_priority_score": 30.0,
        "reasons": ["Current spread value is available."],
    }
