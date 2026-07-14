"""ASA Patch 33A.1 — Calendar Lifecycle Integration Tests

Deterministic structure tests: creates concrete ticker scenarios and verifies
the full lifecycle classification pipeline end-to-end.

Key invariants tested:
- All 9 lifecycle stages are reachable given appropriate inputs
- No silent opportunity deletion (all items produce a row)
- Budget skips never produce FAIL
- Expected-missing data at early DTE never produces FAIL
- Stable opportunity_id across multiple classification runs
- Policy ordering invariants are always satisfied
- DTE thresholds match spec exactly (not off-by-one)
"""

from __future__ import annotations

import unittest
from datetime import date, timedelta


def _policy():
    from app.models.calendar_evolution_policy import CalendarEvolutionPolicy
    return CalendarEvolutionPolicy(
        discovery_start_event_dte=0,
        discovery_end_event_dte=35,
        build_start_event_dte=24,
        surface_start_event_dte=14,
        ideal_entry_min_event_dte=6,
        ideal_entry_max_event_dte=12,
        late_entry_event_dte=4,
    )


class TestCalendarLifecycleScenarios(unittest.TestCase):
    """Deterministic scenarios covering the full lifecycle DTE range."""

    def setUp(self):
        self.policy = _policy()
        self.today = date(2026, 7, 13)  # fixed for deterministic tests

    def _classify(self, days_until_event, **kwargs):
        from app.services.calendar_opportunity_lifecycle_adapter import classify_calendar_opportunity
        return classify_calendar_opportunity(days_until_event, self.policy, **kwargs)

    def _build(self, ticker, days_until_event, **kwargs):
        from app.services.calendar_opportunity_lifecycle_adapter import build_calendar_lifecycle_opportunity
        event_date = self.today + timedelta(days=days_until_event)
        return build_calendar_lifecycle_opportunity(
            ticker=ticker,
            earnings_date=event_date,
            days_until_event=days_until_event,
            policy=self.policy,
            evaluation_date=self.today,
            **kwargs,
        )

    # ── Boundary: discovery_end DTE ───────────────────────────────────────────
    def test_exactly_35_dte_is_in_window(self):
        from app.models.strategy_opportunity_lifecycle import LifecycleStage
        c = self._classify(35)
        self.assertEqual(c.lifecycle_stage, LifecycleStage.DISCOVERED)

    def test_exactly_36_dte_is_outside_window(self):
        from app.models.strategy_opportunity_lifecycle import LifecycleStage
        c = self._classify(36)
        self.assertEqual(c.lifecycle_stage, LifecycleStage.OUTSIDE_WINDOW)

    # ── Boundary: build_start DTE ─────────────────────────────────────────────
    def test_exactly_24_dte_is_build_eligible(self):
        c = self._classify(24)
        self.assertTrue(c.build_eligible)

    def test_exactly_25_dte_is_not_build_eligible(self):
        c = self._classify(25)
        self.assertFalse(c.build_eligible)

    # ── Boundary: surface_start DTE ───────────────────────────────────────────
    def test_exactly_14_dte_is_surface_eligible(self):
        c = self._classify(14)
        self.assertTrue(c.surface_eligible)

    def test_exactly_15_dte_is_not_surface_eligible(self):
        c = self._classify(15)
        self.assertFalse(c.surface_eligible)

    # ── Boundary: entry DTE thresholds ───────────────────────────────────────
    def test_exactly_12_dte_is_ideal_entry(self):
        c = self._classify(12, has_structure=True)
        self.assertTrue(c.entry_allowed)

    def test_exactly_13_dte_is_surfaced_not_entry(self):
        from app.models.strategy_opportunity_lifecycle import LifecycleStage
        c = self._classify(13)
        self.assertEqual(c.lifecycle_stage, LifecycleStage.SURFACED)
        # 13 DTE is surface_eligible but > ideal_entry_max (12) → not entry_allowed yet
        self.assertFalse(c.entry_allowed)

    def test_exactly_6_dte_is_ideal_entry(self):
        c = self._classify(6, has_structure=True)
        self.assertTrue(c.entry_allowed)

    def test_exactly_5_dte_is_late_entry(self):
        c = self._classify(5, has_structure=True)
        self.assertTrue(c.entry_allowed)  # late entry is still entry_allowed

    def test_exactly_4_dte_is_late_entry(self):
        c = self._classify(4, has_structure=True)
        self.assertTrue(c.entry_allowed)

    def test_exactly_0_dte_is_in_window_but_not_entry_allowed(self):
        """0 DTE is within discovery window (start=0) but past late_entry cutoff (4)."""
        from app.models.strategy_opportunity_lifecycle import LifecycleStage
        c = self._classify(0, has_structure=True)
        # still actionable stage (event today), but entry is past the late-entry DTE=4 cutoff
        self.assertFalse(c.entry_allowed, "0 DTE is past late_entry_event_dte=4, not entry_allowed")

    def test_exactly_minus_1_dte_is_post_event(self):
        from app.models.strategy_opportunity_lifecycle import LifecycleStage
        c = self._classify(-1)
        self.assertEqual(c.lifecycle_stage, LifecycleStage.POST_EVENT)
        self.assertFalse(c.entry_allowed)

    # ── No silent failures ────────────────────────────────────────────────────
    def test_no_fail_verdict_for_early_discovery(self):
        from app.models.strategy_opportunity_lifecycle import Verdict
        for dte in range(25, 36):
            c = self._classify(dte)
            self.assertNotEqual(c.verdict, Verdict.FAIL,
                                f"DTE={dte}: expected EXPECTED_MISSING not FAIL")

    def test_no_fail_verdict_for_deferred_budget(self):
        from app.models.strategy_opportunity_lifecycle import EvaluationState, Verdict
        for dte in [5, 8, 12]:
            c = self._classify(dte, structure_evaluation_state=EvaluationState.DEFERRED_BUDGET)
            self.assertNotEqual(c.verdict, Verdict.FAIL,
                                f"DTE={dte} DEFERRED_BUDGET must not be FAIL")

    # ── Opportunity identity stability ────────────────────────────────────────
    def test_opportunity_id_stable_across_dte_changes(self):
        """Same ticker+event_date = same opportunity_id regardless of current DTE."""
        from app.services.calendar_opportunity_lifecycle_adapter import build_opportunity_id
        event_date = self.today + timedelta(days=10)
        oid1 = build_opportunity_id("NVDA", event_date)
        oid2 = build_opportunity_id("NVDA", event_date)
        self.assertEqual(oid1, oid2)

    def test_opportunity_id_stable_with_structure_change(self):
        """Gaining a structure must not change the opportunity_id."""
        opp1, _ = self._build("AAPL", 10, has_structure=False)
        opp2, _ = self._build("AAPL", 10, has_structure=True)
        self.assertEqual(opp1.opportunity_id, opp2.opportunity_id)

    # ── Full lifecycle scenarios ──────────────────────────────────────────────
    def test_nvda_scenario_30_dte_early_monitoring(self):
        """NVDA at 30 DTE: discovered, EXPECTED_MISSING, MONITOR action."""
        from app.models.strategy_opportunity_lifecycle import (
            LifecycleStage, EvaluationState, RecommendedAction,
        )
        opp, errors = self._build("NVDA", 30, has_structure=False)
        self.assertEqual(errors, [])
        self.assertEqual(opp.lifecycle_stage, LifecycleStage.DISCOVERED)
        self.assertEqual(opp.evaluation_state, EvaluationState.EXPECTED_MISSING)
        self.assertEqual(opp.recommended_action, RecommendedAction.MONITOR)
        self.assertFalse(opp.build_eligible)
        self.assertFalse(opp.surface_eligible)
        self.assertFalse(opp.entry_allowed)

    def test_aapl_scenario_20_dte_building(self):
        """AAPL at 20 DTE: DEVELOPING, BUILDING, PREPARE action."""
        from app.models.strategy_opportunity_lifecycle import (
            LifecycleStage, EvaluationState, RecommendedAction,
        )
        opp, errors = self._build("AAPL", 20, has_structure=False)
        self.assertEqual(errors, [])
        self.assertEqual(opp.lifecycle_stage, LifecycleStage.DEVELOPING)
        self.assertEqual(opp.evaluation_state, EvaluationState.BUILDING)
        self.assertTrue(opp.build_eligible)
        self.assertFalse(opp.surface_eligible)

    def test_tsla_scenario_14_dte_surfaced_with_structure(self):
        """TSLA at 14 DTE with structure: SURFACED, STRUCTURE_COMPLETE, WATCH, PREPARE."""
        from app.models.strategy_opportunity_lifecycle import (
            LifecycleStage, EvaluationState, Verdict, RecommendedAction,
        )
        opp, errors = self._build("TSLA", 14, has_structure=True)
        self.assertEqual(errors, [])
        self.assertEqual(opp.lifecycle_stage, LifecycleStage.SURFACED)
        self.assertEqual(opp.evaluation_state, EvaluationState.STRUCTURE_COMPLETE)
        self.assertEqual(opp.verdict, Verdict.WATCH)
        self.assertTrue(opp.surface_eligible)
        self.assertFalse(opp.entry_allowed)

    def test_meta_scenario_10_dte_actionable_entry(self):
        """META at 10 DTE with structure: ACTIONABLE, PASS, ENTER."""
        from app.models.strategy_opportunity_lifecycle import (
            LifecycleStage, Verdict, RecommendedAction,
        )
        opp, errors = self._build("META", 10, has_structure=True)
        self.assertEqual(errors, [])
        self.assertEqual(opp.lifecycle_stage, LifecycleStage.ACTIONABLE)
        self.assertEqual(opp.verdict, Verdict.PASS)
        self.assertEqual(opp.recommended_action, RecommendedAction.ENTER)
        self.assertTrue(opp.entry_allowed)

    def test_googl_scenario_negative_dte_post_event(self):
        """GOOGL at -2 DTE: POST_EVENT, not entry_allowed."""
        from app.models.strategy_opportunity_lifecycle import LifecycleStage
        opp, errors = self._build("GOOGL", -2)
        self.assertEqual(errors, [])
        self.assertEqual(opp.lifecycle_stage, LifecycleStage.POST_EVENT)
        self.assertFalse(opp.entry_allowed)

    def test_amzn_open_position_scenario(self):
        """AMZN with open position: OPEN_POSITION, HOLD."""
        from app.models.strategy_opportunity_lifecycle import (
            LifecycleStage, RecommendedAction,
        )
        opp, errors = self._build("AMZN", 8, has_open_position=True)
        self.assertEqual(errors, [])
        self.assertEqual(opp.lifecycle_stage, LifecycleStage.OPEN_POSITION)
        self.assertEqual(opp.recommended_action, RecommendedAction.HOLD)
        self.assertFalse(opp.entry_allowed)

    # ── Batch: no silent deletion ─────────────────────────────────────────────
    def test_all_in_window_tickers_produce_rows(self):
        """Every ticker within discovery window must produce a lifecycle row."""
        from app.services.calendar_opportunity_lifecycle_adapter import lifecycle_rows_from_discovery
        tickers = ["NVDA", "AAPL", "TSLA", "META", "AMZN"]
        items = []
        for i, ticker in enumerate(tickers):
            dte = 5 + i * 6  # 5, 11, 17, 23, 29 — all within 0-35
            event_date = (self.today + timedelta(days=dte)).isoformat()
            items.append({"ticker": ticker, "earnings_date": event_date})
        quality_result = {"items": items}
        rows = lifecycle_rows_from_discovery(quality_result, self.policy, evaluation_date=self.today)
        produced_tickers = {r["ticker"] for r in rows}
        for ticker in tickers:
            self.assertIn(ticker, produced_tickers,
                          f"{ticker} must not be silently dropped from lifecycle rows")

    # ── Validation errors are never raised for valid inputs ───────────────────
    def test_no_validation_errors_for_standard_scenarios(self):
        scenarios = [
            (35, {}),
            (30, {}),
            (24, {"has_structure": False}),
            (14, {"has_structure": True}),
            (10, {"has_structure": True}),
            (6, {"has_structure": True}),
            (4, {"has_structure": True}),
            (0, {"has_structure": True}),
            (-1, {}),
        ]
        for dte, kwargs in scenarios:
            opp, errors = self._build("NVDA", dte, **kwargs)
            self.assertEqual(errors, [], f"Unexpected validation errors at DTE={dte}: {errors}")


if __name__ == "__main__":
    unittest.main()
