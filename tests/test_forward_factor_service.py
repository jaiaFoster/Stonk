import math
import unittest
from datetime import date, timedelta

from app.services.forward_factor_backtest_service import forward_factor_backtest_status
from app.services.forward_factor_service import build_forward_factor_strategy, calculate_forward_factor, construct_double_calendar
from app.services.strategy_opportunity_repository import opportunity_structure_key
from app.strategies.registry import normalize_strategy_results
from app.services.run_data_context_service import create_run_data_context
from app.services.report_service import format_html


def leg(strike, option_type, delta, bid=1.0, ask=1.1, oi=100, volume=10):
    return {"strike": strike, "option_type": option_type, "delta": delta, "bid": bid, "ask": ask, "open_interest": oi, "volume": volume}


class ForwardFactorTests(unittest.TestCase):
    def test_known_valid_forward_volatility_example(self):
        result = calculate_forward_factor(.50, .45, 60, 90)
        expected_variance = ((.45 ** 2) * (90 / 365) - (.50 ** 2) * (60 / 365)) / ((90 - 60) / 365)
        self.assertAlmostEqual(result["forward_variance"], expected_variance)
        self.assertAlmostEqual(result["forward_iv"], math.sqrt(expected_variance))

    def test_exact_point_two_forward_factor(self):
        result = calculate_forward_factor(.48, math.sqrt((.4 ** 2 * 30 + .48 ** 2 * 60) / 90), 60, 90)
        self.assertAlmostEqual(result["forward_factor"], .20, places=8)

    def test_invalid_time_and_variance_and_percentage_units(self):
        with self.assertRaises(ValueError):
            calculate_forward_factor(.5, .5, 60, 60)
        with self.assertRaises(ValueError):
            calculate_forward_factor(.8, .2, 60, 90)
        with self.assertRaises(ValueError):
            calculate_forward_factor(50, 45, 60, 90)

    def test_constructs_matched_delta_double_calendar(self):
        front = [leg(95, "put", -.35, .9, 1.0), leg(105, "call", .35, .9, 1.0)]
        back = [leg(95, "put", -.25, 1.4, 1.5), leg(105, "call", .25, 1.4, 1.5)]
        row = construct_double_calendar(front, back)
        self.assertEqual(row["put_strike"], 95)
        self.assertEqual(row["call_strike"], 105)
        self.assertAlmostEqual(row["conservative_debit"], 1.2)

    def test_missing_matching_back_strike_rejected(self):
        self.assertIsNone(construct_double_calendar([leg(95, "put", -.35), leg(105, "call", .35)], [leg(100, "put", -.25), leg(105, "call", .25)]))

    def test_backtest_blocks_without_historical_chains(self):
        self.assertEqual(forward_factor_backtest_status()["status"], "BLOCKED / HISTORICAL OPTIONS DATA UNAVAILABLE")

    def test_ff_identity_changes_with_strike(self):
        base = {"ticker": "SPY", "front_expiration": "2030-01-18", "back_expiration": "2030-02-18", "put_strike": 500, "call_strike": 550, "formula_version": "volvibes_v1"}
        self.assertNotEqual(opportunity_structure_key("forward_factor_calendar", base), opportunity_structure_key("forward_factor_calendar", {**base, "call_strike": 555}))

    def test_dry_run_pass_is_counted_but_never_actionable(self):
        front = (date.today() + timedelta(days=60)).isoformat()
        back = (date.today() + timedelta(days=90)).isoformat()
        front_chain = [leg(95, "put", -.35, .95, 1.0), leg(105, "call", .35, .95, 1.0)]
        back_chain = [leg(95, "put", -.25, 1.4, 1.45), leg(105, "call", .25, 1.4, 1.45)]
        back_iv = math.sqrt((.4 ** 2 * 30 + .48 ** 2 * 60) / 90)
        payload = {"expirations": [front, back], "chains": {front: front_chain, back: back_chain}, "expiration_metrics": {front: {"ex_earnings_iv": .48}, back: {"ex_earnings_iv": back_iv}}}
        hub = type("Hub", (), {"get_options_chain": lambda *args, **kwargs: {"payload": payload}})()
        raw = build_forward_factor_strategy(["SPY"], {"SPY": {"required_market_data_complete": True, "current_price": 500, "average_volume_30d": 10000000}}, hub)
        row = raw["items"][0]
        self.assertTrue(row["verdict"].startswith("DRY RUN PASS"))
        self.assertEqual(row["actionability_score"], 0)
        normalized = normalize_strategy_results(create_run_data_context(), {"forward_factor_calendar": raw})["forward_factor_calendar"]
        self.assertEqual(normalized["pass_count"], 1)

    def test_dashboard_renders_ff_dry_bubble_section_and_export(self):
        result = {"strategy_id": "forward_factor_calendar", "strategy_label": "Forward Factor Calendar", "version": "v1", "enabled": True, "ran": True, "rows": [{"ticker": "SPY", "verdict": "FAIL / EX-EARNINGS IV UNAVAILABLE", "actionability_score": 0}], "pass_count": 0, "watch_count": 0, "fail_count": 1, "skipped_count": 0}
        html = format_html("payload", [], {}, [], {"_strategy_results": {"forward_factor_calendar": result}, "_pipeline_status": {"mode": "dev", "steps": []}}, [])
        self.assertIn("FF DRY", html)
        self.assertIn("Forward Factor Calendar Candidates", html)
        self.assertIn("Copy Forward Factor Report", html)


if __name__ == "__main__":
    unittest.main()
