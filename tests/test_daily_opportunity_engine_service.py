import unittest

from app.services.daily_opportunity_engine_service import build_daily_opportunity_engine


class DailyOpportunityEngineTests(unittest.TestCase):
    def test_failed_calendar_candidate_is_not_shown_as_entry(self):
        engine = {
            "new_trade_rows": [
                {
                    "ticker": "ASO",
                    "score": 95,
                    "verdict": "FAIL / NO LIVE LIQUIDITY",
                    "entry_plan": "No entry.",
                    "requirements": [{"name": "Liquidity", "status": "FAIL", "detail": "No OI or volume."}],
                }
            ],
            "open_trade_rows": [],
        }

        result = build_daily_opportunity_engine(engine, {}, {}, [], log_print=lambda msg: None)

        self.assertEqual(result["actions"], [])

    def test_active_calendar_lifecycle_alert_survives_low_score_filter(self):
        engine = {
            "new_trade_rows": [],
            "open_trade_rows": [
                {
                    "ticker": "PDD",
                    "score": 20,
                    "verdict": "URGENT REVIEW / EXIT CHECK",
                    "next_action": "Review before close.",
                    "raw": {"lifecycle_priority_score": 20, "decision_summary": "Short leg near event."},
                }
            ],
        }

        result = build_daily_opportunity_engine(engine, {}, {}, [], log_print=lambda msg: None)

        self.assertEqual(len(result["actions"]), 1)
        self.assertEqual(result["actions"][0]["type"], "active_calendar")
        self.assertGreaterEqual(result["actions"][0]["priority_score"], 90)


if __name__ == "__main__":
    unittest.main()
