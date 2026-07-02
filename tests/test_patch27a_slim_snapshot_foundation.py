import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app import config
from app.main import app
from app.services.developer_snapshot_service import build_developer_snapshot
from app.services.payload_profile_service import build_payload_size_profile
from app.services.redaction_service import redact
from app.services.report_snapshot_service import ReportSnapshotRepository
from app.services.run_manifest_repository import RunManifestRepository, build_run_manifest
from app.services.runtime_profile_service import build_runtime_profile
from app.services.storage_profile_service import build_storage_profile


class Patch27ASlimSnapshotFoundationTests(unittest.TestCase):
    def test_runtime_and_payload_profiles_exist(self):
        runtime = build_runtime_profile({"total_duration_ms": 25, "steps": [{"key": "positions", "duration_ms": 12}, {"key": "news", "duration_ms": 8}]})
        payload = build_payload_size_profile("hello", [], {}, [], {"_pipeline_status": {}}, [], {})
        self.assertEqual(runtime["total_ms"], 25)
        self.assertGreater(payload["sections_bytes"]["payload_text"], 0)

    def test_storage_profile_and_pruning_report_are_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "market.sqlite3")
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.execute("CREATE TABLE market_data_fetch_log (created_at TEXT)")
                conn.execute("INSERT INTO market_data_fetch_log VALUES ('2000-01-01T00:00:00+00:00')")
            profile = build_storage_profile(path)
            self.assertEqual(profile["table_rows"]["market_data_fetch_log"], 1)
            self.assertEqual(profile["pruning_dry_run"]["mode"], "dry_run")
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM market_data_fetch_log").fetchone()[0], 1)

    def test_run_manifest_is_small_and_persistent(self):
        manifest = build_run_manifest("run-1", "dev", "complete", "SUCCESS_COMPLETE", {"total_ms": 10}, {"sections_bytes": {"payload_text": 20}}, {"started_at": "a", "finished_at": "b"}, {}, {}, 2)
        with tempfile.TemporaryDirectory() as temp:
            repo = RunManifestRepository(str(Path(temp) / "manifests.sqlite3"))
            repo.save(manifest)
            self.assertEqual(repo.latest()["run_id"], "run-1")

    def test_redaction_hides_keys_and_known_secret_values(self):
        with patch.object(config, "RUN_TOKEN", "known-secret"):
            output = redact({"access_token": "abc", "message": "token=known-secret", "safe": "ok"})
        self.assertEqual(output["access_token"], "[REDACTED]")
        self.assertNotIn("known-secret", output["message"])
        self.assertEqual(output["safe"], "ok")

    def test_redaction_allows_safe_auth_status_fields(self):
        output = redact({
            "broker_auth_status": "auth_required",
            "broker_auth_message": "Device approval required",
            "degraded_auth_status": "rate_limited",
            "refresh_token": "secret-token",
        })
        self.assertEqual(output["broker_auth_status"], "auth_required")
        self.assertEqual(output["broker_auth_message"], "Device approval required")
        self.assertEqual(output["degraded_auth_status"], "rate_limited")
        self.assertEqual(output["refresh_token"], "[REDACTED]")

    def test_developer_snapshot_latest_uses_stored_report_and_excludes_raw_provider_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            report_repo = ReportSnapshotRepository(str(Path(temp) / "reports.sqlite3"))
            manifest_repo = RunManifestRepository(str(Path(temp) / "manifests.sqlite3"))
            report_repo.save_success("run-1", "dev", "payload", {"report_data": {
                "positions": [{"ticker": "NVDA"}], "news": {}, "recommendations": [],
                "tradier_snapshot": {"_provider_status": {"tradier": {"access_token": "secret"}}, "raw_provider_payload": {"secret": True}},
                "log": ["ok"],
            }}, {}, {})
            manifest_repo.save({"run_id": "run-1", "mode": "dev", "status": "complete", "report_quality": "SUCCESS_COMPLETE"})
            snapshot = build_developer_snapshot("latest", report_repo, manifest_repo)
            self.assertEqual(snapshot["source_run_id"], "run-1")
            self.assertNotIn("raw_provider_payload", snapshot)
            self.assertEqual(snapshot["provider_status"]["tradier"]["access_token"], "[REDACTED]")

    def test_snapshot_endpoint_requires_token_and_latest_does_not_run_pipeline(self):
        with tempfile.TemporaryDirectory() as temp:
            report_path = str(Path(temp) / "reports.sqlite3")
            with patch.object(config, "REPORT_SNAPSHOT_DB_PATH", report_path), patch.object(config, "RUN_MANIFEST_DB_PATH", report_path), patch.object(config, "RUN_TOKEN", "token"), patch.object(config, "ENABLE_DEV_SNAPSHOT_ENDPOINT", True):
                ReportSnapshotRepository().save_success("run-1", "dev", "payload", {"report_data": {"positions": [], "tradier_snapshot": {}, "log": []}}, {}, {})
                RunManifestRepository().save({"run_id": "run-1", "mode": "dev", "status": "complete", "report_quality": "SUCCESS_COMPLETE"})
                self.assertEqual(app.test_client().get("/api/dev/snapshot").status_code, 403)
                with patch("app.main.run") as pipeline:
                    response = app.test_client().get("/api/dev/snapshot?token=token&mode=latest")
                self.assertEqual(response.status_code, 200)
                pipeline.assert_not_called()

    def test_manifest_only_endpoint_is_compact(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "state.sqlite3")
            with patch.object(config, "REPORT_SNAPSHOT_DB_PATH", path), patch.object(config, "RUN_MANIFEST_DB_PATH", path), patch.object(config, "RUN_TOKEN", "token"), patch.object(config, "ENABLE_DEV_SNAPSHOT_ENDPOINT", True):
                RunManifestRepository().save({"run_id": "run-1", "mode": "dev", "status": "complete", "report_quality": "SUCCESS_COMPLETE"})
                data = app.test_client().get("/dev/snapshot?token=token&mode=manifest_only").get_json()
                self.assertEqual(data["run_manifest"]["run_id"], "run-1")
                self.assertNotIn("positions_summary", data)

    def test_report_snapshot_retention_is_bounded(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "REPORT_SNAPSHOT_RETENTION_LIMIT", 2):
            repo = ReportSnapshotRepository(str(Path(temp) / "reports.sqlite3"))
            for index in range(3):
                repo.save_success(f"run-{index}", "dev", "payload", {}, {}, {})
            with closing(sqlite3.connect(repo.db_path)) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM report_snapshots").fetchone()[0], 2)

    def test_active_trades_defaults_to_summary_and_full_is_explicit(self):
        open_options = {"summary": {"option_leg_count": 2}, "provider_status": {"robinhood": {"status": "ok"}}, "raw": "heavy"}
        lifecycle = {"summary": {"calendar_count": 1, "urgent_count": 0, "exit_review_count": 0}, "items": [{"ticker": "NVDA"}]}
        with patch.object(config, "RUN_TOKEN", "token"), patch.object(config, "ACTIVE_TRADES_DEFAULT_DETAIL", "summary"), \
             patch("app.services.open_options_service.detect_open_options_positions", return_value=open_options), \
             patch("app.services.calendar_lifecycle_service.evaluate_calendar_lifecycle", return_value=lifecycle):
            compact = app.test_client().get("/refresh-active-trades?token=token").get_json()
            full = app.test_client().get("/refresh-active-trades?token=token&detail=full").get_json()
        self.assertNotIn("open_options", compact)
        self.assertNotIn("lifecycle", compact)
        self.assertIn("open_options", full)
        self.assertIn("lifecycle", full)

    def test_health_does_not_call_pipeline(self):
        with patch("app.main.run") as pipeline:
            response = app.test_client().get("/health")
        self.assertEqual(response.status_code, 200)
        pipeline.assert_not_called()


if __name__ == "__main__":
    unittest.main()
