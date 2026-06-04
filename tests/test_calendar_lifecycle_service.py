import unittest

from app.services.calendar_lifecycle_service import evaluate_calendar_lifecycle


class CalendarLifecycleServiceTests(unittest.TestCase):
    def test_active_calendar_moneyness_uses_underlying_quote(self):
        open_options = {
            "calendars": [
                {
                    "ticker": "AVGO",
                    "underlying": "AVGO",
                    "option_type": "call",
                    "strike": 430,
                    "quantity": 1,
                    "front_dte": 4,
                    "back_dte": 32,
                    "front_expiration": "2026-06-05",
                    "back_expiration": "2026-07-02",
                    "current_mid_debit": 3.30,
                    "entry_mid_debit_estimate": 3.19,
                    "short_front_leg": {"mid": 5.0},
                    "long_back_leg": {"mid": 8.3},
                    "pricing_quality": {"confidence": "medium"},
                }
            ]
        }
        tradier_snapshot = {"AVGO": {"quote": {"last": 425.0}}}

        result = evaluate_calendar_lifecycle(open_options, tradier_snapshot=tradier_snapshot, log_print=lambda msg: None)
        check = result["checks"][0]

        self.assertEqual(check["underlying_price"], 425.0)
        self.assertIsNotNone(check["short_moneyness_pct"])
        self.assertIsNotNone(check["distance_to_short_strike_dollars"])
        self.assertTrue(check["assignment_risk_reasons"])


if __name__ == "__main__":
    unittest.main()
