"""
ASA Patch 30E — Refresh Endpoint + Dashboard API Compatibility Tests

Verifies 30D.1A endpoints still work after 30E changes:
  - All API modules compile
  - build_dashboard_summary() returns read_only=True, has FF link in api_links
  - build_daily_opportunity() returns read_only=True, provider_calls_triggered=False
  - build_open_positions() returns read_only=True
  - get_strategy_rows() returns read_only=True
  - Compact manifest produces schema_version=2
  - Forward factor calendar link in dashboard api_links
"""
from __future__ import annotations

import py_compile
from unittest.mock import patch


class TestCompile:
    def test_dashboard_api_compiles(self):
        py_compile.compile("app/api/dashboard_api.py", doraise=True)

    def test_daily_opportunity_api_compiles(self):
        py_compile.compile("app/api/daily_opportunity_api.py", doraise=True)

    def test_open_positions_api_compiles(self):
        py_compile.compile("app/api/open_positions_api.py", doraise=True)

    def test_strategy_api_compiles(self):
        py_compile.compile("app/api/strategy_api.py", doraise=True)

    def test_run_api_compiles(self):
        py_compile.compile("app/api/run_api.py", doraise=True)

    def test_main_compiles(self):
        py_compile.compile("app/main.py", doraise=True)

    def test_forward_factor_universal_compiles(self):
        py_compile.compile("app/strategies/forward_factor_universal.py", doraise=True)

    def test_report_snapshot_service_compiles(self):
        py_compile.compile("app/services/report_snapshot_service.py", doraise=True)


# ─── Dashboard summary ────────────────────────────────────────────────────────

class TestDashboardSummary:
    def _build(self, manifest=None) -> dict:
        from app.api.dashboard_api import build_dashboard_summary
        with patch("app.services.run_manifest_repository.RunManifestRepository") as MockRepo:
            MockRepo.return_value.latest.return_value = manifest
            return build_dashboard_summary()

    def test_no_manifest_returns_dict(self):
        assert isinstance(self._build(), dict)

    def test_no_manifest_read_only(self):
        assert self._build().get("read_only") is True

    def test_no_manifest_provider_calls_false(self):
        assert self._build().get("provider_calls_triggered") is False

    def test_no_manifest_has_api_links(self):
        result = self._build()
        assert "api_links" in result

    def test_api_links_has_forward_factor_calendar_rows(self):
        result = self._build()
        links = result.get("api_links", {})
        assert "forward_factor_calendar_rows" in links
        assert links["forward_factor_calendar_rows"] == "/api/strategies/forward_factor_calendar/rows"

    def test_api_links_has_daily_opportunity(self):
        result = self._build()
        links = result.get("api_links", {})
        assert "daily_opportunity" in links

    def test_api_links_has_open_positions(self):
        result = self._build()
        links = result.get("api_links", {})
        assert "open_positions" in links

    def test_api_links_has_run_refresh(self):
        result = self._build()
        links = result.get("api_links", {})
        assert "run_refresh" in links

    def test_with_manifest_read_only(self):
        manifest = {
            "run_id": "run-001",
            "status": "complete",
            "report_quality": "complete",
            "completed_at": "2026-07-07T10:00:00Z",
        }
        result = self._build(manifest=manifest)
        assert result.get("read_only") is True

    def test_with_manifest_has_run_id(self):
        manifest = {"run_id": "run-dash-001", "status": "complete"}
        result = self._build(manifest=manifest)
        assert result.get("run_id") == "run-dash-001"


# ─── Daily Opportunity ────────────────────────────────────────────────────────

class TestDailyOpportunityCompat:
    def test_returns_read_only(self):
        from app.api.daily_opportunity_api import build_daily_opportunity_response
        with patch("app.services.report_snapshot_service.ReportSnapshotRepository") as MockRepo:
            MockRepo.return_value.latest_success.return_value = None
            result = build_daily_opportunity_response()
        assert result.get("read_only") is True

    def test_returns_provider_calls_false(self):
        from app.api.daily_opportunity_api import build_daily_opportunity_response
        with patch("app.services.report_snapshot_service.ReportSnapshotRepository") as MockRepo:
            MockRepo.return_value.latest_success.return_value = None
            result = build_daily_opportunity_response()
        assert result.get("provider_calls_triggered") is False

    def test_returns_dict(self):
        from app.api.daily_opportunity_api import build_daily_opportunity_response
        with patch("app.services.report_snapshot_service.ReportSnapshotRepository") as MockRepo:
            MockRepo.return_value.latest_success.return_value = None
            result = build_daily_opportunity_response()
        assert isinstance(result, dict)


# ─── Open Positions ───────────────────────────────────────────────────────────

class TestOpenPositionsCompat:
    def test_returns_read_only(self):
        from app.api.open_positions_api import build_open_positions_response
        with patch("app.services.report_snapshot_service.ReportSnapshotRepository") as MockRepo:
            MockRepo.return_value.latest_success.return_value = None
            result = build_open_positions_response()
        assert result.get("read_only") is True

    def test_returns_provider_calls_false(self):
        from app.api.open_positions_api import build_open_positions_response
        with patch("app.services.report_snapshot_service.ReportSnapshotRepository") as MockRepo:
            MockRepo.return_value.latest_success.return_value = None
            result = build_open_positions_response()
        assert result.get("provider_calls_triggered") is False


# ─── Strategy rows ────────────────────────────────────────────────────────────

class TestStrategyRowsCompat:
    def test_forward_factor_calendar_returns_read_only(self):
        from app.api.strategy_api import get_strategy_rows
        with patch("app.services.report_snapshot_service.ReportSnapshotRepository") as MockRepo:
            MockRepo.return_value.latest_success.return_value = None
            result = get_strategy_rows("forward_factor_calendar")
        assert result.get("read_only") is True

    def test_forward_factor_calendar_returns_provider_calls_false(self):
        from app.api.strategy_api import get_strategy_rows
        with patch("app.services.report_snapshot_service.ReportSnapshotRepository") as MockRepo:
            MockRepo.return_value.latest_success.return_value = None
            result = get_strategy_rows("forward_factor_calendar")
        assert result.get("provider_calls_triggered") is False

    def test_skew_still_works(self):
        from app.api.strategy_api import get_strategy_rows
        with patch("app.services.report_snapshot_service.ReportSnapshotRepository") as MockRepo:
            MockRepo.return_value.latest_success.return_value = None
            result = get_strategy_rows("skew_momentum_vertical")
        assert isinstance(result, dict)
        assert result.get("read_only") is True


# ─── Compact manifest schema_version=2 ───────────────────────────────────────

class TestCompactManifestSchemaVersion:
    def test_compact_manifest_has_schema_version_2(self):
        from app.services.report_snapshot_service import build_compact_manifest_summary
        result = build_compact_manifest_summary({})
        assert result["schema_version"] == 2

    def test_compact_manifest_has_compact_manifest_flag(self):
        from app.services.report_snapshot_service import build_compact_manifest_summary
        result = build_compact_manifest_summary({})
        assert result["compact_manifest"] is True

    def test_compact_manifest_api_links_contains_ff_rows(self):
        from app.services.report_snapshot_service import build_compact_manifest_summary
        result = build_compact_manifest_summary({})
        links = result.get("api_links", {})
        assert "forward_factor_calendar_rows" in links
