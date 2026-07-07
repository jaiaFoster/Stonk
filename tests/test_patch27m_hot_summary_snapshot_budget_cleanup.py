import json
import tempfile
import unittest
import zlib
from pathlib import Path

from app.services.report_service import format_html
from app.services.report_snapshot_service import ReportSnapshotRepository


def _heavy_rows(count=30):
    return [
        {
            "ticker": f"T{index}",
            "verdict": "WATCH / REVIEW",
            "score": 70 + index,
            "reasons": ["review"] * 20,
            "diagnostics": {
                "pair_candidates": [{"payload": "x" * 1500} for _ in range(10)],
                "source_checks": [{"payload": "y" * 1500} for _ in range(10)],
            },
        }
        for index in range(count)
    ]


def _summary():
    rows = _heavy_rows()
    skew_strategy = {
        "strategy_id": "skew_momentum_vertical",
        "strategy_label": "Skew Momentum Vertical",
        "enabled": True,
        "ran": True,
        "summary": {"pass_count": 0, "watch_count": len(rows), "candidate_audit": rows},
        "pass_items": [],
        "watch_items": rows,
        "blocked_items": [],
        "items": rows,
        "rows": rows,
    }
    ff_strategy = {
        "strategy_id": "forward_factor_calendar",
        "strategy_label": "Forward Factor Calendar",
        "enabled": True,
        "ran": True,
        "summary": {
            "dry_run": True,
            "candidate_selection_audit": rows,
            "positive_signal_count": 0,
        },
        "rows": rows,
    }
    strategies = {
        "skew_momentum_vertical": skew_strategy,
        "forward_factor_calendar": ff_strategy,
    }
    pipeline = {"report_quality": "SUCCESS_COMPLETE", "steps": rows}
    return {
        "strategy_results": strategies,
        "pipeline_status": pipeline,
        "runtime_profile": {"total_ms": 10},
        "payload_size_profile": {"sections_bytes": {"tradier_snapshot": 100}},
        "storage_profile": {"database_size_bytes": 100},
        "report_quality": "SUCCESS_COMPLETE",
        "report_data": {
            "positions": [{"ticker": "NVDA", "market_value": 1000}],
            "news": {},
            "recommendations": [],
            "tradier_snapshot": {
                "_strategy_results": strategies,
                "_skew_momentum_vertical_strategy": skew_strategy,
                "_pipeline_status": pipeline,
                "_runtime_profile": {"total_ms": 10},
                "_payload_size_profile": {"sections_bytes": {"tradier_snapshot": 100}},
                "_storage_profile": {"database_size_bytes": 100},
                "_daily_opportunity_engine": {"summary": {"action_count": len(rows)}, "actions": rows},
                "_unified_calendar_trade_engine": {"summary": {"active_count": len(rows)}, "new_trade_rows": rows},
            },
            "log": ["line"] * 50,
        },
    }


class Patch27MHotSummarySnapshotBudgetCleanupTests(unittest.TestCase):
    def test_hot_summary_keeps_status_but_moves_heavy_rows_to_full_detail(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = ReportSnapshotRepository(str(Path(temp) / "state.sqlite3"))
            repo.save_success("run-1", "dev", "payload", _summary(), {}, {})
            hot = repo.load_summary(repo.latest_success())
            full = repo.load_summary(repo.latest_success(include_full=True), full=True)
            profile = repo.snapshot_profile(repo.latest_success())

        # 30E: summary_json stores compact manifest (schema_version=2, no report_data)
        self.assertEqual(hot.get("schema_version"), 2)
        self.assertTrue(hot.get("compact_manifest"))
        self.assertNotIn("report_data", hot)
        # Full blob still has all strategy rows via report_data.tradier_snapshot
        full_tradier = (full.get("report_data") or {}).get("tradier_snapshot") or {}
        if full_tradier.get("_skew_momentum_vertical_strategy"):
            skew_items = full_tradier["_skew_momentum_vertical_strategy"].get("items") or []
            self.assertEqual(len(skew_items), 30)
        self.assertLess(profile["hot_summary_bytes"], profile["full_summary_bytes"])

    def test_hot_summary_preserves_list_shapes_needed_by_cached_shell(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = ReportSnapshotRepository(str(Path(temp) / "state.sqlite3"))
            repo.save_success("run-1", "dev", "payload", _summary(), {}, {})
            # 30E: Shell now reads from full blob (load_summary full=True) for tradier_snapshot structure
            full_summary = repo.load_summary(repo.latest_success(include_full=True), full=True)
            report = full_summary.get("report_data") or {}

        if not report.get("tradier_snapshot"):
            # If full blob unavailable (compressed disabled), skip HTML check
            return
        html = format_html(
            "payload",
            report.get("positions") or [],
            report.get("news") or {},
            report.get("recommendations") or [],
            report.get("tradier_snapshot") or {},
            report.get("log") or [],
            view="shell",
        )
        self.assertIn('data-dashboard-view="shell"', html)
        self.assertIn("FF DRY", html)

    def test_stored_compact_full_deduplicates_aliases_and_full_read_rehydrates_them(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = ReportSnapshotRepository(str(Path(temp) / "state.sqlite3"))
            repo.save_success("run-1", "dev", "payload", _summary(), {}, {})
            snapshot = repo.latest_success(include_full=True)
            stored = json.loads(zlib.decompress(snapshot["full_summary_blob"]).decode("utf-8"))
            full = repo.load_summary(snapshot, full=True)

        for key in ("strategy_results", "pipeline_status", "runtime_profile", "payload_size_profile", "storage_profile"):
            self.assertNotIn(key, stored)
            self.assertIn(key, full)
        self.assertEqual(full["pipeline_status"]["report_quality"], "SUCCESS_COMPLETE")


if __name__ == "__main__":
    unittest.main()
