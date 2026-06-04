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


if __name__ == "__main__":
    unittest.main()
