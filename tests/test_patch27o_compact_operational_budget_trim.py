import json
import unittest

from app.services.provider_payload_compaction_service import compact_tradier_snapshot


def _heavy_tradier():
    rows = [
        {
            "ticker": f"T{index}",
            "verdict": "WATCH / DIAGNOSTIC",
            "legs": [{"symbol": f"T{index}C", "strike": 100, "bid": 1, "ask": 1.2}],
            "contracts": [{"symbol": f"T{index}-{contract}", "raw": "x" * 1000} for contract in range(10)],
        }
        for index in range(60)
    ]
    return {
        "NVDA": {
            "ticker": "NVDA",
            "has_data": True,
            "chains_by_expiration": {"2026-08-21": rows},
        },
        "_calendar_opportunity_cache": {"summary": {"write_count": len(rows)}, "rows": rows},
        "_strategy_results": {
            "forward_factor_calendar": {
                "strategy_id": "forward_factor_calendar",
                "enabled": True,
                "rows": rows,
                "watch_count": len(rows),
                "summary": {"dry_run": True},
            }
        },
    }


class Patch27OCompactOperationalBudgetTrimTests(unittest.TestCase):
    def test_repeated_operational_diagnostics_are_summaries_not_full_arrays(self):
        rows = [
            {
                "ticker": f"T{index}",
                "verdict": "WATCH",
                "diagnostics": [{"payload": "x" * 2000} for _ in range(20)],
            }
            for index in range(50)
        ]
        tradier = _heavy_tradier()
        tradier.update({
            "_earnings_trade_discovery": {"summary": {"candidate_audit": rows}, "items": rows},
            "_earnings_discovery_quality": {"summary": {"checks": rows}, "items": rows},
            "_run_data_context": {"summary": {"fetch_audit": rows}, "items": rows},
            "_watchlist_review": {"summary": {"candidate_count": len(rows)}, "items": rows},
            "_pipeline_status": {"summary": {"step_count": len(rows)}, "steps": rows},
        })

        compact = compact_tradier_snapshot(tradier)

        for key in (
            "_earnings_trade_discovery",
            "_earnings_discovery_quality",
            "_run_data_context",
            "_watchlist_review",
            "_pipeline_status",
        ):
            self.assertNotIn("items", compact[key])
            self.assertIn("item_summary", compact[key])
        self.assertEqual(
            compact["_earnings_trade_discovery"]["summary"]["candidate_audit"]["count"],
            len(rows),
        )
        self.assertLess(
            len(json.dumps(compact, separators=(",", ":"))),
            250_000,
        )

    def test_selected_strategy_summary_counts_remain_available(self):
        tradier = _heavy_tradier()
        compact = compact_tradier_snapshot(tradier)
        ff = compact["_strategy_results"]["forward_factor_calendar"]

        self.assertEqual(ff["watch_count"], 60)
        self.assertEqual(ff["row_summary"]["count"], 60)
        self.assertTrue(ff["summary"]["dry_run"])
        self.assertEqual(compact["_raw_provider_archive"]["detail_section"], "provider_raw")


if __name__ == "__main__":
    unittest.main()
