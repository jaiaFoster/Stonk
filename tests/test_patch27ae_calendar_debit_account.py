"""
Patch 27AE — Calendar debit cap tiered + account value wiring.

Tests:
 1. TKT-012: tier_1 cap applies for underlying < $100
 2. TKT-012: tier_2 cap applies for underlying $100-$500
 3. TKT-012: tier_3 cap applies for underlying > $500 (MU scenario)
 4. TKT-012: MU at 9.1% debit passes tier_3 (12%) cap
 5. TKT-012: debit_cap_tier_result present in scanner candidate output
 6. TKT-012: debit cap failure is WARN not FAIL in ranking (no score penalty)
 7. TKT-024: account_value from positions flows into evaluate_account_risk
 8. TKT-024: CALENDAR_ACCOUNT_VALUE_OVERRIDE takes precedence over positions
 9. TKT-024: account_risk_status is OK (not UNKNOWN) when positions present
10. TKT-027: price_stale=True when drift exceeds threshold
11. TKT-027: price_stale=False when drift within threshold
12. TKT-027: price_freshness_check=quote_unavailable when ticker not in snapshot
"""

from __future__ import annotations

import unittest
from unittest.mock import patch


# ---------------------------------------------------------------------------
# 1-5: TKT-012 — tiered debit cap
# ---------------------------------------------------------------------------

class TestTieredDebitCap(unittest.TestCase):

    def _cap(self, price):
        from app.services.calendar_spread_service import _tiered_debit_cap_pct
        return _tiered_debit_cap_pct(price)

    def test_tier_1_below_100(self):
        cap = self._cap(50.0)
        self.assertAlmostEqual(cap, 0.08)

    def test_tier_2_between_100_and_500(self):
        cap = self._cap(250.0)
        self.assertAlmostEqual(cap, 0.10)

    def test_tier_3_above_500(self):
        cap = self._cap(1020.0)
        self.assertAlmostEqual(cap, 0.12)

    def test_mu_scenario_passes_tier_3(self):
        # MU at $1020 with $93 debit = 9.1% — should pass tier_3 (12%) cap
        price = 1020.0
        debit = 92.8  # 9.09%
        cap = self._cap(price)
        debit_pct = debit / price
        self.assertTrue(debit_pct <= cap, f"{debit_pct:.3f} should be <= {cap}")

    def test_debit_cap_tier_result_in_candidate(self):
        from app.services.calendar_spread_service import _score_candidate

        def _opt(strike, bid, ask, mid, iv=None):
            return {"strike": strike, "bid": bid, "ask": ask, "mid": mid, "iv": iv, "volume": 100, "open_interest": 200}

        front = _opt(100.0, 2.0, 2.5, 2.25, iv=0.35)
        back = _opt(100.0, 4.0, 4.5, 4.25, iv=0.28)
        candidate = _score_candidate(
            ticker="TEST",
            quote={"last": 100.0},
            underlying_price=100.0,
            front_expiration="2026-07-18",
            back_expiration="2026-08-15",
            front_leg=front,
            back_leg=back,
            earnings_event=None,
        )
        self.assertIn("debit_cap_tier_result", candidate)
        tier_result = candidate["debit_cap_tier_result"]
        self.assertIsNotNone(tier_result)
        self.assertIn("passes", tier_result)
        self.assertIn("tier", tier_result)
        self.assertIn("cap_pct", tier_result)


# ---------------------------------------------------------------------------
# 6: TKT-012 — debit cap failure is WARN not FAIL in ranking
# ---------------------------------------------------------------------------

class TestDebitCapWarnNotFail(unittest.TestCase):

    def _make_candidate(self, debit_pct, underlying_price=50.0):
        debit = underlying_price * debit_pct / 100.0
        return {
            "ticker": "TEST",
            "score": 65.0,
            "underlying_price": underlying_price,
            "debit_pct_underlying": debit_pct,
            "conservative_debit": debit,
            "mid_debit": debit,
            "debit_cap_tier_result": {
                "underlying_price": underlying_price,
                "debit": debit,
                "debit_pct_underlying": debit_pct,
                "cap_pct": 8.0,
                "passes": debit_pct <= 8.0,
                "tier": "tier_1",
            },
            "max_leg_spread_pct": 10.0,
            "min_leg_volume": 50,
            "min_leg_open_interest": 100,
            "iv_edge": 0.05,
            "atm_distance_pct": 1.0,
            "front_dte": 8,
            "back_dte": 35,
            "earnings_event": {"days_until_earnings": 9, "earnings_date": "2026-07-10"},
            "earnings_timing": {"captures_event": True},
        }

    def test_debit_cap_fail_is_warn_in_ranking(self):
        from app.services.calendar_ranking_service import _rank_candidate
        # debit_pct=12% exceeds tier_1 cap=8% → debit_cap_tier_result.passes=False
        candidate = self._make_candidate(debit_pct=12.0, underlying_price=50.0)
        candidate["debit_cap_tier_result"]["passes"] = False
        row = _rank_candidate(candidate, {})
        debit_criterion = next((c for c in row["criteria"] if c["name"] == "Debit size"), None)
        self.assertIsNotNone(debit_criterion)
        self.assertEqual(debit_criterion["status"], "WARN", "Debit cap failure should be WARN not FAIL")
        # WARN should NOT be in hard_fails → should not reduce rank_score by 12
        self.assertEqual(row.get("failed_requirement_count"), 0, "WARN should not count as hard fail")


# ---------------------------------------------------------------------------
# 7-9: TKT-024 — account value wiring
# ---------------------------------------------------------------------------

class TestAccountValueWiring(unittest.TestCase):

    def _candidate(self, debit=200.0):
        return {"conservative_debit": debit / 100.0, "mid_debit": debit / 100.0}

    def test_account_value_from_context(self):
        from app.services.calendar_risk_fact_service import evaluate_account_risk
        with patch("app.config.CALENDAR_ACCOUNT_VALUE_OVERRIDE", None), \
             patch("app.config.CALENDAR_ACCOUNT_GUARDRAILS_ENABLED", True), \
             patch("app.config.CALENDAR_MAX_DEBIT_DOLLARS", 500), \
             patch("app.config.CALENDAR_WARN_DEBIT_DOLLARS", 250), \
             patch("app.config.CALENDAR_MAX_DEBIT_PCT_OF_ACCOUNT", 0.02), \
             patch("app.config.CALENDAR_EXPERIMENTAL_MAX_ACCOUNT_RISK_PCT", 1.5):
            result = evaluate_account_risk(
                self._candidate(debit=200.0),
                account_context={"account_value_estimate": 50000.0},
            )
        self.assertEqual(result["account_value_estimate"], 50000.0)
        self.assertNotEqual(result["account_risk_status"], "UNKNOWN ACCOUNT VALUE")

    def test_account_value_override_takes_precedence(self):
        from app.services.calendar_risk_fact_service import evaluate_account_risk
        with patch("app.config.CALENDAR_ACCOUNT_VALUE_OVERRIDE", 75000.0), \
             patch("app.config.CALENDAR_ACCOUNT_GUARDRAILS_ENABLED", True), \
             patch("app.config.CALENDAR_MAX_DEBIT_DOLLARS", 500), \
             patch("app.config.CALENDAR_WARN_DEBIT_DOLLARS", 250), \
             patch("app.config.CALENDAR_MAX_DEBIT_PCT_OF_ACCOUNT", 0.02), \
             patch("app.config.CALENDAR_EXPERIMENTAL_MAX_ACCOUNT_RISK_PCT", 1.5):
            # Pass different value in context — override should win
            result = evaluate_account_risk(
                self._candidate(debit=200.0),
                account_context={"account_value_estimate": 10000.0},
            )
        self.assertEqual(result["account_value_estimate"], 75000.0)

    def test_account_risk_not_unknown_when_context_present(self):
        from app.services.calendar_risk_fact_service import evaluate_account_risk
        with patch("app.config.CALENDAR_ACCOUNT_VALUE_OVERRIDE", None), \
             patch("app.config.CALENDAR_ACCOUNT_GUARDRAILS_ENABLED", True), \
             patch("app.config.CALENDAR_MAX_DEBIT_DOLLARS", 500), \
             patch("app.config.CALENDAR_WARN_DEBIT_DOLLARS", 250), \
             patch("app.config.CALENDAR_MAX_DEBIT_PCT_OF_ACCOUNT", 0.02), \
             patch("app.config.CALENDAR_EXPERIMENTAL_MAX_ACCOUNT_RISK_PCT", 1.5):
            result = evaluate_account_risk(
                self._candidate(debit=150.0),
                account_context={"account_value_estimate": 100000.0},
            )
        self.assertNotEqual(result["account_risk_status"], "UNKNOWN ACCOUNT VALUE")


# ---------------------------------------------------------------------------
# 10-12: TKT-027 — price freshness gate
# ---------------------------------------------------------------------------

class TestPriceFreshnessGate(unittest.TestCase):

    def _gate(self, structure_price, current_price, threshold=0.015):
        from app.services.analysis_service import _apply_price_freshness_gate
        candidates = [{"ticker": "AAPL", "underlying_price": structure_price, "score": 70.0}]
        snapshot = {
            "AAPL": {"quote": {"last": current_price}} if current_price is not None else {}
        }
        with patch("app.config.CALENDAR_PRICE_FRESHNESS_THRESHOLD", threshold):
            return _apply_price_freshness_gate(candidates, snapshot)[0]

    def test_price_stale_when_drift_exceeds_threshold(self):
        result = self._gate(structure_price=100.0, current_price=102.0, threshold=0.015)
        self.assertTrue(result["price_stale"])
        self.assertAlmostEqual(result["price_drift_pct"], 2.0, places=1)

    def test_price_not_stale_within_threshold(self):
        result = self._gate(structure_price=100.0, current_price=100.5, threshold=0.015)
        self.assertFalse(result["price_stale"])

    def test_freshness_check_unavailable_when_no_quote(self):
        result = self._gate(structure_price=100.0, current_price=None)
        self.assertEqual(result["price_freshness_check"], "quote_unavailable")
        self.assertFalse(result["price_stale"])


if __name__ == "__main__":
    unittest.main()
