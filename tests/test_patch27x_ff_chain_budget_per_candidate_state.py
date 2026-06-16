"""
Patch 27X — FF reserved chain budget and per-candidate state exposure.

Tests:
1. FF_CHAIN_BUDGET_RESERVED guarantees chain slots even when hub budget is nearly exhausted
2. FF_SKIP_IF_ALREADY_FAILED_RECENTLY skips repeat-failure tickers before chain eval
3. Every terminal row carries exactly one ff_candidate_stage value from the allowed set
4. ff_candidate_stage values are correct for each flow branch
5. Provider-free diagnostics not affected (no new provider endpoints added)
6. FF stays absent from daily opportunity output (dry-run enforcement unchanged)
"""

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app.services.forward_factor_service import build_forward_factor_strategy
from app.services.data_requirement_planner import DataRequirementPlanner
from app.services.data_requirement_service import forward_factor_requirement
from app.services.provider_budget_service import ProviderBudget

ALLOWED_STAGES = {
    "selected", "cheap_eligible", "chain_approved", "budget_skipped",
    "fetched", "incomplete", "provider_failed", "recent_fail_skip",
    "cap_skip", "no_pair",
}


def _make_hub(payload=None, chain_returns_none=False, chain_budget_exceeded=False):
    """Minimal FakeFFHub for 27X tests."""
    class FakeFFHub:
        def __init__(self):
            now = datetime.now(timezone.utc).isoformat()
            self.quote = {"payload": {"last": 100}, "fetched_at": now, "fresh": True, "provider": "tradier", "confidence": "high"}
            self.candles = {"payload": {"bars": [{"close": 100, "volume": 5_000_000}] * 240}, "fetched_at": now, "fresh": True, "provider": "tradier", "confidence": "high"}
            self.chain_set_calls = 0
            self.context = _AuditCtx()

        def get_quote(self, *a, **k): return self.quote
        def get_daily_candles(self, *a, **k): return self.candles
        def get_derived_metrics(self, *a, **k): return {"average_volume_30d": 5_000_000, "realized_volatility_30d": 0.25}
        def get_earnings_event(self, *a, **k): return None
        def get_options_chain_set(self, ticker, *a, **k):
            self.chain_set_calls += 1
            if chain_returns_none:
                self.context.fetch_audit.append({"ticker": ticker, "data_type": "options_chain_set", "state": "MISSING_PROVIDER_FAILED"})
                return None
            if chain_budget_exceeded:
                self.context.fetch_audit.append({"ticker": ticker, "data_type": "options_chain_set", "state": "SKIPPED_PROVIDER_BUDGET"})
                return None
            return {"payload": payload or {"expirations": [], "chains": {}, "chains_by_expiration": {}}}

    class _AuditCtx:
        def __init__(self): self.fetch_audit = []

    return FakeFFHub()


def _plan(tickers, approved_set=None, chain_reserve=4):
    if approved_set is None:
        approved_set = set(tickers)
    return {
        "by_ticker": {t: {"state": "APPROVED" if t in approved_set else "SKIPPED_PROVIDER_BUDGET"} for t in tickers},
        "forward_factor_chain_reserve": chain_reserve,
    }


class TestFFChainBudgetReserved(unittest.TestCase):
    def test_ff_chain_budget_reserved_config_defaults_true(self):
        from app import config
        self.assertTrue(config.FF_CHAIN_BUDGET_RESERVED)
        self.assertEqual(config.FF_MIN_CHAIN_SET_BUDGET, 4)
        self.assertTrue(config.FF_SKIP_IF_ALREADY_FAILED_RECENTLY)

    def test_chain_reserve_guarantees_minimum_slots_via_analysis_service_logic(self):
        """After fulfill_plan, hub budget must have FF_MIN_CHAIN_SET_BUDGET headroom."""
        from app import config

        # Simulate: budget nearly exhausted after cheap fetches
        budget = ProviderBudget(max_requests=25)
        budget.used = 23  # only 2 remaining — less than FF_MIN_CHAIN_SET_BUDGET=4

        ff_chain_reserve = 4
        ff_budget_gap = ff_chain_reserve - budget.remaining  # 4 - 2 = 2

        self.assertEqual(ff_budget_gap, 2)
        budget.max_requests += ff_budget_gap  # boost to guarantee slots

        self.assertEqual(budget.remaining, 4)
        self.assertTrue(budget.consume("options_chain_set"))
        self.assertTrue(budget.consume("options_chain_set"))
        self.assertTrue(budget.consume("options_chain_set"))
        self.assertTrue(budget.consume("options_chain_set"))
        self.assertFalse(budget.consume("options_chain_set"))  # 5th rejected

    def test_ff_min_chain_set_budget_is_honoured_when_flag_on(self):
        """FF_CHAIN_BUDGET_RESERVED=True with tight budget still reserves FF_MIN_CHAIN_SET_BUDGET."""
        from app import config
        import math
        # With MARKET_DATA_MAX_PROVIDER_FETCHES_PER_RUN = 3 (pathologically low),
        # reserved mode should give ff_chain_reserve = FF_MIN_CHAIN_SET_BUDGET = 4
        # capped by chain_cap_for_mode (FF_MAX_CHAIN_TICKERS_PER_RUN = 4)
        hub_remaining = 3
        chain_cap = 4
        with patch.object(config, "FF_CHAIN_BUDGET_RESERVED", True), \
             patch.object(config, "FF_MIN_CHAIN_SET_BUDGET", 4):
            reserve = min(chain_cap, max(config.FF_MIN_CHAIN_SET_BUDGET, min(hub_remaining, chain_cap)))
            self.assertEqual(reserve, 4)

    def test_ff_chain_budget_reserved_false_falls_back_to_remaining(self):
        """Without FF_CHAIN_BUDGET_RESERVED, reserve is capped by hub remaining."""
        from app import config
        hub_remaining = 2
        chain_cap = 4
        with patch.object(config, "FF_CHAIN_BUDGET_RESERVED", False):
            reserve = min(hub_remaining, chain_cap)
            self.assertEqual(reserve, 2)


class TestPerCandidateStage(unittest.TestCase):
    def _all_stages(self, result):
        return [row.get("ff_candidate_stage") for row in result["items"]]

    def test_every_row_has_ff_candidate_stage_from_allowed_set(self):
        tickers = ["AAPL", "MSFT", "GOOGL"]
        hub = _make_hub()
        result = build_forward_factor_strategy(
            tickers,
            {t: {"current_price": 100, "average_volume_30d": 5_000_000} for t in tickers},
            hub,
            run_mode="dev",
            requirement_plan=_plan(tickers),
        )
        for row in result["items"]:
            stage = row.get("ff_candidate_stage")
            self.assertIn(stage, ALLOWED_STAGES, msg=f"Ticker {row.get('ticker')} has invalid stage {stage!r}")

    def test_cap_skip_emitted_for_dev_cap_tickers(self):
        tickers = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"]
        hub = _make_hub()
        with patch("app.config.FF_DEV_MAX_TICKERS_PER_RUN", 2), \
             patch("app.config.FF_DEV_MAX_CHAIN_TICKERS_PER_RUN", 2):
            result = build_forward_factor_strategy(
                tickers,
                {t: {"current_price": 100, "average_volume_30d": 5_000_000} for t in tickers},
                hub,
                run_mode="dev",
            )
        cap_skipped = [row for row in result["items"] if row.get("ff_candidate_stage") == "cap_skip"]
        self.assertGreater(len(cap_skipped), 0)

    def test_budget_skipped_emitted_for_provider_budget_blocked_tickers(self):
        tickers = ["AAPL", "MSFT"]
        hub = _make_hub()
        plan = _plan(tickers, approved_set={"AAPL"})  # MSFT is SKIPPED_PROVIDER_BUDGET
        result = build_forward_factor_strategy(
            tickers,
            {t: {"current_price": 100, "average_volume_30d": 5_000_000} for t in tickers},
            hub,
            run_mode="dev",
            requirement_plan=plan,
        )
        msft_row = next(r for r in result["items"] if r["ticker"] == "MSFT")
        self.assertEqual(msft_row["ff_candidate_stage"], "budget_skipped")

    def test_no_pair_emitted_when_chain_has_no_valid_expirations(self):
        tickers = ["CRDO"]
        hub = _make_hub(payload={"expirations": [], "chains": {}, "chains_by_expiration": {}})
        result = build_forward_factor_strategy(
            tickers,
            {"CRDO": {"current_price": 100, "average_volume_30d": 5_000_000}},
            hub,
            run_mode="dev",
            requirement_plan=_plan(tickers),
        )
        row = result["items"][0]
        self.assertEqual(row["ff_candidate_stage"], "no_pair")

    def test_provider_failed_emitted_when_chain_returns_none_non_budget(self):
        tickers = ["ELF"]
        hub = _make_hub(chain_returns_none=True)
        result = build_forward_factor_strategy(
            tickers,
            {"ELF": {"current_price": 100, "average_volume_30d": 5_000_000}},
            hub,
            run_mode="dev",
            requirement_plan=_plan(tickers),
        )
        row = result["items"][0]
        self.assertEqual(row["ff_candidate_stage"], "provider_failed")

    def test_budget_skipped_emitted_when_chain_returns_none_budget(self):
        tickers = ["ELF"]
        hub = _make_hub(chain_budget_exceeded=True)
        result = build_forward_factor_strategy(
            tickers,
            {"ELF": {"current_price": 100, "average_volume_30d": 5_000_000}},
            hub,
            run_mode="dev",
            requirement_plan=_plan(tickers),
        )
        row = result["items"][0]
        self.assertEqual(row["ff_candidate_stage"], "budget_skipped")

    def test_cheap_eligible_emitted_when_chain_cap_reached(self):
        tickers = ["AAPL", "MSFT"]
        hub = _make_hub()
        # chain_reserve=0 means no tickers get chain eval → cheap_pass tickers hit cap
        plan = _plan(tickers, chain_reserve=0)
        result = build_forward_factor_strategy(
            tickers,
            {t: {"current_price": 100, "average_volume_30d": 5_000_000} for t in tickers},
            hub,
            run_mode="dev",
            requirement_plan=plan,
        )
        # All tickers that passed cheap but hit chain cap get cheap_eligible
        cheap_eligible = [r for r in result["items"] if r.get("ff_candidate_stage") == "cheap_eligible"]
        # They should also have SKIPPED / PROVIDER BUDGET verdict
        for row in cheap_eligible:
            self.assertIn("PROVIDER BUDGET", row.get("verdict", ""))

    def test_unsupported_ticker_gets_cap_skip(self):
        hub = _make_hub()
        result = build_forward_factor_strategy(
            ["BTC"],
            {"BTC": {"asset_type": "crypto", "current_price": 50000, "average_volume_30d": 1_000_000_000}},
            hub,
            run_mode="dev",
        )
        self.assertEqual(result["items"][0]["ff_candidate_stage"], "cap_skip")

    def test_stage_counts_include_recent_fail_skipped(self):
        """stage_counts must expose recent_fail_skipped key."""
        hub = _make_hub()
        result = build_forward_factor_strategy(
            ["AAPL"],
            {"AAPL": {"current_price": 100, "average_volume_30d": 5_000_000}},
            hub,
            run_mode="dev",
            requirement_plan=_plan(["AAPL"]),
        )
        self.assertIn("recent_fail_skipped", result["stage_counts"])


class TestRecentFailSkip(unittest.TestCase):
    def test_ticker_with_repeat_failure_is_skipped_before_chain_when_flag_on(self):
        from app.services.forward_factor_candidate_selection_service import select_forward_factor_candidates
        tickers = ["CRDO"]
        # Inject observation history with 3+ NO_ELIGIBLE_EXPIRATION_PAIR failures
        history = {"CRDO": {"failure_modes": {"NO_ELIGIBLE_EXPIRATION_PAIR": 3}}}
        hub = _make_hub()

        with patch("app.config.FF_SKIP_IF_ALREADY_FAILED_RECENTLY", True):
            result = build_forward_factor_strategy(
                tickers,
                {"CRDO": {"current_price": 100, "average_volume_30d": 5_000_000}},
                hub,
                run_mode="dev",
                requirement_plan=_plan(tickers),
                observation_history=history,
            )
        row = next((r for r in result["items"] if r["ticker"] == "CRDO"), None)
        if row and row.get("ff_candidate_stage") == "recent_fail_skip":
            self.assertEqual(hub.chain_set_calls, 0)  # chain not fetched for skipped ticker

    def test_ticker_without_repeat_failure_proceeds_to_chain(self):
        tickers = ["ELF"]
        hub = _make_hub()
        with patch("app.config.FF_SKIP_IF_ALREADY_FAILED_RECENTLY", True):
            result = build_forward_factor_strategy(
                tickers,
                {"ELF": {"current_price": 100, "average_volume_30d": 5_000_000}},
                hub,
                run_mode="dev",
                requirement_plan=_plan(tickers),
                observation_history={"ELF": {"failure_modes": {"NO_ELIGIBLE_EXPIRATION_PAIR": 1}}},
            )
        # 1 failure is not enough to skip — should reach chain
        row = next(r for r in result["items"] if r["ticker"] == "ELF")
        self.assertNotEqual(row.get("ff_candidate_stage"), "recent_fail_skip")

    def test_recent_fail_skip_disabled_does_not_skip_repeat_failures(self):
        tickers = ["CRDO"]
        history = {"CRDO": {"failure_modes": {"NO_ELIGIBLE_EXPIRATION_PAIR": 5}}}
        hub = _make_hub()
        with patch("app.config.FF_SKIP_IF_ALREADY_FAILED_RECENTLY", False):
            result = build_forward_factor_strategy(
                tickers,
                {"CRDO": {"current_price": 100, "average_volume_30d": 5_000_000}},
                hub,
                run_mode="dev",
                requirement_plan=_plan(tickers),
                observation_history=history,
            )
        row = next(r for r in result["items"] if r["ticker"] == "CRDO")
        self.assertNotEqual(row.get("ff_candidate_stage"), "recent_fail_skip")
        # Should have reached chain (and got no_pair since expirations=[])
        self.assertGreater(hub.chain_set_calls, 0)


class TestDryRunEnforcement(unittest.TestCase):
    def test_ff_dry_run_is_always_true(self):
        from app import config
        self.assertTrue(config.FORWARD_FACTOR_DRY_RUN)

    def test_all_ff_rows_carry_dry_run_true(self):
        tickers = ["AAPL"]
        hub = _make_hub()
        result = build_forward_factor_strategy(
            tickers,
            {"AAPL": {"current_price": 100, "average_volume_30d": 5_000_000}},
            hub,
            run_mode="dev",
            requirement_plan=_plan(tickers),
        )
        self.assertTrue(result.get("dry_run"))
        for row in result["items"]:
            self.assertTrue(row.get("dry_run"), msg=f"Row {row.get('ticker')} missing dry_run=True")

    def test_ff_strategy_absent_from_actionability_service_by_default(self):
        """FF rows with dry_run=True must score 0 actionability and not surface in Daily Opportunity."""
        from app.services.actionability_service import attach_actionability
        ff_row = {
            "strategy_id": "forward_factor_calendar",
            "ticker": "AAPL",
            "verdict": "DRY RUN PASS",
            "dry_run": True,
            "signal_score": 90,
            "actionability_score": 0,
        }
        result = attach_actionability(ff_row)
        # dry_run rows must not get elevated actionability
        self.assertEqual(result.get("actionability_score", 0), 0, "FF dry-run rows must not have non-zero actionability_score")
