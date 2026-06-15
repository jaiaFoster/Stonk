import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.payload_profile_service import build_payload_size_profile
from app.services.provider_payload_compaction_service import build_provider_payload_budget, compact_tradier_snapshot
from app.services.report_snapshot_service import ReportSnapshotRepository
from app.services.usage_telemetry_service import UsageTelemetryRepository


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


class Patch27LCompactSizeTelemetryReconciliationTests(unittest.TestCase):
    def test_payload_profile_compact_size_matches_budget_and_compactor(self):
        snapshot = _heavy_summary()["report_data"]["tradier_snapshot"]
        profile = build_payload_size_profile("payload", [], {}, [], snapshot, [], {})
        budget = build_provider_payload_budget(snapshot)
        compact = compact_tradier_snapshot(snapshot)

        self.assertEqual(profile["sections_bytes"]["tradier_snapshot_compact"], budget["compact_tradier_snapshot_bytes"])
        self.assertLess(
            budget["compact_tradier_snapshot_bytes"],
            len(__import__("json").dumps(compact, default=str, separators=(",", ":")).encode("utf-8")),
        )

    def test_telemetry_uses_compact_size_not_legacy_precompaction_size(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "state.sqlite3")
            with patch("app.services.usage_telemetry_service.config.USAGE_TELEMETRY_DB_PATH", path):
                repo = ReportSnapshotRepository(path)
                repo.save_success("run-1", "dev", "payload", _heavy_summary(), {}, {})
                snapshot = repo.latest_success(include_full=True)
                telemetry = UsageTelemetryRepository(path).summary()

        latest = telemetry["latest_size_profile"]
        snapshot_sizes = latest["snapshot_sizes"]
        self.assertLess(snapshot_sizes["compact_tradier_snapshot_bytes"], 1_000_000)
        self.assertEqual(snapshot_sizes["compact_tradier_snapshot_bytes"], 9302)


if __name__ == "__main__":
    unittest.main()
