import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services.data_freshness_service import build_data_freshness_summary
from app.services.developer_snapshot_service import build_developer_snapshot
from app.services.report_service import format_html
from app.services.report_snapshot_service import ReportSnapshotRepository
from app.services.run_manifest_repository import RunManifestRepository


NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


def _summary(quality="SUCCESS_COMPLETE", positions=None):
    return {
        "report_quality": quality,
        "report_data": {
            "positions": positions or [{"ticker": "NVDA", "market_value": 1000}],
            "recommendations": [],
            "news": {},
            "tradier_snapshot": {
                "_pipeline_status": {"report_quality": quality},
                "_provider_status": {"robinhood": {"status": "ok", "positions_available": True}},
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


class Patch27QDataFreshnessRunQualityGuardrailsTests(unittest.TestCase):
    def test_complete_recent_report_is_fresh(self):
        completed = (NOW - timedelta(minutes=10)).isoformat()
        result = build_data_freshness_summary(
            {"run_id": "run-1", "status": "complete", "completed_at": completed},
            _summary(),
            {"run_id": "run-1", "status": "complete", "report_quality": "SUCCESS_COMPLETE", "has_market_data": True, "has_options_data": True},
            now=NOW,
        )
        self.assertEqual(result["quality_label"], "SUCCESS_COMPLETE")
        self.assertEqual(result["freshness_state"], "FRESH")
        self.assertEqual(result["broker_data"]["state"], "CURRENT")
        self.assertFalse(result["provider_calls_triggered"])
        self.assertTrue(result["read_only"])

    def test_stale_cached_report_is_labeled_honestly(self):
        completed = (NOW - timedelta(days=2)).isoformat()
        result = build_data_freshness_summary(
            {"run_id": "run-1", "status": "complete", "completed_at": completed},
            _summary(),
            {"run_id": "run-1", "status": "complete", "report_quality": "SUCCESS_COMPLETE"},
            now=NOW,
        )
        self.assertEqual(result["quality_label"], "STALE_CACHED_REPORT")
        self.assertEqual(result["freshness_state"], "STALE")
        self.assertTrue(result["warnings"])

    def test_latest_degraded_attempt_is_distinct_from_cached_complete_report(self):
        result = build_data_freshness_summary(
            {"run_id": "complete-run", "status": "complete", "completed_at": (NOW - timedelta(hours=1)).isoformat()},
            _summary(),
            {"run_id": "degraded-run", "status": "complete", "report_quality": "SUCCESS_DEGRADED", "has_broker_data": False},
            now=NOW,
        )
        self.assertEqual(result["quality_label"], "LATEST_RUN_DEGRADED")
        self.assertEqual(result["canonical_run_id"], "complete-run")
        self.assertEqual(result["latest_run_id"], "degraded-run")
        self.assertIn("broker position data is unavailable", " ".join(result["warnings"]).lower())

    def test_broker_stale_fallback_and_missing_data_are_explicit(self):
        positions = [{"ticker": "NVDA", "broker_data_state": "STALE_FALLBACK", "broker_snapshot_fetched_at": (NOW - timedelta(hours=8)).isoformat()}]
        result = build_data_freshness_summary(
            {"run_id": "run-1", "status": "complete", "completed_at": NOW.isoformat()},
            _summary(positions=positions),
            {"run_id": "run-1", "status": "complete", "has_market_data": False, "has_options_data": False},
            now=NOW,
        )
        self.assertEqual(result["broker_data"]["state"], "STALE_FALLBACK")
        self.assertEqual(result["market_data"]["state"], "UNAVAILABLE")
        self.assertEqual(result["options_data"]["state"], "UNAVAILABLE")

    def test_cached_shell_renders_run_quality_and_freshness(self):
        summary = _summary()
        report = summary["report_data"]
        report["tradier_snapshot"]["_report_snapshot"] = {
            "freshness": {
                "quality_label": "STALE_CACHED_REPORT",
                "freshness_state": "STALE",
                "report_age_seconds": 90000,
                "canonical_run_id": "run-1",
                "warnings": ["Cached report exceeds the configured stale-age threshold."],
            }
        }
        shell = format_html("payload", report["positions"], {}, [], report["tradier_snapshot"], [], view="shell")
        self.assertIn("Data status: STALE_CACHED_REPORT", shell)
        self.assertIn("STALE", shell)
        self.assertIn("run-1", shell)

    def test_snapshot_exposes_provider_free_freshness_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "state.sqlite3")
            reports = ReportSnapshotRepository(path)
            manifests = RunManifestRepository(path)
            reports.save_success("run-1", "dev", "payload", _summary(), {}, {})
            manifests.save({"run_id": "run-1", "status": "complete", "report_quality": "SUCCESS_COMPLETE"})
            snapshot = build_developer_snapshot("latest", reports, manifests)
        self.assertIn("data_freshness", snapshot)
        self.assertTrue(snapshot["data_freshness"]["read_only"])
        self.assertFalse(snapshot["data_freshness"]["provider_calls_triggered"])


if __name__ == "__main__":
    unittest.main()
