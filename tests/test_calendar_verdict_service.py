import unittest

from app.services.calendar_risk_fact_service import evaluate_account_risk
from app.services.calendar_trade_type_service import classify_trade_type


class CalendarFactServiceTests(unittest.TestCase):
    def test_trade_type_unknown_when_timestamp_unconfirmed(self):
        candidate = {
            "front_expiration": "2026-06-10",
            "back_expiration": "2026-07-10",
            "earnings_event": {"earnings_date": "2026-06-12", "session_label": "Unknown"},
        }

        result = classify_trade_type(candidate)

        self.assertEqual(result["trade_type"], "unknown_event_timing")

    def test_pre_earnings_financing_fact(self):
        candidate = {
            "front_expiration": "2026-06-10",
            "back_expiration": "2026-06-19",
            "earnings_event": {
                "earnings_date": "2026-06-12",
                "session_label": "Before Market Open",
                "is_timestamp_confirmed": True,
            },
        }

        result = classify_trade_type(candidate)

        self.assertEqual(result["trade_type"], "pre_earnings_financing_or_directional_long_vol")

    def test_account_risk_is_fact_only(self):
        result = evaluate_account_risk(
            {"conservative_debit": 2.5},
            {"account_value_estimate": 100_000, "account_value_source": "fixture"},
        )

        self.assertEqual(result["account_value_source"], "fixture")
        self.assertEqual(result["debit_total_estimate"], 250.0)
        self.assertIn(result["account_risk_status"], {"OK", "WATCH SIZE", "TOO LARGE"})


if __name__ == "__main__":
    unittest.main()
