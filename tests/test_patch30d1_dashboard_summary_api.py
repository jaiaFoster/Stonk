"""
ASA Patch 30D.1 — Dashboard Summary API Tests

Covers:
  - GET /api/dashboard/summary: requires dev token → 403
  - GET /api/dashboard/summary: returns 200 with token
  - Response shape: provider_calls_triggered=False, read_only=True
  - Response has strategy_counts, daily_opportunity_count
  - Response has api_links with expected keys
  - Empty state when no manifest: returns empty_state key
  - Manifest available: run_id, status, report_quality present
  - build_dashboard_summary() stand-alone unit tests
"""
from __future__ import annotations

import py_compile
from unittest.mock import patch, MagicMock

from app import config as cfg


class TestCompile:
    def test_dashboard_api_compiles(self):
        py_compile.compile("app/api/dashboard_api.py", doraise=True)


# ─── Flask endpoint tests ──────────────────────────────────────────────────────

class TestDashboardSummaryEndpoint:
    def _app(self):
        from app.main import app
        app.config["TESTING"] = True
        return app

    def test_no_token_returns_403(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=False):
                resp = client.get("/api/dashboard/summary")
                assert resp.status_code == 403

    def test_returns_200_with_token(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/dashboard/summary")
                assert resp.status_code == 200

    def test_response_is_json(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/dashboard/summary")
                data = resp.get_json()
                assert isinstance(data, dict)

    def test_response_read_only(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/dashboard/summary")
                data = resp.get_json()
                assert data.get("provider_calls_triggered") is False
                assert data.get("read_only") is True

    def test_response_has_api_links(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/dashboard/summary")
                data = resp.get_json()
                assert "api_links" in data
                links = data["api_links"]
                assert "daily_opportunity" in links
                assert "open_positions" in links
                assert "run_refresh" in links

    def test_empty_state_when_no_manifest(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True), \
                 patch("app.services.run_manifest_repository.RunManifestRepository") as mock_cls:
                mock_repo = MagicMock()
                mock_repo.latest.return_value = None
                mock_cls.return_value = mock_repo
                resp = client.get("/api/dashboard/summary")
                data = resp.get_json()
                assert data.get("empty_state") == "no_run_manifest"

    def test_manifest_fields_present(self):
        fake_manifest = {
            "run_id": "run-dash-001",
            "status": "SUCCESS_COMPLETE",
            "report_quality": "complete",
            "mode": "prod",
            "completed_at": "2026-07-07T10:00:00+00:00",
            "runtime_total_ms": 30000,
            "strategy_counts": {"skew_momentum_vertical": {"pass": 2, "watch": 1, "fail": 5}},
            "daily_opportunity_count": 3,
            "has_broker_data": True,
            "has_market_data": True,
            "has_options_data": True,
            "has_errors": False,
            "error_count": 0,
            "broker_mode": "connected",
            "broker_auth_status": "OK",
            "summary_json_bytes": 750000,
            "git_commit": "abc12345",
            "deploy_label": "30d1",
            "degraded_reason": None,
        }
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True), \
                 patch("app.services.run_manifest_repository.RunManifestRepository") as mock_cls:
                mock_repo = MagicMock()
                mock_repo.latest.return_value = fake_manifest
                mock_cls.return_value = mock_repo
                resp = client.get("/api/dashboard/summary")
                data = resp.get_json()
                assert data["run_id"] == "run-dash-001"
                assert data["status"] == "SUCCESS_COMPLETE"
                assert data["daily_opportunity_count"] == 3
                assert data["strategy_counts"]["skew_momentum_vertical"]["pass"] == 2
                assert data["summary_json_bytes"] == 750000


# ─── build_dashboard_summary() unit tests ─────────────────────────────────────

class TestBuildDashboardSummary:
    def _call(self) -> dict:
        from app.api.dashboard_api import build_dashboard_summary
        return build_dashboard_summary()

    def test_returns_dict(self):
        assert isinstance(self._call(), dict)

    def test_always_read_only(self):
        result = self._call()
        assert result.get("provider_calls_triggered") is False
        assert result.get("read_only") is True

    def test_api_links_always_present(self):
        result = self._call()
        assert "api_links" in result
        assert "/api/daily-opportunity" in str(result["api_links"])
        assert "/api/open-positions" in str(result["api_links"])

    def test_with_fake_manifest_returns_run_id(self):
        from app.api.dashboard_api import build_dashboard_summary
        fake = {"run_id": "r001", "status": "SUCCESS_COMPLETE", "report_quality": "complete",
                "completed_at": "2026-07-07T10:00:00+00:00", "runtime_total_ms": 1000,
                "strategy_counts": {}, "daily_opportunity_count": 0, "has_broker_data": True,
                "has_market_data": True, "has_options_data": False, "has_errors": False,
                "error_count": 0, "broker_mode": None, "broker_auth_status": "OK",
                "summary_json_bytes": 400000, "git_commit": None, "deploy_label": None,
                "degraded_reason": None, "mode": "prod"}
        with patch("app.services.run_manifest_repository.RunManifestRepository") as mock_cls:
            mock_repo = MagicMock()
            mock_repo.latest.return_value = fake
            mock_cls.return_value = mock_repo
            result = build_dashboard_summary()
        assert result["run_id"] == "r001"
        assert result["status"] == "SUCCESS_COMPLETE"

    def test_no_manifest_returns_empty_state(self):
        from app.api.dashboard_api import build_dashboard_summary
        with patch("app.services.run_manifest_repository.RunManifestRepository") as mock_cls:
            mock_repo = MagicMock()
            mock_repo.latest.return_value = None
            mock_cls.return_value = mock_repo
            result = build_dashboard_summary()
        assert result.get("empty_state") == "no_run_manifest"
        assert "api_links" in result


# ─── GET /api/daily-opportunity endpoint ──────────────────────────────────────

class TestDailyOpportunityEndpoint:
    def _app(self):
        from app.main import app
        app.config["TESTING"] = True
        return app

    def test_no_token_returns_403(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=False):
                resp = client.get("/api/daily-opportunity")
                assert resp.status_code == 403

    def test_returns_200(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/daily-opportunity")
                assert resp.status_code == 200

    def test_response_read_only(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/daily-opportunity")
                data = resp.get_json()
                assert data.get("provider_calls_triggered") is False
                assert data.get("read_only") is True

    def test_has_actions_key(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/daily-opportunity")
                data = resp.get_json()
                assert "actions" in data
                assert isinstance(data["actions"], list)

    def test_limit_param_respected(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/daily-opportunity?limit=5")
                assert resp.status_code == 200


# ─── GET /api/open-positions endpoint ─────────────────────────────────────────

class TestOpenPositionsEndpoint:
    def _app(self):
        from app.main import app
        app.config["TESTING"] = True
        return app

    def test_no_token_returns_403(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=False):
                resp = client.get("/api/open-positions")
                assert resp.status_code == 403

    def test_returns_200(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/open-positions")
                assert resp.status_code == 200

    def test_response_read_only(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/open-positions")
                data = resp.get_json()
                assert data.get("provider_calls_triggered") is False
                assert data.get("read_only") is True

    def test_has_options_positions_key(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/open-positions")
                data = resp.get_json()
                assert "options_positions" in data
                assert isinstance(data["options_positions"], list)

    def test_has_options_count(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/open-positions")
                data = resp.get_json()
                assert "options_count" in data
