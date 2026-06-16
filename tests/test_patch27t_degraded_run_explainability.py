import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.services.app_diagnostics_service import build_latest_profiles
from app.services.data_freshness_service import build_data_freshness_summary
from app.services.developer_snapshot_service import build_developer_snapshot
from app.services.report_service import format_html
from app.services.report_snapshot_service import ReportSnapshotRepository
from app.services.run_manifest_repository import RunManifestRepository


NOW = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)


def _summary(quality="SUCCESS_COMPLETE"):
    return {
        "report_quality": quality,
        "report_data": {
            "positions": [{"ticker": "NVDA"}],
            "recommendations": [],
            "news": {},
            "tradier_snapshot": {
                "_pipeline_status": {"report_quality": quality},
                "_daily_opportunity_engine": {"actions": [{"ticker": "NVDA", "strategy_id": "stock_momentum"}]},
                "_strategy_results": {
                    "forward_factor_calendar": {
                        "strategy_id": "forward_factor_calendar",
                        "enabled": True,
                        "summary": {"dry_run": True},
                    }
                },
            },
            "log": [],
        },
    }


class Patch27TDegradedRunExplainabilityTests(unittest.TestCase):
    def test_complete_latest_run_uses_latest_snapshot(self):
        result = build_data_freshness_summary(
            {"run_id": "run-complete", "status": "complete", "completed_at": NOW.isoformat()},
            _summary(),
            {"run_id": "run-complete", "status": "complete", "report_quality": "SUCCESS_COMPLETE"},
            now=NOW,
        )

        self.assertEqual(result["dashboard_data_source"], "latest_complete_run")
        self.assertTrue(result["dashboard_using_latest_run"])
        self.assertFalse(result["canonical_snapshot_preserved"])
        self.assertEqual(result["canonical_snapshot_run_id"], "run-complete")
        self.assertEqual(result["latest_run_id"], "run-complete")

    def test_degraded_latest_run_preserves_canonical_complete_snapshot(self):
        result = build_data_freshness_summary(
            {"run_id": "run-complete", "status": "complete", "completed_at": NOW.isoformat()},
            _summary(),
            {
                "run_id": "run-degraded",
                "status": "degraded",
                "report_quality": "SUCCESS_DEGRADED",
                "degraded_reason": "broker unavailable",
            },
            now=NOW,
        )

        self.assertEqual(result["quality_label"], "LATEST_RUN_DEGRADED")
        self.assertEqual(result["dashboard_data_source"], "canonical_complete_snapshot_preserved")
        self.assertFalse(result["dashboard_using_latest_run"])
        self.assertTrue(result["dashboard_using_canonical_snapshot"])
        self.assertTrue(result["canonical_snapshot_preserved"])
        self.assertEqual(result["latest_run_id"], "run-degraded")
        self.assertEqual(result["latest_run_report_quality"], "SUCCESS_DEGRADED")
        self.assertEqual(result["canonical_snapshot_run_id"], "run-complete")
        self.assertEqual(result["canonical_snapshot_quality"], "SUCCESS_COMPLETE")
        self.assertEqual(result["latest_run_degraded_reason"], "broker unavailable")

    def test_missing_degraded_reason_is_unknown_not_inferred(self):
        result = build_data_freshness_summary(
            {"run_id": "run-complete", "status": "complete", "completed_at": NOW.isoformat()},
            _summary(),
            {"run_id": "run-degraded", "status": "degraded", "report_quality": "SUCCESS_DEGRADED"},
            now=NOW,
        )

        self.assertEqual(result["latest_run_degraded_reason"], "unknown")

    def test_cached_shell_renders_degraded_latest_and_canonical_distinction(self):
        summary = _summary()
        report = summary["report_data"]
        report["tradier_snapshot"]["_report_snapshot"] = {
            "freshness": {
                "quality_label": "LATEST_RUN_DEGRADED",
                "freshness_state": "FRESH",
                "report_age_seconds": 60,
                "canonical_snapshot_run_id": "run-complete",
                "canonical_snapshot_quality": "SUCCESS_COMPLETE",
                "latest_run_id": "run-degraded",
                "latest_run_report_quality": "SUCCESS_DEGRADED",
                "canonical_snapshot_preserved": True,
                "dashboard_data_source": "canonical_complete_snapshot_preserved",
                "latest_run_degraded_reason": "unknown",
                "warnings": ["Latest attempted run was degraded or failed; showing the latest usable complete report."],
            }
        }

        shell = format_html("payload", report["positions"], {}, [], report["tradier_snapshot"], [], view="shell")

        self.assertIn("Latest run run-degraded (SUCCESS_DEGRADED) degraded", shell)
        self.assertIn("showing canonical snapshot run-complete (SUCCESS_COMPLETE)", shell)
        self.assertIn("Reason: unknown", shell)

    def test_snapshot_diagnostics_remain_provider_free_and_expose_state(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "state.sqlite3")
            reports = ReportSnapshotRepository(path)
            manifests = RunManifestRepository(path)
            reports.save_success("run-complete", "dev", "payload", _summary(), {}, {})
            manifests.save({"run_id": "run-degraded", "status": "degraded", "report_quality": "SUCCESS_DEGRADED"})
            with patch("app.services.developer_snapshot_service.ReportSnapshotRepository", lambda: ReportSnapshotRepository(path)), \
                 patch("app.services.developer_snapshot_service.RunManifestRepository", lambda: RunManifestRepository(path)), \
                 patch("app.services.app_diagnostics_service.RunManifestRepository", lambda: RunManifestRepository(path)):
                snapshot = build_developer_snapshot("latest", reports, manifests)
                profiles = build_latest_profiles()

        self.assertFalse(snapshot["provider_calls_triggered"])
        self.assertTrue(snapshot["read_only"])
        self.assertFalse(profiles["provider_calls_triggered"])
        self.assertEqual(snapshot["data_freshness"]["dashboard_data_source"], "canonical_complete_snapshot_preserved")
        self.assertTrue(snapshot["data_freshness"]["canonical_snapshot_preserved"])


if __name__ == "__main__":
    unittest.main()
