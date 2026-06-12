import tempfile
import unittest
from pathlib import Path

from app.services.report_service import format_html
from app.services.run_data_context_service import create_run_data_context
from app.services.skew_momentum_vertical_service import build_skew_momentum_vertical_strategy
from app.services.strategy_opportunity_repository import StrategyOpportunityRepository
from app.services.generic_option_lifecycle_service import build_lifecycle_envelope, classify_broker_option_structure
from app.services.actionability_service import attach_actionability
from app.services.strategy_execution_service import execute_strategy_registry
from app.services.staged_scan_service import StagedScan
from app.strategies.registry import collect_requirements, normalize_strategy_results


class StrategyRegistryFoundationTests(unittest.TestCase):
    def test_registry_declares_current_strategy_requirements(self):
        context = create_run_data_context("dev")
        context.analysis_tickers = ["NVDA"]
        requirements = collect_requirements(context)
        ids = {item.strategy_id for item in requirements}
        self.assertIn("earnings_calendar", ids)
        self.assertIn("skew_momentum_vertical", ids)
        self.assertIn("stock_momentum", ids)

    def test_registry_normalizes_outer_result_and_isolates_shapes(self):
        context = create_run_data_context()
        context.analysis_tickers = ["NVDA"]
        results = normalize_strategy_results(context, {
            "skew_momentum_vertical": {"items": [{"ticker": "NVDA", "verdict": "WATCH / SKEW NOT RICH ENOUGH"}]},
        })
        self.assertEqual(results["skew_momentum_vertical"]["watch_count"], 1)
        self.assertIn("earnings_calendar", results)

    def test_generic_opportunity_registry_upserts_seen_count(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = StrategyOpportunityRepository(str(Path(temp) / "opps.sqlite3"))
            result = {"skew_momentum_vertical": {"version": "v1", "rows": [{"ticker": "NVDA", "verdict": "WATCH", "score": 80}]}}
            repo.upsert_results(result)
            repo.upsert_results(result)
            self.assertEqual(repo.recent()[0]["seen_count"], 2)

    def test_generic_opportunity_registry_filters_by_strategy(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = StrategyOpportunityRepository(str(Path(temp) / "opps.sqlite3"))
            repo.upsert_results({
                "earnings_calendar": {"rows": [{"ticker": "ADBE", "verdict": "FAIL"}]},
                "skew_momentum_vertical": {"rows": [{"ticker": "NVDA", "verdict": "WATCH"}]},
            })
            rows = repo.recent(strategy_id="earnings_calendar")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["strategy_id"], "earnings_calendar")

    def test_strategy_two_missing_momentum_is_data_unavailable_not_weak_signal(self):
        result = build_skew_momentum_vertical_strategy(
            positions=[{"ticker": "NVDA"}], watchlist_candidates={}, portfolio_gap_analysis={},
            market_metrics={"NVDA": {"has_data": False}}, provider=type("P", (), {"is_configured": True})(),
        )
        self.assertIn("DATA", result["items"][0]["verdict"])
        self.assertNotIn("MOMENTUM NOT CONFIRMED", result["items"][0]["verdict"])

    def test_data_coverage_panel_and_export_render(self):
        snapshot = {
            "_data_coverage": {"mode": "dev", "requested_tickers": 2, "records": {"quotes": 1}, "states": {"SKIPPED_DEV_CAP": 1}},
            "_pipeline_status": {"mode": "dev", "steps": []},
            "_strategy_results": {"test_strategy": {"strategy_id": "test_strategy", "strategy_label": "Test Strategy", "enabled": True, "ran": True, "rows": [], "pass_count": 0, "watch_count": 0, "fail_count": 0, "skipped_count": 0}},
        }
        html = format_html("payload", [], {}, [], snapshot, [])
        self.assertIn("Shared Market Data Hub", html)
        self.assertIn("Copy Data Coverage Report", html)
        self.assertIn("SKIPPED_DEV_CAP", html)
        self.assertIn("Copy Test Strategy Report", html)

    def test_generic_lifecycle_classifies_broker_legs_only(self):
        legs = [
            {"option_type": "call", "strike": 100, "expiration": "2030-01-18"},
            {"option_type": "call", "strike": 110, "expiration": "2030-01-18"},
        ]
        self.assertEqual(classify_broker_option_structure(legs), "call_debit_vertical")
        envelope = build_lifecycle_envelope("NVDA", legs)
        self.assertEqual(envelope["entry_basis_status"], "broker_detected")
        self.assertIsNone(envelope["strategy_id"])
        self.assertTrue(envelope["structure_id"].startswith("NVDA:"))
        self.assertEqual(envelope["confidence"], "high")

    def test_generic_lifecycle_preserves_calendar_unknown_and_unpaired(self):
        calendar = [
            {"option_type": "call", "strike": 100, "expiration": "2030-01-18"},
            {"option_type": "call", "strike": 100, "expiration": "2030-02-18"},
        ]
        self.assertEqual(classify_broker_option_structure(calendar), "calendar")
        self.assertEqual(classify_broker_option_structure(calendar + [calendar[0]]), "unknown_multileg")
        self.assertEqual(classify_broker_option_structure(calendar[:1]), "unpaired_option_leg")

    def test_hard_fail_preserves_signal_but_actionability_is_zero(self):
        row = attach_actionability({"ticker": "ADBE", "verdict": "FAIL / DEBIT TOO LARGE", "score": 90})
        self.assertEqual(row["signal_score"], 90)
        self.assertEqual(row["actionability_score"], 0)

    def test_registry_execution_isolates_plugin_failure(self):
        context = create_run_data_context("dev")
        context.analysis_tickers = ["NVDA"]
        results = execute_strategy_registry(context, {
            "earnings_calendar": lambda: {"items": [{"ticker": "NVDA", "verdict": "PASS"}]},
            "skew_momentum_vertical": lambda: (_ for _ in ()).throw(RuntimeError("boom")),
            "stock_momentum": lambda: {"items": [{"ticker": "NVDA", "action": "CONSIDER ADDING"}]},
        })
        self.assertEqual(results["earnings_calendar"]["pass_count"], 1)
        self.assertTrue(results["skew_momentum_vertical"]["errors"])
        self.assertEqual(results["stock_momentum"]["pass_count"], 1)

    def test_staged_scan_exposes_counts_and_reasons(self):
        scan = StagedScan("skew_momentum_vertical")
        scan.record("cheap_prefilter", 30, 10, {"low_volume": 20})
        self.assertEqual(scan.summary()["stages"]["cheap_prefilter"]["rejected_count"], 20)


if __name__ == "__main__":
    unittest.main()
