import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config
from app.main import app
from app.services.report_snapshot_service import ReportSnapshotRepository
from app.services.usage_telemetry_service import (
    UsageTelemetryRepository,
    build_usage_telemetry_diagnostics,
    record_usage_event,
)


class Patch27IUsageStorageTelemetryTests(unittest.TestCase):
    def test_event_storage_keeps_only_allowed_small_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "state.sqlite3")
            with patch.object(config, "USAGE_TELEMETRY_DB_PATH", path):
                repository = UsageTelemetryRepository()
                saved = repository.record_event(
                    "detail_request",
                    section="strategy",
                    source="test",
                    metadata={
                        "strategy_id": "forward_factor_calendar",
                        "raw_payload": {"secret": "must-not-store"},
                        "account_number": "must-not-store",
                    },
                )
                with repository._connect() as conn:
                    row = conn.execute("SELECT metadata_json FROM usage_events").fetchone()
        self.assertTrue(saved)
        self.assertEqual(json.loads(row["metadata_json"]), {"strategy_id": "forward_factor_calendar"})

    def test_snapshot_save_records_size_profile_without_payload_content(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "state.sqlite3")
            summary = {
                "report_data": {
                    "positions": [],
                    "recommendations": [],
                    "news": {},
                    "tradier_snapshot": {
                        "_payload_size_profile": {"sections_bytes": {"tradier_snapshot": 1234, "secret_raw": "not-a-size"}},
                        "secret_raw": "private provider content",
                    },
                    "log": [],
                },
            }
            with patch.object(config, "USAGE_TELEMETRY_DB_PATH", path), patch.object(config, "REPORT_SNAPSHOT_DB_PATH", path):
                ReportSnapshotRepository().save_success("run-1", "dev", "payload", summary, {}, {})
                telemetry = UsageTelemetryRepository().summary()
        latest = telemetry["latest_size_profile"]
        self.assertEqual(latest["run_id"], "run-1")
        self.assertEqual(latest["largest_sections"], [{"section": "tradier_snapshot", "bytes": 1234}])
        self.assertNotIn("private provider content", json.dumps(telemetry))

    def test_telemetry_failure_never_breaks_route(self):
        with patch.object(config, "RUN_TOKEN", "token"), \
             patch("app.services.usage_telemetry_service.record_usage_event", side_effect=RuntimeError("telemetry unavailable")):
            response = app.test_client().post(
                "/api/usage/event?token=token",
                json={"event_type": "copy_export", "section": "exports"},
            )
        self.assertEqual(response.status_code, 202)

    def test_fail_safe_recorder_returns_false_on_database_error(self):
        with patch.object(config, "USAGE_TELEMETRY_ENABLED", True), \
             patch("app.services.usage_telemetry_service.UsageTelemetryRepository", side_effect=RuntimeError("db unavailable")):
            self.assertFalse(record_usage_event("dashboard_load"))

    def test_usage_event_route_is_token_protected_and_provider_free(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "state.sqlite3")
            with patch.object(config, "USAGE_TELEMETRY_DB_PATH", path), \
                 patch.object(config, "RUN_TOKEN", "token"):
                client = app.test_client()
                self.assertEqual(client.post("/api/usage/event", json={"event_type": "copy_export"}).status_code, 403)
                with patch("app.main.run") as pipeline:
                    response = client.post(
                        "/api/usage/event?token=token",
                        json={"event_type": "copy_export", "section": "exports", "metadata": {"export_key": "dailyBrief"}},
                    )
                pipeline.assert_not_called()
        self.assertEqual(response.status_code, 202)
        self.assertFalse(response.get_json()["provider_calls_triggered"])

    def test_telemetry_diagnostics_are_read_only_provider_free(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "state.sqlite3")
            with patch.object(config, "USAGE_TELEMETRY_DB_PATH", path), \
                 patch.object(config, "DEV_API_TOKEN", "dev-token"), \
                 patch.object(config, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True):
                UsageTelemetryRepository().record_event("detail_request", section="providers")
                with patch("app.main.run") as pipeline:
                    response = app.test_client().get("/api/dev/usage-telemetry?token=dev-token")
                pipeline.assert_not_called()
        body = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["read_only"])
        self.assertFalse(body["provider_calls_triggered"])
        self.assertEqual(body["telemetry"]["most_requested_detail_sections"][0]["section"], "providers")

    def test_snapshot_modes_and_detail_requests_are_counted(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "state.sqlite3")
            summary = {"report_data": {"positions": [], "recommendations": [], "news": {}, "tradier_snapshot": {}, "log": []}}
            with patch.object(config, "USAGE_TELEMETRY_DB_PATH", path), \
                 patch.object(config, "REPORT_SNAPSHOT_DB_PATH", path), \
                 patch.object(config, "RUN_TOKEN", "token"), \
                 patch.object(config, "ENABLE_DEV_SNAPSHOT_ENDPOINT", True):
                ReportSnapshotRepository().save_success("run-1", "dev", "payload", summary, {}, {})
                client = app.test_client()
                self.assertEqual(client.get("/api/dev/snapshot?token=token&mode=latest").status_code, 200)
                self.assertEqual(client.get("/api/dev/snapshot/detail/providers?token=token").status_code, 404)
                telemetry = UsageTelemetryRepository().summary()
        self.assertEqual(telemetry["event_counts"]["snapshot_request"], 1)
        self.assertEqual(telemetry["event_counts"]["detail_request"], 1)

    def test_diagnostics_builder_degrades_quietly(self):
        with patch("app.services.usage_telemetry_service.UsageTelemetryRepository", side_effect=RuntimeError("db unavailable")):
            result = build_usage_telemetry_diagnostics()
        self.assertEqual(result["status"], "warning")
        self.assertFalse(result["provider_calls_triggered"])


if __name__ == "__main__":
    unittest.main()
