import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config
from app.services.usage_telemetry_service import UsageTelemetryRepository


class Patch27JTelemetrySizeBudgetTests(unittest.TestCase):
    def test_size_budget_report_flags_and_categorizes_large_sections(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "state.sqlite3")
            with patch.object(config, "USAGE_TELEMETRY_DB_PATH", path):
                repository = UsageTelemetryRepository()
                repository.record_size_profile(
                    "run-1",
                    mode="dev",
                    status="complete",
                    snapshot_sizes={
                        "hot_summary_bytes": 300_000,
                        "full_summary_bytes": 700_000,
                        "raw_provider_snapshot_bytes": 1_500_000,
                    },
                    section_sizes={
                        "tradier_snapshot": 2_700_000,
                        "_calendar_opportunity_cache": 1_200_000,
                        "daily_opportunity": 10_000,
                    },
                )
                summary = repository.summary()
        report = summary["size_budget_report"]
        self.assertTrue(summary["baseline_ready"])
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["flags"][0]["severity"], "critical")
        self.assertEqual(report["categories"]["hot_summary"][0]["severity"], "warning")
        self.assertEqual(report["categories"]["full_compact_summary"][0]["severity"], "large")
        self.assertTrue(report["categories"]["raw_provider_archive"])
        self.assertTrue(report["categories"]["strategy_cache_output"])

    def test_missing_baseline_is_explicit_and_non_blocking(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(config, "USAGE_TELEMETRY_DB_PATH", str(Path(temp) / "state.sqlite3")):
                summary = UsageTelemetryRepository().summary()
        self.assertFalse(summary["baseline_ready"])
        self.assertEqual(summary["size_budget_report"]["status"], "awaiting_successful_snapshot")

    def test_usage_breakdown_separates_modes_details_exports_and_compatibility(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(config, "USAGE_TELEMETRY_DB_PATH", str(Path(temp) / "state.sqlite3")):
                repository = UsageTelemetryRepository()
                repository.record_event("snapshot_request", metadata={"request_mode": "latest"})
                repository.record_event("snapshot_request", metadata={"request_mode": "full"})
                repository.record_event("dashboard_load", metadata={"dashboard_view": "shell"})
                repository.record_event("detail_request", section="provider_raw", metadata={"detail_section": "provider_raw"})
                repository.record_event("copy_export", section="exports", metadata={"export_key": "dailyBrief"})
                repository.record_event("download_export", section="exports", metadata={"export_key": "fullDebugPayload"})
                breakdown = repository.summary()["usage_breakdown"]
        self.assertEqual(breakdown["snapshot_modes"], {"latest": 1, "full": 1})
        self.assertEqual(breakdown["dashboard_views"], {"shell": 1})
        self.assertEqual(breakdown["detail_sections"], {"provider_raw": 1})
        self.assertEqual(breakdown["compatibility_requests"], {"full_snapshot": 1, "provider_raw_detail": 1})
        self.assertEqual(breakdown["export_actions"], {"dailyBrief": 1, "fullDebugPayload": 1})

    def test_budget_thresholds_are_diagnostics_only(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(config, "USAGE_TELEMETRY_DB_PATH", str(Path(temp) / "state.sqlite3")), \
                 patch.object(config, "USAGE_TELEMETRY_SIZE_WARNING_BYTES", 10), \
                 patch.object(config, "USAGE_TELEMETRY_SIZE_LARGE_BYTES", 20), \
                 patch.object(config, "USAGE_TELEMETRY_SIZE_CRITICAL_BYTES", 30):
                repository = UsageTelemetryRepository()
                self.assertTrue(repository.record_size_profile(
                    "run-1", mode="dev", status="complete",
                    snapshot_sizes={"hot_summary_bytes": 31}, section_sizes={},
                ))
                report = repository.summary()["size_budget_report"]
        self.assertEqual(report["flags"][0]["severity"], "critical")
        self.assertEqual(report["status"], "ready")


if __name__ == "__main__":
    unittest.main()
