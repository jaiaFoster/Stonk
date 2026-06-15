import json
import tempfile
import unittest
import zlib
from pathlib import Path

from app.services.provider_payload_compaction_service import build_provider_payload_budget, compact_tradier_snapshot
from app.services.report_snapshot_service import ReportSnapshotRepository


def _heavy_summary():
    heavy_row = {
        "ticker": "NVDA",
        "verdict": "WATCH / DIAGNOSTIC",
        "legs": [
            {"symbol": "NVDA1", "option_type": "call", "strike": 100, "bid": 1.0, "ask": 1.2, "mid": 1.1, "delta": 0.35, "open_interest": 200, "volume": 10, "raw": "x" * 500},
            {"symbol": "NVDA2", "option_type": "call", "strike": 110, "bid": 0.8, "ask": 1.0, "mid": 0.9, "delta": 0.22, "open_interest": 180, "volume": 8, "raw": "x" * 500},
        ],
        "contracts": [{"symbol": f"NVDA{i}", "raw": "y" * 1000} for i in range(10)],
        "raw_payload": {"blob": "z" * 2000},
    }
    rows = [dict(heavy_row, ticker=f"T{i}") for i in range(60)]
    contracts = [
        {"symbol": f"NVDA260821C{index:08d}", "option_type": "call", "strike": 100 + index, "bid": 1.0, "ask": 1.2, "delta": 0.35, "raw_payload": "x" * 1000}
        for index in range(300)
    ]
    return {
        "report_quality": "SUCCESS_COMPLETE",
        "report_data": {
            "positions": [{"ticker": "NVDA", "market_value": 1000}],
            "news": {},
            "recommendations": [],
            "tradier_snapshot": {
                "NVDA": {
                    "ticker": "NVDA",
                    "has_data": True,
                    "quote": {"last": 180, "bid": 179.9, "ask": 180.1},
                    "selected_expiration": "2026-08-21",
                    "chain_contract_count": len(contracts),
                    "chains_by_expiration": {"2026-08-21": contracts},
                },
                "_provider_status": {"tradier": {"status": "ok"}},
                "_calendar_opportunity_cache": {"summary": {"write_count": len(rows)}, "rows": rows},
                "_forward_factor_strategy": {"summary": {"rows": len(rows)}, "rows": rows},
                "_strategy_results": {
                    "forward_factor_calendar": {
                        "strategy_id": "forward_factor_calendar",
                        "enabled": True,
                        "rows": rows,
                        "pass_count": 0,
                        "watch_count": len(rows),
                        "fail_count": 0,
                        "skipped_count": 0,
                        "summary": {"dry_run": True},
                    }
                },
            },
            "log": ["done"],
        },
    }


class Patch27KTradierCompactSlimmingTests(unittest.TestCase):
    def test_compact_snapshot_reduces_heavy_strategy_and_cache_rows(self):
        tradier = _heavy_summary()["report_data"]["tradier_snapshot"]
        compact = compact_tradier_snapshot(tradier)
        budget = build_provider_payload_budget(tradier, compact=compact)

        self.assertLess(budget["compact_tradier_snapshot_bytes"], 1_000_000)
        self.assertGreater(budget["reduction_pct"], 50)
        self.assertEqual(compact["_strategy_results"]["forward_factor_calendar"]["row_summary"]["count"], 60)
        self.assertNotIn("rows", compact["_strategy_results"]["forward_factor_calendar"])
        self.assertEqual(compact["_calendar_opportunity_cache"]["row_summary"]["count"], 60)
        self.assertIn("sample", compact["_calendar_opportunity_cache"]["row_summary"])

    def test_raw_provider_blob_still_rehydrates_full_detail(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = ReportSnapshotRepository(str(Path(temp) / "reports.sqlite3"))
            repo.save_success("run-1", "dev", "payload", _heavy_summary(), {}, {})
            snapshot = repo.latest_success(include_full=True)
            compact_full = json.loads(zlib.decompress(snapshot["full_summary_blob"]).decode("utf-8"))
            raw = repo.load_raw_provider_snapshot(snapshot)
            full = repo.load_summary(snapshot, full=True)

        self.assertIn("row_summary", compact_full["report_data"]["tradier_snapshot"]["_calendar_opportunity_cache"])
        self.assertIsInstance(raw["_calendar_opportunity_cache"]["rows"], list)
        self.assertIsInstance(full["report_data"]["tradier_snapshot"]["_strategy_results"]["forward_factor_calendar"]["rows"], list)


if __name__ == "__main__":
    unittest.main()
