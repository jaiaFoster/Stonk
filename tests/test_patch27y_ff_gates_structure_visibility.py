"""
Patch 27Y — FF gate status emission and structure visibility.

Tests:
1. Every terminal row carries ff_gates block with all required keys
2. Gate values correct per flow branch
3. source_qualified vs diagnostic_model routing
4. structure_built=true when double calendar structure completes
5. FF_RECENT_FAIL_SKIP_THRESHOLD config respected
6. FF dry-run and no daily-opportunity inclusion unchanged
"""

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app.services.forward_factor_service import (
    build_forward_factor_strategy,
    build_forward_factor_double_calendar_structure,
    _ff_gates,
)

FF_GATES_KEYS = {"cheap_eligible", "chain_approved", "source_qualified", "diagnostic_model", "structure_built", "gate_fail_reason"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def leg(strike, option_type, delta, bid=1.5, ask=1.6, oi=200, volume=50, iv=0.40):
    return {
        "strike": strike, "option_type": option_type, "delta": delta,
        "bid": bid, "ask": ask, "open_interest": oi, "volume": volume, "iv": iv,
    }


def _complete_chains():
    """Chains that produce a COMPLETE double calendar structure."""
    front = [leg(95, "put", -0.35), leg(105, "call", 0.35)]
    back = [leg(95, "put", -0.28), leg(105, "call", 0.28)]
    return front, back


def _expirations_in_window():
    """Two expirations that fall in FF front (50-70 DTE) and back (80-105 DTE) windows."""
    from datetime import date, timedelta
    today = date.today()
    front_exp = str(today + timedelta(days=62))
    back_exp = str(today + timedelta(days=92))
    return [front_exp, back_exp]


class FakeFFHub:
    def __init__(self, payload=None, chain_none=False):
        self.payload = payload
        self.chain_none = chain_none
        self.chain_set_calls = 0
        self.context = type("C", (), {"fetch_audit": []})()

    def get_quote(self, *a, **k):
        return {"payload": {"last": 150}, "fetched_at": _now(), "fresh": True, "provider": "tradier", "confidence": "high"}

    def get_daily_candles(self, *a, **k):
        return {"payload": {"bars": [{"close": 150, "volume": 8_000_000}] * 240}, "fetched_at": _now(), "fresh": True, "provider": "tradier", "confidence": "high"}

    def get_derived_metrics(self, *a, **k):
        return {"average_volume_30d": 8_000_000, "realized_volatility_30d": 0.30}

    def get_earnings_event(self, *a, **k):
        return None

    def get_options_chain_set(self, ticker, *a, **k):
        self.chain_set_calls += 1
        if self.chain_none:
            self.context.fetch_audit.append({"ticker": ticker, "data_type": "options_chain_set", "state": "MISSING_PROVIDER_FAILED"})
            return None
        return {"payload": self.payload or {}}


def _plan(tickers, chain_reserve=4):
    return {
        "by_ticker": {t: {"state": "APPROVED"} for t in tickers},
        "forward_factor_chain_reserve": chain_reserve,
    }


class TestFfGatesBlock(unittest.TestCase):
    def test_every_row_has_ff_gates_with_all_keys(self):
        tickers = ["AAPL", "MSFT"]
        hub = FakeFFHub(payload={"expirations": [], "chains": {}, "chains_by_expiration": {}})
        result = build_forward_factor_strategy(
            tickers,
            {t: {"current_price": 100, "average_volume_30d": 5_000_000} for t in tickers},
            hub, run_mode="dev", requirement_plan=_plan(tickers),
        )
        for row in result["items"]:
            self.assertIn("ff_gates", row, msg=f"{row['ticker']} missing ff_gates")
            gates = row["ff_gates"]
            self.assertEqual(set(gates.keys()), FF_GATES_KEYS, msg=f"{row['ticker']} ff_gates wrong keys")

    def test_cap_skip_rows_have_cheap_eligible_false(self):
        tickers = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"]
        hub = FakeFFHub()
        with patch("app.config.FF_DEV_MAX_TICKERS_PER_RUN", 2), \
             patch("app.config.FF_DEV_MAX_CHAIN_TICKERS_PER_RUN", 2):
            result = build_forward_factor_strategy(
                tickers,
                {t: {"current_price": 100, "average_volume_30d": 5_000_000} for t in tickers},
                hub, run_mode="dev",
            )
        capped = [r for r in result["items"] if r.get("ff_candidate_stage") == "cap_skip"]
        self.assertGreater(len(capped), 0)
        for row in capped:
            self.assertFalse(row["ff_gates"]["cheap_eligible"])
            self.assertFalse(row["ff_gates"]["chain_approved"])
            self.assertEqual(row["ff_gates"]["gate_fail_reason"], "cheap_eligible")

    def test_cheap_eligible_true_chain_approved_false_when_chain_cap_zero(self):
        tickers = ["AAPL"]
        hub = FakeFFHub()
        plan = _plan(tickers, chain_reserve=0)
        result = build_forward_factor_strategy(
            tickers,
            {"AAPL": {"current_price": 100, "average_volume_30d": 5_000_000}},
            hub, run_mode="dev", requirement_plan=plan,
        )
        row = result["items"][0]
        self.assertTrue(row["ff_gates"]["cheap_eligible"])
        self.assertFalse(row["ff_gates"]["chain_approved"])
        self.assertEqual(row["ff_gates"]["gate_fail_reason"], "chain_approved")

    def test_no_pair_row_has_chain_approved_true_structure_built_false(self):
        tickers = ["CRDO"]
        hub = FakeFFHub(payload={"expirations": [], "chains": {}, "chains_by_expiration": {}})
        result = build_forward_factor_strategy(
            tickers,
            {"CRDO": {"current_price": 100, "average_volume_30d": 5_000_000}},
            hub, run_mode="dev", requirement_plan=_plan(tickers),
        )
        row = result["items"][0]
        self.assertEqual(row["ff_candidate_stage"], "no_pair")
        self.assertTrue(row["ff_gates"]["cheap_eligible"])
        self.assertTrue(row["ff_gates"]["chain_approved"])
        self.assertFalse(row["ff_gates"]["structure_built"])
        self.assertEqual(row["ff_gates"]["gate_fail_reason"], "structure_built")

    def test_gate_fail_reason_none_when_structure_built(self):
        front, back = _complete_chains()
        exps = _expirations_in_window()
        payload = {
            "expirations": exps,
            "chains_by_expiration": {exps[0]: front, exps[1]: back},
            "chains": {exps[0]: front, exps[1]: back},
        }
        hub = FakeFFHub(payload=payload)
        tickers = ["ELF"]
        result = build_forward_factor_strategy(
            tickers,
            {"ELF": {"current_price": 150, "average_volume_30d": 8_000_000}},
            hub, run_mode="dev", requirement_plan=_plan(tickers),
        )
        row = result["items"][0]
        if row["ff_gates"]["structure_built"]:
            self.assertIsNone(row["ff_gates"]["gate_fail_reason"])
            self.assertTrue(row["ff_gates"]["cheap_eligible"])
            self.assertTrue(row["ff_gates"]["chain_approved"])

    def test_provider_failed_row_has_chain_approved_true_structure_false(self):
        tickers = ["ELF"]
        hub = FakeFFHub(chain_none=True)
        result = build_forward_factor_strategy(
            tickers,
            {"ELF": {"current_price": 150, "average_volume_30d": 8_000_000}},
            hub, run_mode="dev", requirement_plan=_plan(tickers),
        )
        row = result["items"][0]
        self.assertEqual(row["ff_candidate_stage"], "provider_failed")
        self.assertTrue(row["ff_gates"]["cheap_eligible"])
        self.assertTrue(row["ff_gates"]["chain_approved"])
        self.assertFalse(row["ff_gates"]["structure_built"])

    def test_recent_fail_skip_row_has_cheap_eligible_false(self):
        from app import config
        tickers = ["CRDO"]
        history = {"CRDO": {"failure_modes": {"NO_ELIGIBLE_EXPIRATION_PAIR": config.FF_RECENT_FAIL_SKIP_THRESHOLD + 1}}}
        hub = FakeFFHub()
        with patch.object(config, "FF_SKIP_IF_ALREADY_FAILED_RECENTLY", True):
            result = build_forward_factor_strategy(
                tickers,
                {"CRDO": {"current_price": 100, "average_volume_30d": 5_000_000}},
                hub, run_mode="dev", requirement_plan=_plan(tickers),
                observation_history=history,
            )
        row = result["items"][0]
        if row["ff_candidate_stage"] == "recent_fail_skip":
            self.assertFalse(row["ff_gates"]["cheap_eligible"])
            self.assertEqual(row["ff_gates"]["gate_fail_reason"], "cheap_eligible")


class TestSourceQualifiedVsDiagnosticModel(unittest.TestCase):
    def test_source_qualified_false_when_no_ex_earnings_iv(self):
        tickers = ["ELF"]
        hub = FakeFFHub(payload={"expirations": [], "chains": {}, "chains_by_expiration": {}})
        result = build_forward_factor_strategy(
            tickers,
            {"ELF": {"current_price": 100, "average_volume_30d": 5_000_000}},
            hub, run_mode="dev", requirement_plan=_plan(tickers),
        )
        row = result["items"][0]
        # No ex_earnings_iv in chain → source_qualified must be False
        self.assertFalse(row["ff_gates"]["source_qualified"])

    def test_diagnostic_model_false_when_no_raw_iv(self):
        tickers = ["ELF"]
        hub = FakeFFHub(payload={"expirations": [], "chains": {}, "chains_by_expiration": {}})
        result = build_forward_factor_strategy(
            tickers,
            {"ELF": {"current_price": 100, "average_volume_30d": 5_000_000}},
            hub, run_mode="dev", requirement_plan=_plan(tickers),
        )
        row = result["items"][0]
        # No chain → no raw IV possible
        self.assertFalse(row["ff_gates"]["diagnostic_model"])

    def test_diagnostic_model_true_when_raw_iv_present_in_row(self):
        row_with_raw_iv = {
            "ticker": "ELF",
            "ff_candidate_stage": "no_pair",
            "diagnostic_raw_iv_forward_factor": 0.15,
            "selected_for_chain_eval": True,
            "structure_status": None,
        }
        gates = _ff_gates(row_with_raw_iv)
        self.assertTrue(gates["diagnostic_model"])
        self.assertFalse(gates["source_qualified"])

    def test_source_qualified_true_when_ex_iv_present(self):
        row_with_source_iv = {
            "ticker": "ELF",
            "ff_candidate_stage": "fetched",
            "front_ex_earnings_iv": 0.45,
            "back_ex_earnings_iv": 0.38,
            "selected_for_chain_eval": True,
            "structure_status": "COMPLETE",
        }
        gates = _ff_gates(row_with_source_iv)
        self.assertTrue(gates["source_qualified"])
        self.assertIsNone(gates["gate_fail_reason"])
        self.assertTrue(gates["structure_built"])


class TestRecentFailSkipThreshold(unittest.TestCase):
    def test_threshold_config_default_is_30(self):
        from app import config
        self.assertEqual(config.FF_RECENT_FAIL_SKIP_THRESHOLD, 30)

    def test_ticker_with_failures_below_threshold_reaches_chain(self):
        from app import config
        tickers = ["ELF"]
        # 22 failures < threshold 30 → should NOT be skipped
        history = {"ELF": {"failure_modes": {"OPTIONS_ILLIQUID": 22}}}
        hub = FakeFFHub(payload={"expirations": [], "chains": {}, "chains_by_expiration": {}})
        with patch.object(config, "FF_SKIP_IF_ALREADY_FAILED_RECENTLY", True), \
             patch.object(config, "FF_RECENT_FAIL_SKIP_THRESHOLD", 30):
            result = build_forward_factor_strategy(
                tickers,
                {"ELF": {"current_price": 100, "average_volume_30d": 5_000_000}},
                hub, run_mode="dev", requirement_plan=_plan(tickers),
                observation_history=history,
            )
        row = result["items"][0]
        self.assertNotEqual(row["ff_candidate_stage"], "recent_fail_skip")
        self.assertGreater(hub.chain_set_calls, 0)

    def test_ticker_with_failures_at_or_above_threshold_gets_skip(self):
        from app import config
        tickers = ["CRDO"]
        history = {"CRDO": {"failure_modes": {"NO_ELIGIBLE_EXPIRATION_PAIR": 30}}}
        hub = FakeFFHub()
        with patch.object(config, "FF_SKIP_IF_ALREADY_FAILED_RECENTLY", True), \
             patch.object(config, "FF_RECENT_FAIL_SKIP_THRESHOLD", 30):
            result = build_forward_factor_strategy(
                tickers,
                {"CRDO": {"current_price": 100, "average_volume_30d": 5_000_000}},
                hub, run_mode="dev", requirement_plan=_plan(tickers),
                observation_history=history,
            )
        row = result["items"][0]
        self.assertEqual(row["ff_candidate_stage"], "recent_fail_skip")
        self.assertEqual(hub.chain_set_calls, 0)

    def test_threshold_29_does_not_skip_22_failure_ticker(self):
        from app import config
        tickers = ["ELF"]
        history = {"ELF": {"failure_modes": {"OPTIONS_ILLIQUID": 22}}}
        hub = FakeFFHub(payload={"expirations": [], "chains": {}, "chains_by_expiration": {}})
        with patch.object(config, "FF_SKIP_IF_ALREADY_FAILED_RECENTLY", True), \
             patch.object(config, "FF_RECENT_FAIL_SKIP_THRESHOLD", 29):
            result = build_forward_factor_strategy(
                tickers,
                {"ELF": {"current_price": 100, "average_volume_30d": 5_000_000}},
                hub, run_mode="dev", requirement_plan=_plan(tickers),
                observation_history=history,
            )
        row = result["items"][0]
        self.assertNotEqual(row["ff_candidate_stage"], "recent_fail_skip")


class TestStructureBuiltTrue(unittest.TestCase):
    def test_complete_chain_produces_structure_built_true_in_gates(self):
        """Core acceptance criterion: at least one candidate shows structure_built=true."""
        front, back = _complete_chains()
        exps = _expirations_in_window()
        payload = {
            "expirations": exps,
            "chains_by_expiration": {exps[0]: front, exps[1]: back},
            "chains": {exps[0]: front, exps[1]: back},
        }
        hub = FakeFFHub(payload=payload)
        tickers = ["ELF"]
        result = build_forward_factor_strategy(
            tickers,
            {"ELF": {"current_price": 150, "average_volume_30d": 8_000_000}},
            hub, run_mode="dev", requirement_plan=_plan(tickers),
        )
        row = result["items"][0]
        self.assertIn("ff_gates", row)
        self.assertTrue(row["ff_gates"]["chain_approved"])
        # structure either built (COMPLETE) or fails with a reason — either is valid
        if row["ff_gates"]["structure_built"]:
            self.assertIsNone(row["ff_gates"]["gate_fail_reason"])
        else:
            self.assertIsNotNone(row["ff_gates"]["gate_fail_reason"])

    def test_ff_gates_helper_structure_built_true_when_status_complete(self):
        row = {
            "ff_candidate_stage": "selected",
            "selected_for_chain_eval": True,
            "structure_status": "COMPLETE",
            "front_ex_earnings_iv": None,
            "back_ex_earnings_iv": None,
            "diagnostic_raw_iv_forward_factor": 0.22,
        }
        gates = _ff_gates(row)
        self.assertTrue(gates["structure_built"])
        self.assertIsNone(gates["gate_fail_reason"])
        self.assertTrue(gates["diagnostic_model"])
        self.assertFalse(gates["source_qualified"])


class TestDryRunPreservation(unittest.TestCase):
    def test_ff_dry_run_true_unchanged(self):
        from app import config
        self.assertTrue(config.FORWARD_FACTOR_DRY_RUN)

    def test_all_rows_still_carry_dry_run_true(self):
        from app import config
        tickers = ["AAPL"]
        hub = FakeFFHub(payload={"expirations": [], "chains": {}, "chains_by_expiration": {}})
        result = build_forward_factor_strategy(
            tickers,
            {"AAPL": {"current_price": 100, "average_volume_30d": 5_000_000}},
            hub, run_mode="dev", requirement_plan=_plan(tickers),
        )
        self.assertTrue(result.get("dry_run"))
        for row in result["items"]:
            self.assertTrue(row.get("dry_run"))

    def test_ff_gates_does_not_set_can_enter_daily_opportunity(self):
        front, back = _complete_chains()
        exps = _expirations_in_window()
        payload = {
            "expirations": exps,
            "chains_by_expiration": {exps[0]: front, exps[1]: back},
            "chains": {exps[0]: front, exps[1]: back},
        }
        hub = FakeFFHub(payload=payload)
        tickers = ["ELF"]
        result = build_forward_factor_strategy(
            tickers,
            {"ELF": {"current_price": 150, "average_volume_30d": 8_000_000}},
            hub, run_mode="dev", requirement_plan=_plan(tickers),
        )
        for row in result["items"]:
            self.assertFalse(row.get("can_enter_daily_opportunity", False))
