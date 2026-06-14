import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config
from app.main import app
from app.services.app_diagnostics_service import build_latest_profiles
from app.services.developer_snapshot_service import build_developer_snapshot
from app.services.report_service import format_html
from app.services.report_snapshot_service import ReportSnapshotRepository
from app.services.run_manifest_repository import RunManifestRepository


def _full_summary():
    heavy_rows = [{"ticker": f"T{index}", "payload": "x" * 500} for index in range(100)]
    return {
        "report_data": {
            "positions": [{"ticker": "NVDA", "market_value": 1000}],
            "news": {"NVDA": heavy_rows},
            "recommendations": [
                {
                    "ticker": "NVDA",
                    "action": "WATCH / REVIEW",
                    "risks": ["Market metrics were not evaluated in this dev run. Reason: skipped by dev data cap."],
                },
                {"ticker": "SOFI", "action": "REDUCE RISK", "risks": ["Concentration risk"]},
                {
                    "ticker": "MIXED",
                    "action": "WATCH / REVIEW",
                    "risks": ["Concentration risk", "Data incomplete due to dev cap."],
                },
            ],
            "tradier_snapshot": {
                "_daily_opportunity_engine": {"actions": [{"ticker": "CRDO", "action": "CONSIDER ADDING", "priority_score": 90}]},
                "_pipeline_status": {"mode": "dev", "steps": []},
                "_provider_status": {"robinhood": {"status": "ok", "success": True}},
                "_runtime_profile": {"total_ms": 10},
                "_payload_size_profile": {"sections_bytes": {"tradier_snapshot": 999}},
                "_storage_profile": {"database_size_bytes": 100},
                "_strategy_results": {
                    "forward_factor_calendar": {
                        "strategy_id": "forward_factor_calendar",
                        "enabled": True,
                        "pass_count": 0,
                        "watch_count": 1,
                        "fail_count": 2,
                        "rows": heavy_rows,
                    }
                },
                "raw_provider_payload": {"has_data": False, "rows": heavy_rows},
            },
            "log": ["line"] * 100,
        }
    }


class Patch27DSnapshotSlimmingTests(unittest.TestCase):
    def test_hot_summary_is_small_and_full_detail_is_compressed_on_demand(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = ReportSnapshotRepository(str(Path(temp) / "reports.sqlite3"))
            repo.save_success("run-1", "dev", "payload " + ("y" * 10_000), _full_summary(), {}, {})
            hot = repo.latest_success()
            full = repo.latest_success(include_full=True)
            hot_summary = repo.load_summary(hot)
            full_summary = repo.load_summary(full, full=True)
            profile = repo.snapshot_profile(hot)

        self.assertNotIn("full_summary_blob", hot)
        self.assertLess(profile["hot_summary_bytes"], profile["full_summary_bytes"])
        self.assertLess(profile["compressed_full_summary_bytes"], profile["full_summary_bytes"])
        self.assertNotIn("raw_provider_payload", hot_summary["report_data"]["tradier_snapshot"])
        self.assertIn("raw_provider_payload", full_summary["report_data"]["tradier_snapshot"])
        self.assertEqual(repo.load_payload(full, full=True), "payload " + ("y" * 10_000))

    def test_old_uncompressed_snapshot_remains_readable(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "reports.sqlite3")
            repo = ReportSnapshotRepository(path)
            with sqlite3.connect(path) as conn:
                conn.execute(
                    """INSERT INTO report_snapshots
                       (run_id,mode,status,started_at,completed_at,payload_json,summary_json,data_coverage_json,
                        provider_status_json,schema_version,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    ("old", "dev", "complete", "a", "b", json.dumps("old payload"), json.dumps(_full_summary()), "{}", "{}", 1, "b"),
                )
            old = repo.latest_success(include_full=True)
        self.assertEqual(repo.load_payload(old, full=True), "old payload")
        self.assertEqual(repo.load_summary(old, full=True)["report_data"]["positions"][0]["ticker"], "NVDA")

    def test_latest_profiles_uses_hot_snapshot_and_full_developer_snapshot_keeps_detail(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "state.sqlite3")
            repo = ReportSnapshotRepository(path)
            manifests = RunManifestRepository(path)
            repo.save_success("run-1", "dev", "payload", _full_summary(), {}, {})
            manifests.save({"run_id": "run-1", "status": "complete"})
            with patch.object(config, "REPORT_SNAPSHOT_DB_PATH", path), patch.object(config, "RUN_MANIFEST_DB_PATH", path):
                profiles = build_latest_profiles()
                latest = build_developer_snapshot("latest")
                full = build_developer_snapshot("full")
        self.assertTrue(profiles["report_snapshot_profile"]["compression_enabled"])
        self.assertNotIn("rows", latest["strategy_summaries"]["forward_factor_calendar"])
        self.assertIn("rows", full["strategy_summaries"]["forward_factor_calendar"])

    def test_shell_excludes_missing_metric_watch_rows_from_urgent_risk(self):
        summary = _full_summary()
        report = summary["report_data"]
        shell = format_html(
            "payload", report["positions"], {}, report["recommendations"],
            report["tradier_snapshot"], [], view="shell",
        )
        risk = shell[shell.index('id="risk-review"'):shell.index('id="strategy-summary"')]
        self.assertIn("SOFI", risk)
        self.assertIn("MIXED", risk)
        self.assertNotIn("NVDA", risk)
        self.assertIn("Metrics unavailable for 1 holding(s)", risk)
        self.assertIn("COUNT<strong>2</strong>", risk)

    def test_cached_shell_and_full_routes_use_compatible_snapshot_views(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "state.sqlite3")
            with patch.object(config, "REPORT_SNAPSHOT_DB_PATH", path), patch.object(config, "RUN_TOKEN", "token"):
                ReportSnapshotRepository().save_success("run-1", "dev", "payload", _full_summary(), {}, {})
                client = app.test_client()
                shell = client.get("/?token=token")
                full = client.get("/?token=token&view=full")
        self.assertEqual(shell.status_code, 200)
        self.assertEqual(full.status_code, 200)
        self.assertIn(b'data-dashboard-view="shell"', shell.data)
        self.assertIn(b'data-dashboard-view="full"', full.data)
        self.assertLess(len(shell.data), len(full.data))


if __name__ == "__main__":
    unittest.main()
