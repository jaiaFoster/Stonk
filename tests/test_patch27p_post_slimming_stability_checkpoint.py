import tempfile
import unittest
from pathlib import Path

from app.services.developer_snapshot_service import build_developer_snapshot, build_snapshot_detail
from app.services.report_service import format_html
from app.services.report_snapshot_service import ReportSnapshotRepository
from app.services.run_manifest_repository import RunManifestRepository


HOT_SUMMARY_GUARDRAIL_BYTES = 250_000
COMPACT_TRADIER_GUARDRAIL_BYTES = 250_000
COMPACT_FULL_SUMMARY_GUARDRAIL_BYTES = 250_000
COMPRESSED_FULL_SUMMARY_GUARDRAIL_BYTES = 100_000
SNAPSHOT_SAVE_GUARDRAIL_BYTES = 500_000


def _summary():
    rows = [
        {
            "ticker": f"T{index}",
            "verdict": "WATCH / REVIEW",
            "score": 75,
            "diagnostics": [{"payload": "x" * 1000} for _ in range(8)],
        }
        for index in range(30)
    ]
    ff = {
        "strategy_id": "forward_factor_calendar",
        "enabled": True,
        "ran": True,
        "pass_count": 0,
        "watch_count": 1,
        "fail_count": 2,
        "skipped_count": 3,
        "summary": {"dry_run": True},
        "rows": rows,
    }
    daily = {
        "summary": {"action_count": 1, "stock_count": 1},
        "actions": [{"ticker": "NVDA", "strategy_id": "stock_momentum", "action": "CONSIDER ADDING"}],
    }
    lifecycle = {
        "summary": {"calendar_count": 1, "urgent_count": 0},
        "calendars": [{"ticker": "NVDA", "verdict": "HOLD"}],
    }
    return {
        "strategy_results": {"forward_factor_calendar": ff},
        "pipeline_status": {"report_quality": "SUCCESS_COMPLETE", "steps": rows},
        "report_quality": "SUCCESS_COMPLETE",
        "report_data": {
            "positions": [{"ticker": "NVDA", "market_value": 1000}],
            "recommendations": [{"ticker": "NVDA", "action": "HOLD", "risks": []}],
            "news": {},
            "tradier_snapshot": {
                "_strategy_results": {"forward_factor_calendar": ff},
                "_daily_opportunity_engine": daily,
                "_calendar_lifecycle_checks": lifecycle,
                "_unified_calendar_trade_engine": lifecycle,
                "_provider_status": {"robinhood": {"status": "ok"}, "tradier": {"status": "ok"}},
                "_pipeline_status": {"report_quality": "SUCCESS_COMPLETE", "steps": rows},
            },
            "log": ["line"] * 50,
        },
    }


class Patch27PPostSlimmingStabilityCheckpointTests(unittest.TestCase):
    def test_cached_shell_preserves_essential_dashboard_contract(self):
        summary = _summary()
        report = summary["report_data"]
        shell = format_html(
            "payload",
            report["positions"],
            report["news"],
            report["recommendations"],
            report["tradier_snapshot"],
            report["log"],
            view="shell",
        )

        for required_text in (
            "Portfolio Status",
            "Active Calendar Lifecycle",
            "Daily Opportunity",
            "Top Actionable Adds",
            "Urgent Risk Review",
            "Strategy Summary",
            "FF DRY",
            "Open Full Report",
            "Heavy detail stays dormant until requested.",
        ):
            self.assertIn(required_text, shell)

    def test_latest_full_and_detail_contracts_remain_provider_free(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "state.sqlite3")
            reports = ReportSnapshotRepository(path)
            manifests = RunManifestRepository(path)
            reports.save_success("run-1", "dev", "payload", _summary(), {}, {})
            manifests.save({"run_id": "run-1", "status": "complete"})

            latest = build_developer_snapshot("latest", reports, manifests)
            full = build_developer_snapshot("full", reports, manifests)
            details = {
                section: build_snapshot_detail(section, report_repository=reports)
                for section in ("daily_opportunity", "lifecycle", "portfolio", "providers", "strategies", "pipeline", "provider_raw")
            }

        for snapshot in (latest, full, *details.values()):
            self.assertTrue(snapshot["read_only"])
            self.assertFalse(snapshot["provider_calls_triggered"])
        for key in (
            "available_detail_sections",
            "portfolio_summary",
            "positions_summary",
            "calendar_lifecycle_summary",
            "daily_opportunity",
            "strategy_summaries",
            "report_snapshot_profile",
        ):
            self.assertIn(key, latest)
        self.assertIn("rows", full["strategy_summaries"]["forward_factor_calendar"])
        self.assertTrue(details["provider_raw"]["raw_provider_payload"])

    def test_daily_opportunity_and_forward_factor_dry_run_contract_survive_compaction(self):
        with tempfile.TemporaryDirectory() as temp:
            reports = ReportSnapshotRepository(str(Path(temp) / "state.sqlite3"))
            reports.save_success("run-1", "dev", "payload", _summary(), {}, {})
            latest = build_developer_snapshot("latest", reports, RunManifestRepository(str(Path(temp) / "state.sqlite3")))

        # 30E: developer_snapshot now uses full=True; daily_opportunity comes from full blob
        do = latest.get("daily_opportunity") or {}
        actions = do.get("actions") or []
        ff = latest.get("strategy_summaries", {}).get("forward_factor_calendar") or {}
        # FF should not appear in DO actions (dry-run excluded)
        self.assertFalse(any(row.get("strategy_id") == "forward_factor_calendar" for row in actions))

    def test_representative_snapshot_stays_inside_accepted_non_brittle_budgets(self):
        with tempfile.TemporaryDirectory() as temp:
            reports = ReportSnapshotRepository(str(Path(temp) / "state.sqlite3"))
            reports.save_success("run-1", "dev", "payload", _summary(), {}, {})
            profile = reports.snapshot_profile(reports.latest_success())

        snapshot_save = (
            profile["hot_summary_bytes"]
            + profile["compressed_full_summary_bytes"]
            + profile["compressed_full_payload_bytes"]
            + profile["compressed_raw_provider_bytes"]
        )
        self.assertLess(profile["hot_summary_bytes"], HOT_SUMMARY_GUARDRAIL_BYTES)
        self.assertLess(profile["compact_tradier_snapshot_bytes"], COMPACT_TRADIER_GUARDRAIL_BYTES)
        self.assertLess(profile["full_summary_bytes"], COMPACT_FULL_SUMMARY_GUARDRAIL_BYTES)
        self.assertLess(profile["compressed_full_summary_bytes"], COMPRESSED_FULL_SUMMARY_GUARDRAIL_BYTES)
        self.assertLess(snapshot_save, SNAPSHOT_SAVE_GUARDRAIL_BYTES)


if __name__ == "__main__":
    unittest.main()
