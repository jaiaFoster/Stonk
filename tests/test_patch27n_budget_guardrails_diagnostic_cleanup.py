import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config
from app.main import app
from app.services.report_service import format_html
from app.services.report_snapshot_service import ReportSnapshotRepository
from app.services.usage_telemetry_service import UsageTelemetryRepository


HOT_SUMMARY_FIXTURE_CEILING_BYTES = 250_000
SNAPSHOT_SAVE_FIXTURE_CEILING_BYTES = 500_000


def _summary():
    rows = [
        {
            "ticker": f"T{index}",
            "verdict": "WATCH / REVIEW",
            "score": 70 + index,
            "diagnostics": {"rows": [{"payload": "x" * 1000} for _ in range(5)]},
        }
        for index in range(30)
    ]
    ff = {
        "strategy_id": "forward_factor_calendar",
        "enabled": True,
        "ran": True,
        "summary": {"dry_run": True, "candidate_selection_audit": rows},
        "rows": rows,
    }
    skew = {
        "strategy_id": "skew_momentum_vertical",
        "enabled": True,
        "ran": True,
        "summary": {"watch_count": len(rows)},
        "watch_items": rows,
        "items": rows,
        "rows": rows,
    }
    pipeline = {"report_quality": "SUCCESS_COMPLETE", "steps": rows}
    return {
        "strategy_results": {
            "forward_factor_calendar": ff,
            "skew_momentum_vertical": skew,
        },
        "pipeline_status": pipeline,
        "report_quality": "SUCCESS_COMPLETE",
        "report_data": {
            "positions": [{"ticker": "NVDA", "market_value": 1000}],
            "news": {},
            "recommendations": [],
            "tradier_snapshot": {
                "_strategy_results": {
                    "forward_factor_calendar": ff,
                    "skew_momentum_vertical": skew,
                },
                "_skew_momentum_vertical_strategy": skew,
                "_pipeline_status": pipeline,
                "_daily_opportunity_engine": {
                    "summary": {"action_count": len(rows)},
                    "actions": rows,
                },
            },
            "log": ["line"] * 50,
        },
    }


class Patch27NBudgetGuardrailsDiagnosticCleanupTests(unittest.TestCase):
    def test_representative_snapshot_stays_within_safe_budget_ceilings(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = ReportSnapshotRepository(str(Path(temp) / "state.sqlite3"))
            repo.save_success("run-1", "dev", "payload", _summary(), {}, {})
            snapshot = repo.latest_success(include_full=True)
            profile = repo.snapshot_profile(snapshot)

        stored_bytes = (
            profile["hot_summary_bytes"]
            + profile["compressed_full_summary_bytes"]
            + profile["compressed_full_payload_bytes"]
            + profile["compressed_raw_provider_bytes"]
        )
        self.assertLess(profile["hot_summary_bytes"], HOT_SUMMARY_FIXTURE_CEILING_BYTES)
        self.assertLess(stored_bytes, SNAPSHOT_SAVE_FIXTURE_CEILING_BYTES)

    def test_cached_shell_preserves_required_operational_facts(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = ReportSnapshotRepository(str(Path(temp) / "state.sqlite3"))
            repo.save_success("run-1", "dev", "payload", _summary(), {}, {})
            snapshot = repo.latest_success(include_full=True)
            report = repo.load_summary(snapshot, full=True)["report_data"]

        html = format_html(
            "payload",
            report["positions"],
            report["news"],
            report["recommendations"],
            report["tradier_snapshot"],
            report["log"],
            view="shell",
        )
        for required_text in (
            "Daily Opportunity",
            "FF DRY",
            "SUCCESS_COMPLETE",
            "Urgent Risk Review",
            "Open Full Report",
            "Heavy detail stays dormant until requested.",
        ):
            self.assertIn(required_text, html)

    def test_full_and_raw_compatibility_paths_remain_explicit_and_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "state.sqlite3")
            repo = ReportSnapshotRepository(path)
            repo.save_success("run-1", "dev", "payload", _summary(), {}, {})
            full = repo.load_summary(repo.latest_success(include_full=True), full=True)
            with patch.object(config, "REPORT_SNAPSHOT_DB_PATH", path), \
                 patch.object(config, "RUN_TOKEN", "token"), \
                 patch.object(config, "ENABLE_DEV_SNAPSHOT_ENDPOINT", True):
                response = app.test_client().get("/api/dev/snapshot/detail/provider_raw?token=token")

        self.assertIn("strategy_results", full)
        self.assertIn("pipeline_status", full)
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["raw_provider_payload"])
        self.assertTrue(body["read_only"])
        self.assertFalse(body["provider_calls_triggered"])

    def test_size_budget_labels_dormant_raw_archive_and_operational_compact_state(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = UsageTelemetryRepository(str(Path(temp) / "state.sqlite3"))
            repository.record_size_profile(
                "run-1",
                mode="dev",
                status="complete",
                snapshot_sizes={
                    "raw_provider_snapshot_bytes": 2_800_000,
                    "compact_tradier_snapshot_bytes": 400_000,
                    "hot_summary_bytes": 99_000,
                    "full_summary_bytes": 445_000,
                },
                section_sizes={
                    "tradier_snapshot": 2_800_000,
                    "tradier_snapshot_compact": 399_000,
                    "report_snapshot_save": 285_000,
                },
            )
            report = repository.summary()["size_budget_report"]

        rows = {
            row["name"]: row
            for category in report["categories"].values()
            for row in category
        }
        self.assertEqual(rows["snapshot:raw_provider_snapshot_bytes"]["policy"], "intentional_dormant")
        self.assertFalse(rows["snapshot:raw_provider_snapshot_bytes"]["action_required"])
        self.assertEqual(rows["snapshot:compact_tradier_snapshot_bytes"]["policy"], "compact_operational")
        self.assertTrue(rows["snapshot:compact_tradier_snapshot_bytes"]["action_required"])
        self.assertEqual(rows["snapshot:hot_summary_bytes"]["severity"], "ok")
        self.assertEqual(rows["snapshot:full_summary_bytes"]["severity"], "warning")
        self.assertEqual(rows["section:report_snapshot_save"]["severity"], "warning")
        self.assertIn("intentional_dormant", report["policy_notes"])


if __name__ == "__main__":
    unittest.main()
