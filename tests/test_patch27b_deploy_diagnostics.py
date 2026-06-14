import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config
from app.main import app
from app.services.app_diagnostics_service import build_feature_health, build_latest_profiles
from app.services.report_snapshot_service import ReportSnapshotRepository
from app.services.run_manifest_repository import RunManifestRepository


class Patch27BDeployDiagnosticsTests(unittest.TestCase):
    def _state(self, path: str) -> None:
        ReportSnapshotRepository(path).save_success("run-27b", "dev", "payload", {
            "report_data": {
                "positions": [],
                "recommendations": [],
                "tradier_snapshot": {
                    "_runtime_profile": {"total_ms": 20, "phases_ms": {"daily_opportunity": 8, "positions": 12}},
                    "_payload_size_profile": {"sections_bytes": {"daily_opportunity": 25, "pipeline_status": 50}},
                    "_storage_profile": {"database_size_bytes": 100},
                    "_strategy_results": {
                        "forward_factor_calendar": {
                            "strategy_id": "forward_factor_calendar", "enabled": True, "ran": True,
                            "pass_count": 0, "watch_count": 1, "fail_count": 0, "skipped_count": 0,
                        },
                    },
                    "_daily_opportunity_engine": {"summary": {"count": 1}, "actions": [{"strategy_id": "stock_momentum"}]},
                },
                "log": ["ok"],
            },
        }, {}, {})
        RunManifestRepository(path).save({
            "run_id": "run-27b", "mode": "dev", "status": "complete",
            "report_quality": "SUCCESS_COMPLETE",
        })

    def test_diagnostics_routes_require_token_and_never_run_pipeline(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "state.sqlite3")
            with patch.object(config, "REPORT_SNAPSHOT_DB_PATH", path), \
                 patch.object(config, "RUN_MANIFEST_DB_PATH", path), \
                 patch.object(config, "DEV_API_TOKEN", "dev-token"), \
                 patch.object(config, "RUN_TOKEN", "run-token"), \
                 patch.object(config, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True):
                self._state(path)
                client = app.test_client()
                routes = (
                    "/api/dev/status",
                    "/api/dev/latest-run-manifest",
                    "/api/dev/latest-profiles",
                    "/api/dev/feature-health",
                )
                for route in routes:
                    self.assertEqual(client.get(route).status_code, 403)
                    with patch("app.main.run") as pipeline:
                        response = client.get(f"{route}?token=dev-token")
                    self.assertEqual(response.status_code, 200)
                    self.assertFalse(response.get_json()["provider_calls_triggered"])
                    pipeline.assert_not_called()

    def test_separate_dev_token_protects_snapshot_endpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "state.sqlite3")
            with patch.object(config, "REPORT_SNAPSHOT_DB_PATH", path), \
                 patch.object(config, "RUN_MANIFEST_DB_PATH", path), \
                 patch.object(config, "DEV_API_TOKEN", "dev-token"), \
                 patch.object(config, "RUN_TOKEN", "run-token"), \
                 patch.object(config, "ENABLE_DEV_SNAPSHOT_ENDPOINT", True):
                self._state(path)
                client = app.test_client()
                self.assertEqual(client.get("/api/dev/snapshot?token=run-token&mode=manifest_only").status_code, 403)
                self.assertEqual(client.get("/api/dev/snapshot?token=dev-token&mode=manifest_only").status_code, 200)

    def test_latest_profiles_identifies_slowest_and_largest(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "state.sqlite3")
            with patch.object(config, "REPORT_SNAPSHOT_DB_PATH", path), patch.object(config, "RUN_MANIFEST_DB_PATH", path):
                self._state(path)
                profiles = build_latest_profiles()
        self.assertEqual(profiles["slowest_runtime_phase"]["phase"], "positions")
        self.assertEqual(profiles["largest_payload_section"]["section"], "pipeline_status")
        self.assertFalse(profiles["provider_calls_triggered"])

    def test_feature_health_confirms_ff_dry_run_exclusion(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "state.sqlite3")
            with patch.object(config, "REPORT_SNAPSHOT_DB_PATH", path), \
                 patch.object(config, "RUN_MANIFEST_DB_PATH", path), \
                 patch.object(config, "FORWARD_FACTOR_DRY_RUN", True):
                self._state(path)
                health = build_feature_health()
        self.assertEqual(health["status"], "ok")
        self.assertTrue(health["checks"]["forward_factor_dry_run"])
        self.assertTrue(health["checks"]["forward_factor_daily_opportunity_excluded"])
        self.assertFalse(health["trade_execution_enabled"])

    def test_disabled_diagnostics_routes_return_not_found(self):
        with patch.object(config, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", False):
            self.assertEqual(app.test_client().get("/api/dev/status?token=anything").status_code, 404)


if __name__ == "__main__":
    unittest.main()
