import unittest

from app.services.calendar_hold_through_service import build_hold_through_score


class CalendarHoldThroughServiceTests(unittest.TestCase):
    def test_positive_pnl_does_not_override_large_historical_moves(self):
        check = {
            "estimated_pnl_pct": 20,
            "target_profit_pct": 50,
            "max_loss_pct": -35,
            "historical_move_summary": {
                "avg_abs_event_move_pct": 15,
                "max_abs_event_move_pct": 28,
                "small_move_rate_pct": 20,
            },
            "estimated_breakeven_pct": 7,
            "pricing_quality": {"confidence": "medium"},
            "assignment_risk_level": "Low",
        }

        result = build_hold_through_score(check)

        self.assertIn(result["hold_through_action"], {"CONSIDER CLOSING BEFORE EARNINGS", "CLOSE / AVOID HOLD-THROUGH"})
        self.assertTrue(result["historical_move_warning"])
        self.assertLess(result["hold_through_score"], 60)

    def test_muted_moves_and_low_assignment_risk_support_hold_review(self):
        check = {
            "estimated_pnl_pct": 0,
            "target_profit_pct": 50,
            "max_loss_pct": -35,
            "historical_move_summary": {
                "avg_abs_event_move_pct": 4,
                "max_abs_event_move_pct": 7,
                "small_move_rate_pct": 80,
            },
            "estimated_breakeven_pct": 8,
            "net_iv_estimate": 1,
            "pricing_quality": {"confidence": "high"},
            "assignment_risk_level": "Low",
        }

        result = build_hold_through_score(check)

        self.assertGreaterEqual(result["hold_through_score"], 60)
        self.assertIn(result["hold_through_action"], {"HOLD-THROUGH SUPPORTED", "HOLD, BUT REDUCE RISK / STRICT EXIT"})


if __name__ == "__main__":
    unittest.main()
