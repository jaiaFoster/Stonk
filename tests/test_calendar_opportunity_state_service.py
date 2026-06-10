import unittest

from app.services.calendar_opportunity_state_service import (
    attach_calendar_display_fields,
    build_strategy_opportunity_row,
    normalize_calendar_opportunity_state,
)


class CalendarOpportunityStateServiceTests(unittest.TestCase):
    def test_precheck_failure_has_recoverable_blocked_state(self):
        state = normalize_calendar_opportunity_state(
            {
                "ticker": "ASO",
                "verdict": "FAIL / NO VALID CALENDAR STRUCTURE",
                "quality_precheck": {
                    "passes_precheck": False,
                    "primary_rejection_reason": "Option expirations: No front/back expiration pair matched scanner settings.",
                },
            }
        )

        self.assertEqual(state["display_state"], "BLOCKED_PRECHECK")
        self.assertIn("precheck", state["recoverability_hint"].lower())

    def test_ranking_failure_and_strategy_shape_are_explicit(self):
        row = attach_calendar_display_fields(
            {
                "ticker": "ADBE",
                "score": 72,
                "verdict": "FAIL / DO NOT ENTER",
                "candidate": {"ticker": "ADBE"},
                "ranking": {"action": "FAIL / DO NOT BACKTEST", "entry_timing": "EARLY"},
                "main_blocker": "options market untradeable",
                "risks": ["Wide spread"],
            }
        )
        opportunity = build_strategy_opportunity_row(row)

        self.assertEqual(row["display_state"], "BLOCKED_RANKING")
        self.assertEqual(opportunity["strategy_id"], "earnings_calendar")
        self.assertEqual(opportunity["ticker"], "ADBE")
        self.assertEqual(opportunity["display_state"], "BLOCKED_RANKING")

    def test_open_calendar_is_active(self):
        state = normalize_calendar_opportunity_state(
            {"ticker": "AVGO", "type": "open_calendar", "verdict": "HOLD / MONITOR"}
        )

        self.assertEqual(state["display_state"], "ACTIVE_OPEN")


if __name__ == "__main__":
    unittest.main()
