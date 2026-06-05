import unittest

from app.services.unified_calendar_trade_engine_service import _entry_plan


class UnifiedCalendarTradeEngineWordingTests(unittest.TestCase):
    def test_pre_earnings_financing_debit_fail_is_research_only_no_entry(self):
        plan = _entry_plan(
            "FAIL / DEBIT TOO LARGE",
            {"days_until_earnings": 5},
            {"ticker": "ADBE", "conservative_debit": 15.0},
            {"next_check": "Possible entry window after live quotes confirm."},
            {
                "trade_type": "pre_earnings_financing_or_directional_long_vol",
                "trade_type_label": "PRE-EARNINGS FINANCING / LONG-VOL TRADE",
                "main_blocker": "debit too large for account",
            },
        )

        self.assertIn("No entry.", plan)
        self.assertIn("Research-only pre-earnings financing / long-vol", plan)
        self.assertIn("debit/account guardrail failed", plan)
        self.assertNotIn("Possible entry window", plan)


if __name__ == "__main__":
    unittest.main()
