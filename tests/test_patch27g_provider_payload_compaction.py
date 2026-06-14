import json
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from app import config
from app.main import app
from app.services.payload_profile_service import build_payload_size_profile
from app.services.provider_payload_compaction_service import compact_tradier_snapshot
from app.services.report_snapshot_service import ReportSnapshotRepository


def _summary():
    contracts = [
        {
            "symbol": f"NVDA260821C{index:08d}",
            "option_type": "call",
            "strike": 100 + index,
            "bid": 1.0,
            "ask": 1.2,
            "delta": 0.35,
            "raw_payload": "x" * 1000,
        }
        for index in range(200)
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
                "_strategy_results": {
                    "forward_factor_calendar": {
                        "strategy_id": "forward_factor_calendar",
                        "enabled": True,
                        "rows": [{"ticker": "NVDA", "verdict": "FAIL / BELOW THRESHOLD"}],
                    }
                },
            },
            "log": ["done"],
        },
    }


class Patch27GProviderPayloadCompactionTests(unittest.TestCase):
    def test_compactor_replaces_raw_chain_and_reports_budget(self):
        tradier = _summary()["report_data"]["tradier_snapshot"]
        compact = compact_tradier_snapshot(tradier)
        chain = compact["NVDA"]["chains_by_expiration"]
        budget = compact["_provider_payload_budget"]

        self.assertTrue(chain["compacted"])
        self.assertEqual(chain["count"], 1)
        self.assertLess(budget["compact_tradier_snapshot_bytes"], budget["tradier_snapshot_bytes"])
        self.assertGreater(budget["saved_bytes"], 0)
        self.assertEqual(compact["_raw_provider_archive"]["detail_section"], "provider_raw")

    def test_stored_full_summary_is_compact_and_explicit_full_rehydrates_raw(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = ReportSnapshotRepository(str(Path(temp) / "reports.sqlite3"))
            repo.save_success("run-1", "dev", "payload", _summary(), {}, {})
            snapshot = repo.latest_success(include_full=True)
            compact_full = json.loads(zlib.decompress(snapshot["full_summary_blob"]).decode("utf-8"))
            profile = repo.snapshot_profile(snapshot)
            raw = repo.load_raw_provider_snapshot(snapshot)
            full = repo.load_summary(snapshot, full=True)

        compact_chain = compact_full["report_data"]["tradier_snapshot"]["NVDA"]["chains_by_expiration"]
        self.assertTrue(compact_chain["compacted"])
        self.assertIsInstance(raw["NVDA"]["chains_by_expiration"]["2026-08-21"], list)
        self.assertIsInstance(full["report_data"]["tradier_snapshot"]["NVDA"]["chains_by_expiration"]["2026-08-21"], list)
        self.assertLess(profile["full_summary_bytes"], profile["raw_provider_snapshot_bytes"])
        self.assertGreater(profile["compressed_raw_provider_bytes"], 0)

    def test_provider_raw_detail_is_explicit_read_only_and_provider_free(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "reports.sqlite3")
            with patch.object(config, "REPORT_SNAPSHOT_DB_PATH", path), \
                 patch.object(config, "RUN_TOKEN", "token"), \
                 patch.object(config, "ENABLE_DEV_SNAPSHOT_ENDPOINT", True):
                ReportSnapshotRepository().save_success("run-1", "dev", "payload", _summary(), {}, {})
                client = app.test_client()
                with patch("app.main.run") as pipeline:
                    response = client.get("/api/dev/snapshot/detail/provider_raw?token=token")
                body = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["raw_provider_payload"])
        self.assertTrue(body["read_only"])
        self.assertFalse(body["provider_calls_triggered"])
        self.assertIn("chains_by_expiration", body["detail"]["NVDA"])
        pipeline.assert_not_called()

    def test_uncompressed_compatibility_mode_still_rehydrates_raw_provider(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(config, "REPORT_SNAPSHOT_STORE_COMPRESSED_FULL", False):
            repo = ReportSnapshotRepository(str(Path(temp) / "reports.sqlite3"))
            repo.save_success("run-1", "dev", "payload", _summary(), {}, {})
            snapshot = repo.latest_success(include_full=True)
            full = repo.load_summary(snapshot, full=True)

        self.assertIsNone(snapshot["full_summary_blob"])
        self.assertIsInstance(full["report_data"]["tradier_snapshot"]["NVDA"]["chains_by_expiration"]["2026-08-21"], list)

    def test_payload_profile_includes_provider_budget(self):
        tradier = _summary()["report_data"]["tradier_snapshot"]
        profile = build_payload_size_profile("payload", [], {}, [], tradier, [], {})
        budget = profile["provider_payload_budget"]

        self.assertEqual(profile["sections_bytes"]["tradier_snapshot"], budget["tradier_snapshot_bytes"])
        self.assertEqual(profile["sections_bytes"]["tradier_snapshot_compact"], budget["compact_tradier_snapshot_bytes"])
        self.assertGreater(budget["reduction_pct"], 50)


if __name__ == "__main__":
    unittest.main()
