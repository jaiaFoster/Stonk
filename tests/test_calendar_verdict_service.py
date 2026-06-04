import unittest

from app.services.calendar_verdict_service import build_final_calendar_verdict, classify_trade_type


class CalendarVerdictServiceTests(unittest.TestCase):
    def test_aso_style_untradeable_candidate_never_passes(self):
        candidate = {
            "ticker": "ASO",
            "max_leg_spread_pct": 91.9,
            "min_leg_open_interest": 0,
            "min_leg_volume": 0,
            "iv_edge": -8,
            "front_expiration": "2026-06-10",
            "back_expiration": "2026-07-10",
            "earnings_event": {
                "earnings_date": "2026-06-12",
                "session_label": "After Market Close",
                "is_timestamp_confirmed": True,
            },
        }
        ranking = {
            "ticker": "ASO",
            "action": "FAIL / DO NOT BACKTEST",
            "criteria": [{"status": "FAIL", "detail": "Max spread 91.9%, min OI 0, min volume 0."}],
        }

        verdict = build_final_calendar_verdict(candidate, ranking)

        self.assertEqual(verdict["status"], "FAIL")
        self.assertFalse(verdict["can_show_as_entry"])
        self.assertEqual(verdict["main_blocker"], "options market untradeable")
        self.assertEqual(verdict["backtest_status"], "skipped_untradeable")

    def test_ranking_fail_overrides_raw_pass(self):
        candidate = {
            "ticker": "XYZ",
            "max_leg_spread_pct": 10,
            "min_leg_open_interest": 100,
            "min_leg_volume": 25,
            "iv_edge": 2,
            "front_expiration": "2026-06-10",
            "back_expiration": "2026-07-10",
            "earnings_event": {
                "earnings_date": "2026-06-12",
                "session_label": "After Market Close",
                "is_timestamp_confirmed": True,
            },
        }
        ranking = {"ticker": "XYZ", "action": "FAIL / DO NOT BACKTEST", "criteria": []}

        verdict = build_final_calendar_verdict(candidate, ranking)

        self.assertEqual(verdict["final_verdict"], "FAIL / DO NOT ENTER")
        self.assertEqual(verdict["status"], "FAIL")
        self.assertEqual(verdict["raw_scanner_verdict"], "PASS / POSSIBLE ENTRY SETUP")

    def test_pre_earnings_financing_is_research_only_by_default(self):
        candidate = {
            "ticker": "ABC",
            "max_leg_spread_pct": 5,
            "min_leg_open_interest": 100,
            "min_leg_volume": 25,
            "iv_edge": 1,
            "front_expiration": "2026-06-10",
            "back_expiration": "2026-06-19",
            "earnings_event": {
                "earnings_date": "2026-06-12",
                "session_label": "Before Market Open",
                "is_timestamp_confirmed": True,
            },
        }
        ranking = {"ticker": "ABC", "action": "PASS / BACKTEST", "backtest_eligible": True}

        verdict = build_final_calendar_verdict(candidate, ranking)

        self.assertEqual(verdict["trade_type"], "pre_earnings_financing_or_directional_long_vol")
        self.assertEqual(verdict["status"], "WATCH")
        self.assertFalse(verdict["can_show_as_entry"])

    def test_trade_type_unknown_when_timestamp_unconfirmed(self):
        candidate = {
            "front_expiration": "2026-06-10",
            "back_expiration": "2026-07-10",
            "earnings_event": {"earnings_date": "2026-06-12", "session_label": "Unknown"},
        }

        result = classify_trade_type(candidate)

        self.assertEqual(result["trade_type"], "unknown_event_timing")

    def test_cpb_bmo_short_before_earnings_is_pre_earnings_financing(self):
        candidate = {
            "ticker": "CPB",
            "max_leg_spread_pct": 54.5,
            "min_leg_open_interest": 27,
            "min_leg_volume": 0,
            "front_expiration": "2026-06-05",
            "back_expiration": "2026-07-02",
            "earnings_event": {
                "earnings_date": "2026-06-08",
                "session_label": "Before market open",
                "is_timestamp_confirmed": True,
            },
        }

        verdict = build_final_calendar_verdict(candidate, {"action": "FAIL / DO NOT BACKTEST"})

        self.assertEqual(verdict["trade_type"], "pre_earnings_financing_or_directional_long_vol")
        self.assertEqual(verdict["final_verdict"], "FAIL / UNTRADEABLE SPREAD")
        self.assertEqual(verdict["main_blocker"], "options market untradeable")

    def test_mtn_amc_short_after_earnings_not_generic_not_calendar(self):
        candidate = {
            "ticker": "MTN",
            "max_leg_spread_pct": 10,
            "min_leg_open_interest": 100,
            "min_leg_volume": 20,
            "front_expiration": "2026-06-18",
            "back_expiration": "2026-07-17",
            "earnings_event": {
                "earnings_date": "2026-06-08",
                "session_label": "After market close",
                "is_timestamp_confirmed": True,
            },
        }

        result = classify_trade_type(candidate)

        self.assertIn(result["trade_type"], {"true_earnings_iv_crush_calendar", "invalid_for_earnings_strategy"})
        self.assertNotEqual(result["trade_type"], "not_an_earnings_calendar")


if __name__ == "__main__":
    unittest.main()
