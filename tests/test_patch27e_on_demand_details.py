import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config
from app.main import app
from app.services.developer_snapshot_service import build_developer_snapshot, build_snapshot_detail
from app.services.report_snapshot_service import ReportSnapshotRepository
from app.services.run_manifest_repository import RunManifestRepository


def _summary():
    heavy_rows = [{"ticker": f"T{index}", "detail": "x" * 1000} for index in range(100)]
    strategy = {
        "strategy_id": "forward_factor_calendar",
        "enabled": True,
        "ran": True,
        "pass_count": 0,
        "watch_count": 1,
        "fail_count": 2,
        "skipped_count": 3,
        "rows": heavy_rows,
        "summary": {"dry_run": True},
    }
    return {
        "strategy_results": {"forward_factor_calendar": strategy},
        "pipeline_status": {"steps": heavy_rows, "report_quality": "SUCCESS_COMPLETE"},
        "report_quality": "SUCCESS_COMPLETE",
        "report_data": {
            "positions": [{"ticker": "NVDA", "market_value": 1000}],
            "recommendations": [],
            "news": {"NVDA": heavy_rows},
            "tradier_snapshot": {
                "_strategy_results": {"forward_factor_calendar": strategy},
                "_pipeline_status": {"steps": heavy_rows, "report_quality": "SUCCESS_COMPLETE"},
                "_provider_status": {"tradier": {"status": "ok"}},
                "_daily_opportunity_engine": {"summary": {"count": 1}, "actions": [{"ticker": "NVDA", "strategy_id": "stock_momentum"}]},
                "_unified_calendar_trade_engine": {"summary": {"pass_count": 0}, "new_trade_rows": heavy_rows},
                "_stock_momentum_strategy": {"summary": {"pass_count": 1}, "items": heavy_rows},
                "_portfolio_gap": {"summary": {"suggestion_count": 1}, "suggestions": heavy_rows},
            },
            "log": ["line"] * 100,
        },
    }


class Patch27EOnDemandDetailsTests(unittest.TestCase):
    def test_hot_summary_deduplicates_top_level_and_detailed_strategy_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = ReportSnapshotRepository(str(Path(temp) / "state.sqlite3"))
            repo.save_success("run-1", "dev", "payload", _summary(), {}, {})
            hot = repo.load_summary(repo.latest_success())
            full = repo.load_summary(repo.latest_success(include_full=True), full=True)
            profile = repo.snapshot_profile(repo.latest_success())

        self.assertNotIn("pipeline_status", hot)
        self.assertNotIn("strategy_results", hot)
        self.assertNotIn("rows", hot["report_data"]["tradier_snapshot"]["_strategy_results"]["forward_factor_calendar"])
        self.assertIn("rows", full["strategy_results"]["forward_factor_calendar"])
        self.assertGreater(profile["hot_summary_bytes"], 0)
        self.assertGreater(profile["full_summary_bytes"], 0)
        self.assertNotEqual(profile["hot_summary_bytes"], profile["full_summary_bytes"])

    def test_snapshot_responses_explicitly_confirm_read_only_provider_free_behavior(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "state.sqlite3")
            reports = ReportSnapshotRepository(path)
            manifests = RunManifestRepository(path)
            reports.save_success("run-1", "dev", "payload", _summary(), {}, {})
            manifests.save({"run_id": "run-1", "status": "complete"})
            for mode in ("manifest_only", "latest", "summary", "full"):
                snapshot = build_developer_snapshot(mode, reports, manifests)
                self.assertFalse(snapshot["provider_calls_triggered"])
                self.assertTrue(snapshot["read_only"])

    def test_detail_builder_loads_only_requested_full_section(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = ReportSnapshotRepository(str(Path(temp) / "state.sqlite3"))
            repo.save_success("run-1", "dev", "payload", _summary(), {}, {})
            strategy = build_snapshot_detail("strategy", strategy_id="forward_factor_calendar", report_repository=repo)
            providers = build_snapshot_detail("providers", report_repository=repo)

        self.assertIn("rows", strategy["detail"])
        self.assertNotIn("positions", strategy)
        self.assertEqual(providers["detail"]["tradier"]["status"], "ok")
        self.assertFalse(strategy["provider_calls_triggered"])
        self.assertTrue(strategy["read_only"])

    def test_detail_endpoint_is_explicit_token_protected_and_does_not_run_pipeline(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "state.sqlite3")
            with patch.object(config, "REPORT_SNAPSHOT_DB_PATH", path), \
                 patch.object(config, "RUN_TOKEN", "token"), \
                 patch.object(config, "ENABLE_DEV_SNAPSHOT_ENDPOINT", True):
                ReportSnapshotRepository().save_success("run-1", "dev", "payload", _summary(), {}, {})
                client = app.test_client()
                self.assertEqual(client.get("/api/dev/snapshot/detail/providers").status_code, 403)
                with patch("app.main.run") as pipeline:
                    response = client.get("/api/dev/snapshot/detail/strategy?token=token&strategy_id=forward_factor_calendar")
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.get_json()["provider_calls_triggered"])
                pipeline.assert_not_called()

    def test_unknown_detail_section_is_rejected_without_provider_calls(self):
        with patch.object(config, "RUN_TOKEN", "token"), patch.object(config, "ENABLE_DEV_SNAPSHOT_ENDPOINT", True):
            response = app.test_client().get("/api/dev/snapshot/detail/raw_everything?token=token")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()["provider_calls_triggered"])


if __name__ == "__main__":
    unittest.main()
