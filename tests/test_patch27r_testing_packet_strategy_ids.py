import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config
from app.main import app
from app.services.developer_snapshot_service import build_developer_snapshot, build_snapshot_detail
from app.services.report_snapshot_service import ReportSnapshotRepository
from app.services.run_manifest_repository import RunManifestRepository
from app.services.testing_packet_service import build_testing_packet


def _summary():
    ff_rows = [
        {"ticker": "SPY", "verdict": "WATCH / RESEARCH", "score": 70, "detail": "x" * 5000},
        {"ticker": "QQQ", "verdict": "FAIL / BLOCKED", "score": 20, "detail": "x" * 5000},
    ]
    return {"report_data": {
        "positions": [{"ticker": "NVDA", "market_value": 1000, "account": "secret-account"}],
        "recommendations": [],
        "tradier_snapshot": {
            "_strategy_results": {
                "forward_factor_calendar": {
                    "strategy_id": "forward_factor_calendar", "strategy_label": "Forward Factor Calendar",
                    "enabled": True, "ran": True, "pass_count": 0, "watch_count": 1, "fail_count": 1,
                    "skipped_count": 0, "rows": ff_rows, "summary": {"dry_run": True},
                },
            },
            "_daily_opportunity_engine": {"summary": {"action_count": 1}, "actions": [{"ticker": "NVDA", "strategy_id": "stock_momentum"}]},
            "_calendar_lifecycle_checks": {"summary": {"calendar_count": 1}, "calendars": [{"ticker": "NVDA"}]},
            "_runtime_profile": {"total_ms": 20},
            "_payload_size_profile": {"sections_bytes": {"summary": 100}},
            "_storage_profile": {"database_size_bytes": 200},
            "_provider_status": {"tradier": {"status": "ok", "access_token": "secret"}},
            "_data_coverage": {"counters": {"provider_fetches": 2}},
        },
        "log": ["secret log"],
    }}


class Patch27RTestingPacketStrategyIdsTests(unittest.TestCase):
    def _state(self, path):
        ReportSnapshotRepository(path).save_success("run-27r", "dev", "payload", _summary(), {}, {})
        RunManifestRepository(path).save({"run_id": "run-27r", "status": "complete", "report_quality": "SUCCESS_COMPLETE"})

    def test_summary_exposes_valid_strategy_ids_and_aliases(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "state.sqlite3")
            self._state(path)
            snapshot = build_developer_snapshot("summary", ReportSnapshotRepository(path), RunManifestRepository(path))
        ids = {row["strategy_id"]: row for row in snapshot["strategy_ids"]}
        self.assertEqual(set(ids), {"earnings_calendar", "skew_momentum_vertical", "forward_factor_calendar", "stock_momentum"})
        self.assertIn("forward_factor", ids["forward_factor_calendar"]["aliases"])
        self.assertTrue(ids["forward_factor_calendar"]["dry_run"])

    def test_wrong_strategy_id_returns_valid_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "state.sqlite3")
            self._state(path)
            result = build_snapshot_detail("strategy", strategy_id="forward_factor", report_repository=ReportSnapshotRepository(path))
        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["error"], "Unknown strategy_id.")
        self.assertIn("forward_factor_calendar", result["valid_strategy_ids"])
        self.assertFalse(result["provider_calls_triggered"])

    def test_testing_packet_is_compact_redacted_and_provider_free(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "state.sqlite3")
            with patch.object(config, "REPORT_SNAPSHOT_DB_PATH", path), patch.object(config, "RUN_MANIFEST_DB_PATH", path):
                self._state(path)
                packet = build_testing_packet()
        self.assertTrue(packet["forward_factor_dry_run_excluded"])
        self.assertFalse(packet["trade_execution_enabled"])
        self.assertFalse(packet["provider_calls_triggered"])
        self.assertNotIn("positions_summary", packet)
        self.assertNotIn("logs", packet)
        self.assertNotIn("detail", str(packet["strategy_results"]))
        self.assertEqual(packet["provider_caveats"]["provider_status"]["tradier"]["access_token"], "[REDACTED]")

    def test_routes_require_dev_token_and_never_run_pipeline(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "state.sqlite3")
            with patch.object(config, "REPORT_SNAPSHOT_DB_PATH", path), patch.object(config, "RUN_MANIFEST_DB_PATH", path), \
                 patch.object(config, "DEV_API_TOKEN", "dev-token"), patch.object(config, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True):
                self._state(path)
                client = app.test_client()
                for route in ("/api/dev/strategy-ids", "/api/dev/testing-packet"):
                    self.assertEqual(client.get(route).status_code, 403)
                    with patch("app.main.run") as pipeline:
                        response = client.get(f"{route}?token=dev-token")
                    self.assertEqual(response.status_code, 200)
                    self.assertFalse(response.get_json()["provider_calls_triggered"])
                    pipeline.assert_not_called()


if __name__ == "__main__":
    unittest.main()
