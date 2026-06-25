import math
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from app.services.forward_factor_backtest_service import forward_factor_backtest_status
from app.services.forward_factor_data_eligibility_service import validate_required_data
from app.services.forward_factor_candidate_selection_service import score_forward_factor_candidate, select_forward_factor_candidates
from app.services.forward_factor_service import build_forward_factor_double_calendar_structure, build_forward_factor_strategy, build_scenario_grid, calculate_forward_factor, construct_double_calendar
from app.services.data_requirement_planner import DataRequirementPlanner
from app.services.data_requirement_service import forward_factor_requirement
from app.services.strategy_opportunity_repository import opportunity_structure_key
from app.strategies.registry import normalize_strategy_results
from app.services.run_data_context_service import create_run_data_context
from app.services.report_service import format_html
from app.providers.tradier_provider import _normalize_option


def leg(strike, option_type, delta, bid=1.0, ask=1.1, oi=100, volume=10):
    return {"strike": strike, "option_type": option_type, "delta": delta, "bid": bid, "ask": ask, "open_interest": oi, "volume": volume, "iv": .4}


class FakeFFHub:
    def __init__(self, payload):
        self.payload = payload
        now = datetime.now(timezone.utc).isoformat()
        self.quote = {"payload": {"last": 500}, "fetched_at": now, "fresh": True, "provider": "tradier", "confidence": "high"}
        self.candles = {"payload": {"bars": [{"close": 500, "volume": 10_000_000}] * 240}, "fetched_at": now, "fresh": True, "provider": "tradier", "confidence": "high"}
        self.requested_quotes = []
        self.earnings_lookaheads = []
        self.chain_set_calls = 0
    def get_quote(self, ticker, *args, **kwargs):
        self.requested_quotes.append(ticker)
        return self.quote
    def get_daily_candles(self, *args, **kwargs): return self.candles
    def get_derived_metrics(self, *args, **kwargs): return {"average_volume_30d": 10_000_000, "realized_volatility_30d": .2}
    def get_options_chain_set(self, *args, **kwargs):
        self.chain_set_calls += 1
        return {"payload": self.payload}
    def get_earnings_event(self, *args, **kwargs):
        self.earnings_lookaheads.append(kwargs.get("lookahead_days"))
        return None


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

    def test_structure_builder_explains_missing_delta_and_matching_strike(self):
        missing_delta = build_forward_factor_double_calendar_structure(
            [leg(95, "put", None), leg(105, "call", None)],
            [leg(95, "put", -.25), leg(105, "call", .25)],
        )
        self.assertEqual(missing_delta["structure_status"], "DELTA_DATA_UNAVAILABLE")
        no_match = build_forward_factor_double_calendar_structure(
            [leg(95, "put", -.35), leg(105, "call", .35)],
            [leg(100, "put", -.25), leg(105, "call", .25)],
        )
        self.assertEqual(no_match["structure_status"], "NO_MATCHED_DOUBLE_CALENDAR")
        self.assertFalse(no_match["matched_put_calendar"])
        self.assertTrue(no_match["matched_call_calendar"])

    def test_structure_builder_prices_package_and_reports_liquidity(self):
        front = [leg(95, "put", -.35, .9, 1.0), leg(105, "call", .35, .9, 1.0)]
        back = [leg(95, "put", -.25, 1.4, 1.5), leg(105, "call", .25, 1.4, 1.5)]
        row = build_forward_factor_double_calendar_structure(front, back)
        self.assertEqual(row["structure_status"], "COMPLETE")
        self.assertAlmostEqual(row["conservative_debit"], 1.2)
        self.assertAlmostEqual(row["mid_debit"], 1.0)
        self.assertAlmostEqual(row["package_bid_ask_width"], .4)
        self.assertEqual(row["liquidity_status"], "FAIL")
        self.assertTrue(row["front_put_symbol"])

    def test_structure_builder_handles_invalid_quotes_and_partial_liquidity(self):
        zero_bid = build_forward_factor_double_calendar_structure(
            [leg(95, "put", -.35, 0, 1.0), leg(105, "call", .35)],
            [leg(95, "put", -.25, 1.4, 1.5), leg(105, "call", .25, 1.4, 1.5)],
        )
        self.assertEqual(zero_bid["structure_status"], "INVALID_QUOTES")
        front = [leg(95, "put", -.35, .99, 1.0, oi=None, volume=None), leg(105, "call", .35, .99, 1.0, oi=None, volume=None)]
        back = [leg(95, "put", -.25, 1.1, 1.11, oi=None, volume=None), leg(105, "call", .25, 1.1, 1.11, oi=None, volume=None)]
        partial = build_forward_factor_double_calendar_structure(front, back)
        self.assertEqual(partial["structure_status"], "COMPLETE")
        self.assertEqual(partial["liquidity_status"], "WATCH")

    def test_structure_builder_liquidity_failures_are_explicit(self):
        cases = {
            "low_oi": (
                [leg(95, "put", -.35, .99, 1.0, oi=1), leg(105, "call", .35, .99, 1.0)],
                [leg(95, "put", -.25, 1.1, 1.11), leg(105, "call", .25, 1.1, 1.11)],
                "open interest below minimum",
            ),
            "low_volume": (
                [leg(95, "put", -.35, .99, 1.0, volume=0), leg(105, "call", .35, .99, 1.0)],
                [leg(95, "put", -.25, 1.1, 1.11), leg(105, "call", .25, 1.1, 1.11)],
                "volume below minimum",
            ),
            "wide_spread": (
                [leg(95, "put", -.35, .5, 1.0), leg(105, "call", .35, .99, 1.0)],
                [leg(95, "put", -.25, 1.1, 1.11), leg(105, "call", .25, 1.1, 1.11)],
                "bid/ask spread too wide",
            ),
        }
        for name, (front, back, blocker) in cases.items():
            with self.subTest(name=name):
                result = build_forward_factor_double_calendar_structure(front, back)
                self.assertEqual(result["liquidity_status"], "FAIL")
                self.assertTrue(any(blocker in item for item in result["liquidity_result"]["blockers"]))

    def test_structure_builder_rejects_crossed_and_negative_debit_markets(self):
        crossed = build_forward_factor_double_calendar_structure(
            [leg(95, "put", -.35, 1.1, 1.0), leg(105, "call", .35)],
            [leg(95, "put", -.25, 1.4, 1.5), leg(105, "call", .25, 1.4, 1.5)],
        )
        self.assertEqual(crossed["structure_status"], "INVALID_QUOTES")
        negative = build_forward_factor_double_calendar_structure(
            [leg(95, "put", -.35, 2.0, 2.1), leg(105, "call", .35, 2.0, 2.1)],
            [leg(95, "put", -.25, 1.0, 1.1), leg(105, "call", .25, 1.0, 1.1)],
        )
        self.assertEqual(negative["structure_status"], "INVALID_DEBIT")

    def test_backtest_blocks_without_historical_chains(self):
        self.assertEqual(forward_factor_backtest_status()["status"], "BLOCKED / HISTORICAL OPTIONS DATA UNAVAILABLE")

    def test_ff_identity_changes_with_strike(self):
        base = {"ticker": "SPY", "front_expiration": "2030-01-18", "back_expiration": "2030-02-18", "put_strike": 500, "call_strike": 550, "formula_version": "volvibes_v1"}
        self.assertNotEqual(opportunity_structure_key("forward_factor_calendar", base), opportunity_structure_key("forward_factor_calendar", {**base, "call_strike": 555}))

    def test_dry_run_pass_is_counted_but_never_actionable(self):
        front = (date.today() + timedelta(days=60)).isoformat()
        back = (date.today() + timedelta(days=90)).isoformat()
        front_chain = [leg(95, "put", -.35, .99, 1.0), leg(105, "call", .35, .99, 1.0)]
        back_chain = [leg(95, "put", -.25, 1.4, 1.41), leg(105, "call", .25, 1.4, 1.41)]
        back_iv = math.sqrt((.4 ** 2 * 30 + .48 ** 2 * 60) / 90)
        payload = {"expirations": [front, back], "chains": {front: front_chain, back: back_chain}, "expiration_metrics": {front: {"ex_earnings_iv": .48}, back: {"ex_earnings_iv": back_iv}}}
        hub = FakeFFHub(payload)
        raw = build_forward_factor_strategy(["SPY"], {"SPY": {"required_market_data_complete": True, "current_price": 500, "average_volume_30d": 10000000}}, hub)
        row = raw["items"][0]
        self.assertEqual(row["verdict"], "SOURCE-QUALIFIED POSITIVE FF SIGNAL / REVIEW ENTRY")
        self.assertEqual(row["actionability_score"], 0)
        self.assertFalse(row["can_enter_daily_opportunity"])
        normalized = normalize_strategy_results(create_run_data_context(), {"forward_factor_calendar": raw})["forward_factor_calendar"]
        self.assertEqual(normalized["pass_count"], 1)

    def test_dashboard_renders_ff_dry_bubble_section_and_export(self):
        result = {"strategy_id": "forward_factor_calendar", "strategy_label": "Forward Factor Calendar", "version": "v1", "enabled": True, "ran": True, "rows": [{"ticker": "SPY", "verdict": "FAIL / EX-EARNINGS IV UNAVAILABLE", "actionability_score": 0}], "pass_count": 0, "watch_count": 0, "fail_count": 1, "skipped_count": 0}
        html = format_html("payload", [], {}, [], {"_strategy_results": {"forward_factor_calendar": result}, "_pipeline_status": {"mode": "dev", "steps": []}}, [])
        self.assertIn("FF DRY", html)
        self.assertIn("Forward Factor Calendar Candidates", html)
        self.assertIn("Copy Forward Factor Report", html)

    def test_clean_expiration_derives_ex_iv_from_raw_iv_path_b(self):
        front = (date.today() + timedelta(days=60)).isoformat()
        back = (date.today() + timedelta(days=90)).isoformat()
        payload = {"expirations": [front, back], "chains": {
            front: [leg(95, "put", -.35, .99, 1.0), leg(105, "call", .35, .99, 1.0)],
            back: [leg(95, "put", -.25, 1.2, 1.21), leg(105, "call", .25, 1.2, 1.21)],
        }, "expiration_metrics": {front: {"raw_iv": .50}, back: {"raw_iv": .45}}}
        result = build_forward_factor_strategy(["SPY"], {}, FakeFFHub(payload))
        row = result["items"][0]
        self.assertEqual(row["verdict"], "SOURCE-QUALIFIED POSITIVE FF SIGNAL / REVIEW ENTRY")
        self.assertIsNotNone(row.get("forward_factor"))
        self.assertEqual(row["front_iv_derivation_method"], "path_b_clean")
        self.assertEqual(row["back_iv_derivation_method"], "path_b_clean")
        self.assertEqual(row["structure_status"], "COMPLETE")
        self.assertIsNotNone(row["put_strike"])
        self.assertEqual(row["actionability_score"], 0)
        self.assertIsNotNone(row["T1"])
        self.assertEqual(result["stage_counts"]["structure_attempts"], 1)
        self.assertEqual(result["stage_counts"]["structures"], 1)
        self.assertEqual(result["stage_counts"]["source_ff_calculated"], 1)
        self.assertEqual(result["stage_counts"]["ff_calculated"], 1)
        self.assertTrue(result["summary"]["counts_reconcile"])

    def test_derived_iv_equal_front_back_below_threshold_when_no_delta(self):
        front = (date.today() + timedelta(days=60)).isoformat()
        back = (date.today() + timedelta(days=90)).isoformat()
        payload = {"expirations": [front, back], "chains": {
            front: [leg(95, "put", None), leg(105, "call", None)],
            back: [leg(95, "put", None), leg(105, "call", None)],
        }}
        row = build_forward_factor_strategy(["SPY"], {}, FakeFFHub(payload))["items"][0]
        self.assertEqual(row["verdict"], "FAIL / FORWARD FACTOR BELOW THRESHOLD")
        self.assertEqual(row["actionability_score"], 0)

    def test_source_qualified_below_threshold_reaches_numeric_terminal_result(self):
        front = (date.today() + timedelta(days=60)).isoformat()
        back = (date.today() + timedelta(days=90)).isoformat()
        payload = {
            "expirations": [front, back],
            "chains_by_expiration": {
                front: [leg(95, "put", -.35), leg(105, "call", .35)],
                back: [leg(95, "put", -.25), leg(105, "call", .25)],
            },
            "expiration_metrics": {
                front: {"ex_earnings_iv": .40},
                back: {"ex_earnings_iv": .40},
            },
        }
        hub = FakeFFHub(payload)
        result = build_forward_factor_strategy(["SPY"], {}, hub, run_mode="prod")
        row = result["items"][0]
        self.assertEqual(row["verdict"], "FAIL / FORWARD FACTOR BELOW THRESHOLD")
        self.assertAlmostEqual(row["forward_factor"], 0.0)
        self.assertEqual(result["stage_counts"]["chain_sets"], 1)
        self.assertEqual(result["stage_counts"]["expiration_pairs"], 1)
        self.assertEqual(result["stage_counts"]["ff_calculated"], 1)
        self.assertEqual(hub.earnings_lookaheads, [120])
        self.assertTrue(result["summary"]["counts_reconcile"])

    def test_scenario_grid_is_model_estimate(self):
        rows = build_scenario_grid(100, 95, 105, 2, 30, .3)
        self.assertEqual(len(rows), 11)
        self.assertTrue(all("MODEL ESTIMATE" in row["label"] for row in rows))

    def test_tradier_normalization_preserves_explicit_ex_earnings_iv(self):
        row = _normalize_option({"strike": 100, "option_type": "call", "greeks": {"delta": .35, "mid_iv": .5, "ex_earnings_iv": .42}}, "SPY", "2030-01-18")
        self.assertEqual(row["iv"], .5)
        self.assertEqual(row["ex_earnings_iv"], .42)

    def test_fresh_shared_records_pass_ff_eligibility_without_stock_trend_metrics(self):
        hub = FakeFFHub({})
        result = validate_required_data(hub.quote, hub.candles, {"average_volume_30d": 10_000_000})
        self.assertTrue(result["eligible"])
        self.assertEqual(result["data_state"], "COMPLETE")

    def test_timezone_naive_timestamp_does_not_false_stale(self):
        hub = FakeFFHub({})
        hub.quote["fetched_at"] = datetime.now().isoformat()
        result = validate_required_data(hub.quote, hub.candles, {"average_volume_30d": 10_000_000})
        self.assertTrue(result["eligible"])

    def test_missing_volume_names_exact_field(self):
        hub = FakeFFHub({})
        result = validate_required_data(hub.quote, {"payload": {"bars": [{"close": 500}] * 240}, "fresh": True}, {})
        self.assertFalse(result["eligible"])
        self.assertIn("average_volume_30d", result["missing_fields"])

    def test_planned_provider_budget_is_skip_not_stale(self):
        result = validate_required_data(None, None, {}, planned_state="SKIPPED_PROVIDER_BUDGET")
        self.assertEqual(result["data_state"], "SKIPPED_PROVIDER_BUDGET")

    def test_average_volume_is_calculated_from_candles_before_eligibility(self):
        hub = FakeFFHub({})
        result = validate_required_data(hub.quote, hub.candles, {})
        self.assertEqual(result["average_volume_30d"], 10_000_000)
        self.assertTrue(result["average_volume_pass"])
        self.assertEqual(result["minimum_average_volume"], 1_000_000)

    def test_low_price_and_volume_are_threshold_failures_not_unsupported(self):
        hub = FakeFFHub({})
        low_price = validate_required_data({"payload": {"last": 5}, "fresh": True}, hub.candles, {"average_volume_30d": 10_000_000})
        low_volume = validate_required_data(hub.quote, hub.candles, {"average_volume_30d": 10})
        self.assertEqual(low_price["data_state"], "PRICE_BELOW_MINIMUM")
        self.assertEqual(low_volume["data_state"], "AVERAGE_VOLUME_BELOW_MINIMUM")
        self.assertEqual(low_volume["missing_fields"], [])

    def test_known_low_price_ticker_does_not_consume_dev_slot(self):
        hub = FakeFFHub({})
        metrics = {
            "AAA": {"has_data": True, "current_price": 5, "average_volume_30d": 10_000_000},
            "BBB": {"has_data": True, "current_price": 50, "average_volume_30d": 10_000_000},
            "CCC": {"has_data": True, "current_price": 60, "average_volume_30d": 10_000_000},
            "DDD": {"has_data": True, "current_price": 70, "average_volume_30d": 10_000_000},
        }
        result = build_forward_factor_strategy(["AAA", "BBB", "CCC", "DDD"], metrics, hub, run_mode="dev")
        self.assertNotIn("AAA", hub.requested_quotes)
        self.assertEqual(result["stage_counts"]["cheap_evaluated"], 3)

    def test_candidate_quality_prioritizes_valid_pair_and_penalizes_repeat_failures(self):
        valid = score_forward_factor_candidate("ELF", {
            "current_price": 50, "average_volume_30d": 2_000_000, "options_available": True,
        }, {"valid_pair_seen": True, "structure_seen": True, "best_liquidity_status": "FAIL"})
        repeated = score_forward_factor_candidate("CRDO", {
            "current_price": 50, "average_volume_30d": 2_000_000, "options_available": True,
        }, {"failure_modes": {"NO_ELIGIBLE_EXPIRATION_PAIR": 3}})
        self.assertGreater(valid["score"], repeated["score"])
        self.assertTrue(valid["cached_pair_seen"])
        self.assertTrue(any("repeated" in warning.lower() for warning in repeated["warnings"]))

    def test_candidate_discovery_pool_is_broader_than_final_selection(self):
        tickers = [f"T{i:02d}" for i in range(8)]
        metrics = {ticker: {"current_price": 50, "average_volume_30d": 2_000_000, "options_available": True} for ticker in tickers}
        selected, audit = select_forward_factor_candidates(tickers, metrics, {}, discovery_pool_size=6, final_cap=3)
        self.assertEqual(len(selected), 3)
        self.assertEqual(sum(row["selected_for_discovery_pool"] for row in audit), 6)
        self.assertEqual(sum(row["selected_for_cheap_eval"] for row in audit), 3)

    def test_repeat_no_pair_candidate_is_deprioritized_when_alternative_exists(self):
        metrics = {
            ticker: {"current_price": 50, "average_volume_30d": 2_000_000, "options_available": True}
            for ticker in ("CRDO", "ELF", "NVDA")
        }
        selected, _ = select_forward_factor_candidates(
            list(metrics), metrics,
            {"CRDO": {"failure_modes": {"NO_ELIGIBLE_EXPIRATION_PAIR": 4}}, "ELF": {"valid_pair_seen": True}},
            discovery_pool_size=3, final_cap=2,
        )
        self.assertIn("ELF", selected)
        self.assertNotIn("CRDO", selected)

    def test_final_selector_excludes_planner_skipped_high_score_candidates(self):
        tickers = ["ELF", "AMZN", "FANUY", "FSLR", "CRDO", "LULU", "METU"]
        metrics = {
            ticker: {"current_price": 50, "average_volume_30d": 2_000_000, "options_available": True}
            for ticker in tickers
        }
        history = {"ELF": {"valid_pair_seen": True}}
        planner_states = {
            "ELF": "APPROVED", "AMZN": "SKIPPED_DEV_CAP", "FANUY": "SKIPPED_DEV_CAP",
            "FSLR": "SKIPPED_DEV_CAP", "CRDO": "APPROVED", "LULU": "APPROVED", "METU": "APPROVED",
        }
        selected, audit = select_forward_factor_candidates(
            tickers, metrics, history, discovery_pool_size=12, final_cap=4, planner_states=planner_states,
        )
        self.assertEqual(selected, ["ELF", "CRDO", "LULU", "METU"])
        by_ticker = {row["ticker"]: row for row in audit}
        self.assertFalse(by_ticker["AMZN"]["selected_for_cheap_eval"])
        self.assertIn("SKIPPED_DEV_CAP", by_ticker["AMZN"]["not_selected_reason"])
        self.assertTrue(by_ticker["AMZN"]["selected_for_discovery_pool"])

    def test_selector_does_not_fill_slots_when_only_two_planner_candidates_are_approved(self):
        tickers = ["ELF", "CRDO", "AMZN", "FSLR"]
        metrics = {
            ticker: {"current_price": 50, "average_volume_30d": 2_000_000, "options_available": True}
            for ticker in tickers
        }
        selected, audit = select_forward_factor_candidates(
            tickers, metrics, {}, discovery_pool_size=12, final_cap=4,
            planner_states={"ELF": "APPROVED", "CRDO": "APPROVED", "AMZN": "SKIPPED_DEV_CAP", "FSLR": "SKIPPED_DEV_CAP"},
        )
        self.assertEqual(selected, ["CRDO", "ELF"])
        self.assertEqual(sum(row["planner_approved"] for row in audit), 2)

    def test_planner_aligned_service_selection_has_no_pre_eval_dev_cap_skips(self):
        tickers = ["ELF", "AMZN", "FANUY", "FSLR", "CRDO", "LULU", "METU"]
        metrics = {
            ticker: {"current_price": 50, "average_volume_30d": 2_000_000, "options_available": True}
            for ticker in tickers
        }
        approved = {"ELF", "CRDO", "LULU", "METU"}
        plan = {"by_ticker": {
            ticker: {"state": "APPROVED" if ticker in approved else "SKIPPED_DEV_CAP"}
            for ticker in tickers
        }, "forward_factor_chain_reserve": 4}
        logs = []
        with patch("app.config.FF_DEV_MAX_TICKERS_PER_RUN", 4), patch("app.config.FF_DEV_MAX_CHAIN_TICKERS_PER_RUN", 4):
            result = build_forward_factor_strategy(
                tickers, metrics, FakeFFHub({"expirations": [], "chains": {}}), run_mode="dev", requirement_plan=plan,
                observation_history={"ELF": {"valid_pair_seen": True}}, log_print=logs.append,
            )
        self.assertEqual(result["stage_counts"]["final_selected"], 4)
        self.assertEqual(result["stage_counts"]["planner_approved_candidates"], 4)
        self.assertEqual(result["stage_counts"]["pre_eval_skipped"], 0)
        self.assertEqual(result["stage_counts"]["cheap_evaluated"], 4)
        self.assertEqual(result["stage_counts"]["chain_approved"], 4)
        self.assertEqual(result["stage_counts"]["chain_sets"], 4)
        self.assertTrue(any("FF selector validation: final_selected=4 approved=4" in line for line in logs))
        self.assertTrue(any("FF evaluation reconciliation: final_selected=4 evaluated=4 pre_eval_skipped=0" in line for line in logs))
        self.assertTrue(any("FF chain reconciliation: cheap_pass=4 chain_approved=4 chain_skipped_budget=0 chain_sets=4" in line for line in logs))

    def test_approved_ff_tickers_reach_cheap_evaluation_and_chain_set(self):
        tickers = ["AMZN", "CRDO", "ELF"]
        plan = DataRequirementPlanner("dev", dev_ticker_cap=6).merge([forward_factor_requirement(tickers)])
        self.assertTrue(all(plan["by_ticker"][ticker]["state"] == "APPROVED" for ticker in tickers))
        hub = FakeFFHub({})
        result = build_forward_factor_strategy(tickers, {}, hub, run_mode="dev", requirement_plan=plan)
        self.assertGreater(result["stage_counts"]["cheap_evaluated"], 0)
        self.assertGreater(result["stage_counts"]["cheap_pass"], 0)
        self.assertGreater(hub.chain_set_calls, 0)

    def test_planner_blocked_ticker_is_excluded_before_evaluation_and_reconciled(self):
        tickers = ["AMZN", "CRDO", "ELF"]
        plan = DataRequirementPlanner("dev", dev_ticker_cap=2).merge([forward_factor_requirement(tickers)])
        logs = []
        result = build_forward_factor_strategy(tickers, {}, FakeFFHub({}), run_mode="dev", requirement_plan=plan, log_print=logs.append)
        self.assertTrue(any(row["ticker"] == "ELF" and row["verdict"] == "SKIPPED / DEV CAP" for row in result["items"]))
        self.assertEqual(result["stage_counts"]["planner_blocked"], 0)
        self.assertEqual(result["stage_counts"]["pre_eval_skipped"], 0)
        self.assertEqual(result["stage_counts"]["cheap_evaluated"], 2)
        self.assertTrue(result["summary"]["counts_reconcile"])
        self.assertTrue(any("FF selector validation: final_selected=2 approved=2" in line for line in logs))

    def test_crypto_is_reserved_as_unsupported_security(self):
        hub = FakeFFHub({})
        result = build_forward_factor_strategy(["BTC"], {"BTC": {"asset_type": "crypto", "current_price": 100, "average_volume_30d": 10_000_000}}, hub, run_mode="dev")
        self.assertEqual(result["items"][0]["verdict"], "FAIL / UNSUPPORTED SECURITY")
        self.assertEqual(hub.requested_quotes, [])

    def test_production_cap_is_mode_aware_and_terminal_counts_reconcile(self):
        hub = FakeFFHub({})
        tickers = [f"T{i:02d}" for i in range(12)] + ["BTC", "SOL"]
        result = build_forward_factor_strategy(tickers, {"BTC": {"asset_type": "crypto"}, "SOL": {"asset_type": "crypto"}}, hub, run_mode="prod")
        verdicts = [row["verdict"] for row in result["items"]]
        self.assertIn("SKIPPED / STRATEGY CAP", verdicts)
        self.assertNotIn("SKIPPED / DEV CAP", verdicts)
        self.assertEqual(result["stage_counts"]["unsupported"], 2)
        self.assertEqual(result["summary"]["terminal_count"], len(tickers))
        self.assertTrue(result["summary"]["counts_reconcile"])

    def test_dev_cap_rows_are_collapsed_in_ff_ui(self):
        rows = [{"ticker": f"T{i}", "verdict": "SKIPPED / DEV CAP", "actionability_score": 0} for i in range(5)]
        result = {"strategy_id": "forward_factor_calendar", "strategy_label": "Forward Factor Calendar", "enabled": True, "rows": rows, "pass_count": 0, "watch_count": 0, "fail_count": 0, "skipped_count": 5, "summary": {"stage_counts": {}}}
        html = format_html("payload", [], {}, [], {"_strategy_results": {"forward_factor_calendar": result}, "_pipeline_status": {"mode": "dev", "steps": []}}, [])
        self.assertIn("Skipped by dev cap: 5", html)
        self.assertNotIn("<strong>T0</strong>", html)

    def test_diagnostic_structure_fields_render_in_dashboard_and_export(self):
        row = {
            "ticker": "ELF", "verdict": "WATCH / EX-EARNINGS IV UNAVAILABLE", "diagnostic_only": True,
            "signal_tier": "DIAGNOSTIC_POSITIVE", "is_positive_signal": True, "is_source_qualified": False,
            "is_diagnostic_only": True, "is_trade_review_candidate": True, "can_enter_daily_opportunity": False,
            "positive_reasons": ["FF above threshold"], "warnings": ["Source unavailable"], "blockers": [],
            "diagnostic_raw_iv_forward_factor": .3118, "T1": .16, "T2": .25, "forward_variance": .3, "forward_iv": .55,
            "put_strike": 60, "call_strike": 65, "front_put_delta": -.34, "front_call_delta": .36,
            "front_put_symbol": "ELF-FP", "back_put_symbol": "ELF-BP", "front_call_symbol": "ELF-FC", "back_call_symbol": "ELF-BC",
            "conservative_debit": 2.1, "mid_debit": 1.9, "debit_at_risk": 210, "package_slippage_pct": 10.5,
            "liquidity_status": "WATCH", "liquidity_result": {"status": "WATCH"}, "actionability_score": 0,
        }
        result = {"strategy_id": "forward_factor_calendar", "strategy_label": "Forward Factor Calendar", "enabled": True, "rows": [row], "pass_count": 0, "watch_count": 1, "fail_count": 0, "skipped_count": 0, "summary": {"stage_counts": {"structure_attempts": 1, "structures": 1}}}
        html = format_html("payload", [], {}, [], {"_strategy_results": {"forward_factor_calendar": result}, "_pipeline_status": {"mode": "dev", "steps": []}}, [])
        self.assertIn("DIAGNOSTIC ONLY", html)
        self.assertIn("ELF-FP", html)
        self.assertIn("Conservative / Mid Debit", html)
        self.assertIn("Structure attempts", html)
        self.assertIn("forward_variance", html)
        self.assertIn("Positive / Signal Tier", html)
        self.assertIn("DIAGNOSTIC_POSITIVE", html)

    def test_candidate_selection_audit_renders_and_nonpositive_ff_actionability_is_zero(self):
        front = (date.today() + timedelta(days=60)).isoformat()
        back = (date.today() + timedelta(days=90)).isoformat()
        payload = {"expirations": [front, back], "chains": {
            front: [leg(95, "put", -.35, .5, 1.0), leg(105, "call", .35, .5, 1.0)],
            back: [leg(95, "put", -.25, 1.4, 1.5), leg(105, "call", .25, 1.4, 1.5)],
        }, "expiration_metrics": {front: {"raw_iv": .50}, back: {"raw_iv": .45}}}
        raw = build_forward_factor_strategy(
            ["ELF"], {"ELF": {"current_price": 50, "average_volume_30d": 2_000_000, "options_available": True}},
            FakeFFHub(payload), observation_history={"ELF": {"valid_pair_seen": True, "structure_seen": True}},
        )
        normalized = normalize_strategy_results(create_run_data_context(), {"forward_factor_calendar": raw})["forward_factor_calendar"]
        row = normalized["rows"][0]
        self.assertEqual(row["actionability_score"], 0)
        self.assertGreater(row["candidate_quality_score"], 0)
        self.assertTrue(row["what_would_make_positive"])
        html = format_html("payload", [], {}, [], {"_strategy_results": {"forward_factor_calendar": normalized}, "_pipeline_status": {"mode": "dev", "steps": []}}, [])
        self.assertIn("FF Candidate Selection Audit", html)
        self.assertIn("Candidate Quality", html)
        self.assertIn("what_would_make_positive", html)


if __name__ == "__main__":
    unittest.main()
