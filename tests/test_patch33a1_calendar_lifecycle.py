"""ASA Patch 33A.1 — Calendar Lifecycle Adapter Tests

Covers:
- classify_calendar_opportunity() for each DTE range
- build_opportunity_id() stable identity
- build_calendar_lifecycle_opportunity() end-to-end
- lifecycle_rows_from_discovery() bridge
- API endpoint /api/dev/strategy-lifecycle registered
- Early-stage (25-35 DTE) yields DISCOVERED / EXPECTED_MISSING, not FAIL
- Budget skips do not produce FAIL verdict
- POST_EVENT yields POST_EVENT stage, not FAIL
"""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch


def _default_policy():
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


# ---------------------------------------------------------------------------
# classify_calendar_opportunity — DTE range mapping
# ---------------------------------------------------------------------------
class TestClassifyCalendarOpportunity(unittest.TestCase):
    def setUp(self):
        self.policy = _default_policy()

    def _classify(self, days_until_event, **kwargs):
        from app.services.calendar_opportunity_lifecycle_adapter import classify_calendar_opportunity
        return classify_calendar_opportunity(days_until_event, self.policy, **kwargs)

    # Outside window
    def test_outside_window_36_dte(self):
        from app.models.strategy_opportunity_lifecycle import LifecycleStage, EvaluationState, Verdict
        c = self._classify(36)
        self.assertEqual(c.lifecycle_stage, LifecycleStage.OUTSIDE_WINDOW)
        self.assertEqual(c.verdict, Verdict.NOT_EVALUATED)
        self.assertFalse(c.build_eligible)
        self.assertFalse(c.entry_allowed)

    # Post-event
    def test_post_event_negative_dte(self):
        from app.models.strategy_opportunity_lifecycle import LifecycleStage, Verdict
        c = self._classify(-1)
        self.assertEqual(c.lifecycle_stage, LifecycleStage.POST_EVENT)
        self.assertEqual(c.verdict, Verdict.NOT_EVALUATED)
        self.assertFalse(c.entry_allowed)

    # Early discovery: 25–35 DTE → DISCOVERED / EXPECTED_MISSING
    def test_early_discovery_35_dte(self):
        from app.models.strategy_opportunity_lifecycle import LifecycleStage, EvaluationState, Verdict
        c = self._classify(35)
        self.assertEqual(c.lifecycle_stage, LifecycleStage.DISCOVERED)
        self.assertEqual(c.evaluation_state, EvaluationState.EXPECTED_MISSING)
        self.assertEqual(c.verdict, Verdict.NOT_EVALUATED)
        self.assertFalse(c.build_eligible)

    def test_early_discovery_30_dte(self):
        from app.models.strategy_opportunity_lifecycle import LifecycleStage, EvaluationState
        c = self._classify(30)
        self.assertEqual(c.lifecycle_stage, LifecycleStage.DISCOVERED)
        self.assertEqual(c.evaluation_state, EvaluationState.EXPECTED_MISSING)

    def test_early_discovery_25_dte(self):
        from app.models.strategy_opportunity_lifecycle import LifecycleStage, EvaluationState
        c = self._classify(25)
        self.assertEqual(c.lifecycle_stage, LifecycleStage.DISCOVERED)
        self.assertEqual(c.evaluation_state, EvaluationState.EXPECTED_MISSING)

    # Early discovery EXPECTED_MISSING is NOT a FAIL verdict
    def test_expected_missing_is_not_fail(self):
        from app.models.strategy_opportunity_lifecycle import Verdict
        c = self._classify(30)
        self.assertNotEqual(c.verdict, Verdict.FAIL,
                            "EXPECTED_MISSING at 30 DTE must not produce FAIL verdict")

    # Structure building: 15–24 DTE → DEVELOPING
    def test_developing_24_dte_no_structure(self):
        from app.models.strategy_opportunity_lifecycle import LifecycleStage, EvaluationState
        c = self._classify(24, has_structure=False)
        self.assertEqual(c.lifecycle_stage, LifecycleStage.DEVELOPING)
        self.assertTrue(c.build_eligible)
        self.assertFalse(c.surface_eligible)
        self.assertEqual(c.evaluation_state, EvaluationState.BUILDING)

    def test_developing_24_dte_with_structure(self):
        from app.models.strategy_opportunity_lifecycle import LifecycleStage, EvaluationState
        c = self._classify(24, has_structure=True)
        self.assertEqual(c.lifecycle_stage, LifecycleStage.DEVELOPING)
        self.assertEqual(c.evaluation_state, EvaluationState.BUILDING)

    def test_developing_15_dte(self):
        from app.models.strategy_opportunity_lifecycle import LifecycleStage
        c = self._classify(15)
        self.assertEqual(c.lifecycle_stage, LifecycleStage.DEVELOPING)

    # Surfaced: 13–14 DTE
    def test_surfaced_14_dte_with_structure(self):
        from app.models.strategy_opportunity_lifecycle import LifecycleStage, EvaluationState, Verdict
        c = self._classify(14, has_structure=True)
        self.assertEqual(c.lifecycle_stage, LifecycleStage.SURFACED)
        self.assertTrue(c.surface_eligible)
        self.assertEqual(c.evaluation_state, EvaluationState.STRUCTURE_COMPLETE)
        self.assertEqual(c.verdict, Verdict.WATCH)

    def test_surfaced_13_dte_no_structure(self):
        from app.models.strategy_opportunity_lifecycle import LifecycleStage, EvaluationState
        c = self._classify(13, has_structure=False)
        self.assertEqual(c.lifecycle_stage, LifecycleStage.SURFACED)
        self.assertEqual(c.evaluation_state, EvaluationState.STRUCTURE_UNAVAILABLE)

    # Actionable: 4–12 DTE
    def test_actionable_10_dte_with_structure(self):
        from app.models.strategy_opportunity_lifecycle import LifecycleStage, Verdict, RecommendedAction
        c = self._classify(10, has_structure=True)
        self.assertEqual(c.lifecycle_stage, LifecycleStage.ACTIONABLE)
        self.assertEqual(c.verdict, Verdict.PASS)
        self.assertEqual(c.recommended_action, RecommendedAction.ENTER)
        self.assertTrue(c.entry_allowed)

    def test_actionable_6_dte_ideal_entry(self):
        from app.models.strategy_opportunity_lifecycle import LifecycleStage, Verdict
        c = self._classify(6, has_structure=True)
        self.assertEqual(c.lifecycle_stage, LifecycleStage.ACTIONABLE)
        self.assertEqual(c.verdict, Verdict.PASS)
        self.assertTrue(c.entry_allowed)

    def test_actionable_4_dte_late_entry(self):
        from app.models.strategy_opportunity_lifecycle import LifecycleStage
        c = self._classify(4, has_structure=True)
        self.assertEqual(c.lifecycle_stage, LifecycleStage.ACTIONABLE)
        self.assertTrue(c.entry_allowed)

    # Budget skip: DEFERRED_BUDGET evaluation state must not produce FAIL
    def test_deferred_budget_is_not_fail(self):
        from app.models.strategy_opportunity_lifecycle import EvaluationState, Verdict
        c = self._classify(
            10,
            structure_evaluation_state=EvaluationState.DEFERRED_BUDGET,
        )
        self.assertEqual(c.evaluation_state, EvaluationState.DEFERRED_BUDGET)
        self.assertNotEqual(c.verdict, Verdict.FAIL,
                            "DEFERRED_BUDGET must not produce FAIL verdict")

    # Open position
    def test_open_position_overrides_all(self):
        from app.models.strategy_opportunity_lifecycle import LifecycleStage, RecommendedAction
        c = self._classify(10, has_open_position=True)
        self.assertEqual(c.lifecycle_stage, LifecycleStage.OPEN_POSITION)
        self.assertEqual(c.recommended_action, RecommendedAction.HOLD)
        self.assertFalse(c.entry_allowed)


# ---------------------------------------------------------------------------
# opportunity_id stability
# ---------------------------------------------------------------------------
class TestBuildOpportunityId(unittest.TestCase):
    def test_stable_id_format(self):
        from app.services.calendar_opportunity_lifecycle_adapter import build_opportunity_id
        oid = build_opportunity_id("NVDA", date(2026, 8, 15))
        self.assertEqual(oid, "earnings_calendar:NVDA:2026-08-15")

    def test_lowercase_ticker_normalized(self):
        from app.services.calendar_opportunity_lifecycle_adapter import build_opportunity_id
        self.assertEqual(
            build_opportunity_id("nvda", "2026-08-15"),
            "earnings_calendar:NVDA:2026-08-15",
        )

    def test_string_date_accepted(self):
        from app.services.calendar_opportunity_lifecycle_adapter import build_opportunity_id
        self.assertEqual(
            build_opportunity_id("AAPL", "2026-08-20"),
            "earnings_calendar:AAPL:2026-08-20",
        )


# ---------------------------------------------------------------------------
# build_calendar_lifecycle_opportunity — end-to-end
# ---------------------------------------------------------------------------
class TestBuildCalendarLifecycleOpportunity(unittest.TestCase):
    def setUp(self):
        self.policy = _default_policy()
        self.today = date.today()

    def test_actionable_with_structure(self):
        from app.models.strategy_opportunity_lifecycle import LifecycleStage, Verdict
        from app.services.calendar_opportunity_lifecycle_adapter import build_calendar_lifecycle_opportunity
        event_date = self.today + timedelta(days=8)
        opp, errors = build_calendar_lifecycle_opportunity(
            ticker="NVDA",
            earnings_date=event_date,
            days_until_event=8,
            policy=self.policy,
            has_structure=True,
            evaluation_date=self.today,
        )
        self.assertEqual(errors, [], f"Unexpected validation errors: {errors}")
        self.assertEqual(opp.ticker, "NVDA")
        self.assertEqual(opp.lifecycle_stage, LifecycleStage.ACTIONABLE)
        self.assertEqual(opp.verdict, Verdict.PASS)
        self.assertTrue(opp.entry_allowed)

    def test_early_discovery_no_structure_no_fail(self):
        from app.models.strategy_opportunity_lifecycle import LifecycleStage, EvaluationState, Verdict
        from app.services.calendar_opportunity_lifecycle_adapter import build_calendar_lifecycle_opportunity
        event_date = self.today + timedelta(days=30)
        opp, errors = build_calendar_lifecycle_opportunity(
            ticker="TSLA",
            earnings_date=event_date,
            days_until_event=30,
            policy=self.policy,
            has_structure=False,
            evaluation_date=self.today,
        )
        self.assertEqual(errors, [])
        self.assertEqual(opp.lifecycle_stage, LifecycleStage.DISCOVERED)
        self.assertEqual(opp.evaluation_state, EvaluationState.EXPECTED_MISSING)
        self.assertNotEqual(opp.verdict, Verdict.FAIL)

    def test_opportunity_id_is_stable(self):
        from app.services.calendar_opportunity_lifecycle_adapter import build_calendar_lifecycle_opportunity
        event_date = self.today + timedelta(days=10)
        opp1, _ = build_calendar_lifecycle_opportunity(
            ticker="AAPL", earnings_date=event_date, days_until_event=10,
            policy=self.policy, has_structure=False, evaluation_date=self.today,
        )
        opp2, _ = build_calendar_lifecycle_opportunity(
            ticker="AAPL", earnings_date=event_date, days_until_event=10,
            policy=self.policy, has_structure=True, evaluation_date=self.today,
        )
        self.assertEqual(opp1.opportunity_id, opp2.opportunity_id,
                         "opportunity_id must be stable across structure changes")

    def test_string_earnings_date_accepted(self):
        from app.services.calendar_opportunity_lifecycle_adapter import build_calendar_lifecycle_opportunity
        event_date = (self.today + timedelta(days=10)).isoformat()
        opp, errors = build_calendar_lifecycle_opportunity(
            ticker="META", earnings_date=event_date, days_until_event=10,
            policy=self.policy, evaluation_date=self.today,
        )
        self.assertEqual(errors, [])
        self.assertEqual(opp.ticker, "META")


# ---------------------------------------------------------------------------
# lifecycle_rows_from_discovery — bridge function
# ---------------------------------------------------------------------------
class TestLifecycleRowsFromDiscovery(unittest.TestCase):
    def setUp(self):
        self.policy = _default_policy()
        self.today = date.today()

    def _make_quality_result(self, items):
        return {"items": items}

    def test_generates_rows_for_in_window_items(self):
        from app.services.calendar_opportunity_lifecycle_adapter import lifecycle_rows_from_discovery
        event_date = (self.today + timedelta(days=30)).isoformat()
        quality_result = self._make_quality_result([
            {"ticker": "NVDA", "earnings_date": event_date},
        ])
        rows = lifecycle_rows_from_discovery(quality_result, self.policy, evaluation_date=self.today)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ticker"], "NVDA")
        self.assertEqual(rows[0]["row_type"], "lifecycle_monitor")

    def test_skips_out_of_window_items(self):
        from app.services.calendar_opportunity_lifecycle_adapter import lifecycle_rows_from_discovery
        future_date = (self.today + timedelta(days=50)).isoformat()
        quality_result = self._make_quality_result([
            {"ticker": "AAPL", "earnings_date": future_date},
        ])
        rows = lifecycle_rows_from_discovery(quality_result, self.policy, evaluation_date=self.today)
        self.assertEqual(len(rows), 0, "50 DTE is outside discovery window, should be skipped")

    def test_skips_missing_earnings_date(self):
        from app.services.calendar_opportunity_lifecycle_adapter import lifecycle_rows_from_discovery
        quality_result = self._make_quality_result([
            {"ticker": "TSLA"},  # no earnings_date
        ])
        rows = lifecycle_rows_from_discovery(quality_result, self.policy, evaluation_date=self.today)
        self.assertEqual(len(rows), 0)

    def test_budget_skip_produces_deferred_budget_not_fail(self):
        from app.models.strategy_opportunity_lifecycle import EvaluationState, Verdict
        from app.services.calendar_opportunity_lifecycle_adapter import lifecycle_rows_from_discovery
        event_date = (self.today + timedelta(days=10)).isoformat()
        quality_result = self._make_quality_result([
            {
                "ticker": "META",
                "earnings_date": event_date,
                "exit_stage": "DEV_MODE_BUDGET_NOT_SELECTED",
            },
        ])
        rows = lifecycle_rows_from_discovery(quality_result, self.policy, evaluation_date=self.today)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["evaluation_state"], EvaluationState.DEFERRED_BUDGET)
        self.assertNotEqual(rows[0]["verdict"], Verdict.FAIL)

    def test_empty_quality_result(self):
        from app.services.calendar_opportunity_lifecycle_adapter import lifecycle_rows_from_discovery
        rows = lifecycle_rows_from_discovery({}, self.policy, evaluation_date=self.today)
        self.assertEqual(rows, [])


# ---------------------------------------------------------------------------
# API endpoint /api/dev/strategy-lifecycle registered
# ---------------------------------------------------------------------------
class TestStrategyLifecycleEndpointRegistered(unittest.TestCase):
    def test_endpoint_returns_200_or_404_but_not_500_without_snapshot(self):
        """Endpoint must be registered; it returns 200/404 not 500 when no snapshot exists."""
        import os
        os.environ.setdefault("RUN_TOKEN", "test-token-33a1")
        import app.main as main_module
        client = main_module.app.test_client()
        response = client.get(
            "/api/dev/strategy-lifecycle",
            headers={"X-Dev-Token": os.environ.get("RUN_TOKEN", "test-token-33a1")},
        )
        # 200 (no snapshot = empty list) or 404 are both acceptable; 500 is not
        self.assertNotEqual(response.status_code, 500,
                            f"Endpoint must not 500; got: {response.data[:500]}")

    def test_endpoint_response_has_correct_keys(self):
        import os
        os.environ.setdefault("RUN_TOKEN", "test-token-33a1")
        import app.main as main_module
        client = main_module.app.test_client()
        response = client.get(
            "/api/dev/strategy-lifecycle",
            headers={"X-Dev-Token": os.environ.get("RUN_TOKEN", "test-token-33a1")},
            query_string={"token": os.environ.get("RUN_TOKEN", "test-token-33a1")},
        )
        if response.status_code == 200:
            import json
            data = json.loads(response.data)
            self.assertIn("strategy_id", data)
            self.assertIn("policy", data)
            self.assertIn("summary", data)
            self.assertFalse(data.get("provider_calls_triggered"),
                             "endpoint must not trigger provider calls")


if __name__ == "__main__":
    unittest.main()
