import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config
from app.services.analysis_service import _latest_complete_broker_state
from app.services.broker_position_snapshot_service import BrokerPositionSnapshotRepository, apply_broker_position_fallback
from app.services.report_snapshot_service import ReportSnapshotRepository
from app.services.report_service import format_html


class BrokerSnapshotResilienceTests(unittest.TestCase):
    def test_failed_fetch_uses_last_good_positions(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = BrokerPositionSnapshotRepository(str(Path(temp) / "broker.sqlite3"))
            good = {"account_results": [{"account_id": "1", "account_name": "Roth", "status": "SUCCESS", "positions": [{"ticker": "NVDA", "quantity": 2}]}]}
            apply_broker_position_fallback(good, repo)
            failed = {"account_results": [{"account_id": "1", "account_name": "Roth", "status": "FAILED", "positions": None, "error": "503"}]}
            result = apply_broker_position_fallback(failed, repo)
            self.assertEqual(result["positions"][0]["ticker"], "NVDA")
            self.assertEqual(result["positions"][0]["broker_data_state"], "STALE_FALLBACK")
            self.assertEqual(result["report_quality"], "SUCCESS_DEGRADED")

    def test_successful_empty_replaces_prior_positions(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = BrokerPositionSnapshotRepository(str(Path(temp) / "broker.sqlite3"))
            apply_broker_position_fallback({"account_results": [{"account_id": "1", "account_name": "Roth", "status": "SUCCESS", "positions": [{"ticker": "NVDA"}]}]}, repo)
            result = apply_broker_position_fallback({"account_results": [{"account_id": "1", "account_name": "Roth", "status": "SUCCESS_EMPTY", "positions": []}]}, repo)
            self.assertEqual(result["positions"], [])
            self.assertEqual(repo.latest_account("robinhood", "1")["positions"], [])
            self.assertEqual(result["report_quality"], "SUCCESS_COMPLETE")

    def test_failed_fetch_without_snapshot_is_unknown_not_empty_success(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = BrokerPositionSnapshotRepository(str(Path(temp) / "broker.sqlite3"))
            result = apply_broker_position_fallback({"account_results": [{"account_id": "1", "account_name": "Roth", "status": "FAILED", "positions": None}]}, repo)
            self.assertEqual(result["account_summary"]["unavailable"], 1)
            self.assertEqual(result["report_quality"], "SUCCESS_DEGRADED")

    def test_partial_account_failure_preserves_current_and_cached_positions(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = BrokerPositionSnapshotRepository(str(Path(temp) / "broker.sqlite3"))
            apply_broker_position_fallback({"account_results": [{"account_id": "2", "account_name": "IRA", "status": "SUCCESS", "positions": [{"ticker": "AMZN"}]}]}, repo)
            result = apply_broker_position_fallback({"account_results": [
                {"account_id": "1", "account_name": "Roth", "status": "SUCCESS", "positions": [{"ticker": "NVDA"}]},
                {"account_id": "2", "account_name": "IRA", "status": "FAILED", "positions": None},
            ]}, repo)
            self.assertEqual({row["ticker"] for row in result["positions"]}, {"NVDA", "AMZN"})
            self.assertEqual(result["account_summary"]["stale_fallback"], 1)

    def test_degraded_report_does_not_replace_complete_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = ReportSnapshotRepository(str(Path(temp) / "reports.sqlite3"))
            repo.save_success("complete", "dev", "complete payload", {}, {}, {})
            repo.save_degraded("degraded", "dev", "degraded payload", {}, {}, {})
            self.assertEqual(repo.latest_success()["run_id"], "complete")
            self.assertEqual(repo.latest_degraded()["run_id"], "degraded")

    def test_latest_complete_report_supplies_stale_active_trade_state(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "reports.sqlite3")
            repo = ReportSnapshotRepository(path)
            repo.save_success("complete", "dev", "payload", {"report_data": {"tradier_snapshot": {
                "_open_options_positions": {"calendars": [{"ticker": "NVDA"}]},
                "_calendar_lifecycle_checks": {"items": [{"ticker": "NVDA"}]},
            }}}, {}, {})
            with patch.object(config, "REPORT_SNAPSHOT_DB_PATH", path):
                state = _latest_complete_broker_state(lambda message: None)
            self.assertEqual(state["open_options"]["calendars"][0]["ticker"], "NVDA")

    def test_degraded_warning_renders(self):
        html = format_html("payload", [{"ticker": "NVDA", "quantity": 1, "market_value": 100}], {}, [], {
            "_pipeline_status": {"mode": "dev", "steps": [], "report_quality": "SUCCESS_DEGRADED"},
            "_provider_status": {"robinhood": {"status": "positions_failed", "success": False}},
        }, [])
        self.assertIn("Refresh completed with warnings", html)
        self.assertIn("POSITIONS STALE", html)


if __name__ == "__main__":
    unittest.main()
