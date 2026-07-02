import unittest

from app.services.calendar_ranking_service import build_calendar_ranking


class CalendarRankingServiceTests(unittest.TestCase):
    def test_failed_threshold_wording_uses_correct_operators(self):
        ranking = build_calendar_ranking(
            [
                {
                    "ticker": "CPB",
                    "max_leg_spread_pct": 54.5,
                    "min_leg_open_interest": 27,
                    "min_leg_volume": 0,
                    "debit_pct_underlying": 4,
                    "iv_edge": 1,
                    "earnings_event": {"earnings_date": "2026-06-08", "is_timestamp_confirmed": True},
                }
            ],
            {"items": [{"ticker": "CPB", "score": 60, "is_preferred_setup": False}]},
            log_print=lambda msg: None,
        )

        criteria = {item["name"]: item["detail"] for item in ranking["items"][0]["criteria"]}

        self.assertIn("54.5% > 15% limit", criteria["Bid/ask spread"])
        self.assertIn("27 < 50 minimum", criteria["Open interest"])
        self.assertIn("0 < 10 preferred minimum", criteria["Same-day volume"])

    def test_insufficient_candles_block_backtest_not_candidate_verdict(self):
        ranking = build_calendar_ranking(
            [
                {
                    "ticker": "AVGO",
                    "front_expiration": "2026-06-12",
                    "back_expiration": "2026-07-17",
                    "max_leg_spread_pct": 5,
                    "min_leg_open_interest": 100,
                    "min_leg_volume": 50,
                    "debit_pct_underlying": 2,
                    "iv_edge": 3,
                    "earnings_timing": {"captures_event": True},
                    "earnings_event": {"earnings_date": "2026-06-11", "session_label": "AMC", "is_timestamp_confirmed": True},
                    "candle_quality": {"confidence": "low", "selected_provider": "tradier"},
                }
            ],
            {"items": [{"ticker": "AVGO", "score": 85, "is_preferred_setup": True, "earnings": {"earnings_date": "2026-06-11", "session_label": "AMC", "is_timestamp_confirmed": True}}]},
            log_print=lambda msg: None,
        )

        row = ranking["items"][0]
        self.assertTrue(row["passes_all_criteria"])
        self.assertFalse(row["backtest_eligible"])
        self.assertEqual(row["backtest_mode"], "skipped_insufficient_candles")
        self.assertIn("insufficient_historical_candle_data", row["backtest_blockers"])

    def test_adverse_iv_relationship_is_fail_not_warn(self):
        ranking = build_calendar_ranking(
            [
                {
                    "ticker": "CAG",
                    "front_expiration": "2026-06-12",
                    "back_expiration": "2026-07-17",
                    "max_leg_spread_pct": 5,
                    "min_leg_open_interest": 100,
                    "min_leg_volume": 50,
                    "debit_pct_underlying": 2,
                    "iv_edge": -0.5,
                    "earnings_timing": {"captures_event": True},
                    "earnings_event": {"earnings_date": "2026-06-11", "session_label": "AMC", "is_timestamp_confirmed": True},
                    "candle_quality": {"confidence": "high", "selected_provider": "tradier"},
                }
            ],
            {"items": [{"ticker": "CAG", "score": 85, "is_preferred_setup": True, "earnings": {"earnings_date": "2026-06-11", "session_label": "AMC", "is_timestamp_confirmed": True}}]},
            log_print=lambda msg: None,
        )

        row = ranking["items"][0]
        criteria = {item["name"]: item["status"] for item in row["criteria"]}
        self.assertEqual(criteria["IV relationship"], "FAIL")
        self.assertFalse(row["passes_all_criteria"])


if __name__ == "__main__":
    unittest.main()
