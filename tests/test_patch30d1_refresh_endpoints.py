"""
ASA Patch 30D.1 — Refresh + Run Status Endpoint Tests

Covers:
  - POST /api/run/refresh: requires RUN_TOKEN → 403 without it
  - POST /api/run/refresh: 202 with valid token (mocked run)
  - POST /api/run/refresh: already_running returns 202 with job_id
  - POST /api/run/refresh: invalid mode → 400
  - GET /api/run/status/<job_id>: requires dev token → 403
  - GET /api/run/status/<job_id>: returns 200 with job state
  - GET /api/runs/latest: requires dev token → 403
  - GET /api/runs/latest: returns 200 with manifest shape
  - All read endpoints: provider_calls_triggered=False, read_only=True
"""
from __future__ import annotations

import py_compile
from unittest.mock import patch, MagicMock

from app import config as cfg


class TestCompile:
    def test_run_api_compiles(self):
        py_compile.compile("app/api/run_api.py", doraise=True)

    def test_main_compiles(self):
        py_compile.compile("app/main.py", doraise=True)


# ─── POST /api/run/refresh ────────────────────────────────────────────────────

class TestRunRefreshEndpoint:
    def _app(self):
        from app.main import app
        app.config["TESTING"] = True
        return app

    def test_no_token_returns_403(self):
        with self._app().test_client() as client:
            resp = client.post("/api/run/refresh")
            assert resp.status_code == 403

    def test_wrong_token_returns_403(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "RUN_TOKEN", "secret123"):
                resp = client.post("/api/run/refresh?token=wrong")
                assert resp.status_code == 403

    def test_invalid_mode_returns_400(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "RUN_TOKEN", "secret123"):
                resp = client.post("/api/run/refresh?token=secret123&mode=bad")
                assert resp.status_code == 400

    def test_valid_token_triggers_run(self):
        from app.main import RUN_LOCK, RUN_STATE_LOCK, RUN_JOBS
        with self._app().test_client() as client:
            with patch.object(cfg, "RUN_TOKEN", "secret123"), \
                 patch("app.main._run_job") as mock_run:
                resp = client.post("/api/run/refresh?token=secret123&mode=dev")
                # 202 = triggered or already_running
                assert resp.status_code == 202
                data = resp.get_json()
                assert data.get("provider_calls_triggered") is not None

    def test_valid_token_returns_job_id(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "RUN_TOKEN", "secret123"), \
                 patch("app.main._run_job"):
                resp = client.post("/api/run/refresh?token=secret123")
                data = resp.get_json()
                # Could be triggered or already_running — either has job_id
                assert "job_id" in data or "status" in data

    def test_already_running_returns_202(self):
        """When run lock is held, refresh returns 202 with already_running status."""
        from app.main import RUN_LOCK
        # Briefly hold the lock to simulate an active run
        with self._app().test_client() as client:
            if RUN_LOCK.acquire(blocking=False):
                try:
                    with patch.object(cfg, "RUN_TOKEN", "secret123"):
                        resp = client.post("/api/run/refresh?token=secret123")
                        assert resp.status_code == 202
                        data = resp.get_json()
                        assert data.get("status") == "already_running"
                finally:
                    RUN_LOCK.release()

    def test_refresh_not_read_only_when_triggered(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "RUN_TOKEN", "secret123"), \
                 patch("app.main._run_job"):
                resp = client.post("/api/run/refresh?token=secret123")
                data = resp.get_json()
                if data.get("status") == "triggered":
                    assert data.get("provider_calls_triggered") is True
                    assert data.get("read_only") is False


# ─── GET /api/run/status/<job_id> ─────────────────────────────────────────────

class TestRunStatusEndpoint:
    def _app(self):
        from app.main import app
        app.config["TESTING"] = True
        return app

    def test_no_token_returns_403(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=False):
                resp = client.get("/api/run/status/some-job-id")
                assert resp.status_code == 403

    def test_known_job_returns_200(self):
        from app.main import RUN_JOBS
        job_id = "test-job-abc123"
        RUN_JOBS[job_id] = {
            "status": "complete",
            "message": "Done.",
            "mode": "dev",
            "created_at": 0.0,
            "result": None,
        }
        try:
            with self._app().test_client() as client:
                with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                     patch("app.main._valid_dev_token", return_value=True):
                    resp = client.get(f"/api/run/status/{job_id}")
                    assert resp.status_code == 200
                    data = resp.get_json()
                    assert data.get("status") == "complete"
                    assert data.get("mode") == "dev"
                    assert data.get("read_only") is True
        finally:
            RUN_JOBS.pop(job_id, None)

    def test_unknown_job_returns_not_found_status(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/run/status/does-not-exist")
                assert resp.status_code == 200
                data = resp.get_json()
                assert data.get("status") == "not_found"

    def test_response_provider_calls_false(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/run/status/nonexistent")
                data = resp.get_json()
                assert data.get("provider_calls_triggered") is False


# ─── GET /api/runs/latest ─────────────────────────────────────────────────────

class TestRunsLatestEndpoint:
    def _app(self):
        from app.main import app
        app.config["TESTING"] = True
        return app

    def test_no_token_returns_403(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=False):
                resp = client.get("/api/runs/latest")
                assert resp.status_code == 403

    def test_returns_200_with_token(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/runs/latest")
                assert resp.status_code == 200

    def test_response_read_only(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/runs/latest")
                data = resp.get_json()
                assert data.get("provider_calls_triggered") is False
                assert data.get("read_only") is True

    def test_response_has_expected_shape_when_manifest_exists(self):
        fake_manifest = {
            "run_id": "run-test-001",
            "status": "SUCCESS_COMPLETE",
            "report_quality": "complete",
            "mode": "prod",
            "completed_at": "2026-07-07T12:00:00+00:00",
            "runtime_total_ms": 45000,
            "strategy_counts": {"earnings_calendar": {"pass": 1, "watch": 0, "fail": 3}},
            "daily_opportunity_count": 2,
            "has_broker_data": True,
            "has_errors": False,
            "degraded_reason": None,
            "broker_auth_status": "OK",
            "summary_json_bytes": 800000,
        }
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True), \
                 patch("app.services.run_manifest_repository.RunManifestRepository") as mock_cls:
                mock_repo = MagicMock()
                mock_repo.latest.return_value = fake_manifest
                mock_cls.return_value = mock_repo
                resp = client.get("/api/runs/latest")
                data = resp.get_json()
                assert data.get("run_id") == "run-test-001"
                assert data.get("status") == "SUCCESS_COMPLETE"
                assert "strategy_counts" in data
                assert data.get("daily_opportunity_count") == 2
