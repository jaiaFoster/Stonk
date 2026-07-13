"""ASA Patch 33A.1 — Strategy Opportunity Lifecycle Kernel Tests

Covers:
- Generic LifecycleStage, EvaluationState, Verdict, RecommendedAction enums
- LifecycleClassification invariant validation
- CalendarEvolutionPolicy construction and ordering invariants
- load_calendar_evolution_policy() config integration
- build_strategy_opportunity() canonical construction
- summarize_lifecycle_batch() output shape
- Config fix: EARNINGS_DISCOVERY_END_DAYS no longer hardcoded to 21
"""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Lifecycle stage / state / verdict enum coverage
# ---------------------------------------------------------------------------
class TestLifecycleStageEnum(unittest.TestCase):
    def test_all_stages_are_valid(self):
        from app.models.strategy_opportunity_lifecycle import LifecycleStage
        for stage in [
            LifecycleStage.OUTSIDE_WINDOW, LifecycleStage.DISCOVERED,
            LifecycleStage.DEVELOPING, LifecycleStage.SURFACED,
            LifecycleStage.ACTIONABLE, LifecycleStage.OPEN_POSITION,
            LifecycleStage.POST_EVENT, LifecycleStage.INVALIDATED,
            LifecycleStage.TERMINAL,
        ]:
            self.assertTrue(LifecycleStage.is_valid(stage), f"{stage} should be valid")

    def test_unknown_stage_is_invalid(self):
        from app.models.strategy_opportunity_lifecycle import LifecycleStage
        self.assertFalse(LifecycleStage.is_valid("MADE_UP_STAGE"))

    def test_active_stages(self):
        from app.models.strategy_opportunity_lifecycle import LifecycleStage
        self.assertTrue(LifecycleStage.is_active(LifecycleStage.DISCOVERED))
        self.assertTrue(LifecycleStage.is_active(LifecycleStage.ACTIONABLE))
        self.assertFalse(LifecycleStage.is_active(LifecycleStage.POST_EVENT))
        self.assertFalse(LifecycleStage.is_active(LifecycleStage.OUTSIDE_WINDOW))


class TestEvaluationStateEnum(unittest.TestCase):
    def test_non_failure_states(self):
        from app.models.strategy_opportunity_lifecycle import EvaluationState
        self.assertTrue(EvaluationState.is_non_failure(EvaluationState.EXPECTED_MISSING))
        self.assertTrue(EvaluationState.is_non_failure(EvaluationState.DEFERRED_BUDGET))
        self.assertTrue(EvaluationState.is_non_failure(EvaluationState.NOT_REQUESTED))
        self.assertFalse(EvaluationState.is_non_failure(EvaluationState.STRUCTURE_UNAVAILABLE))
        self.assertFalse(EvaluationState.is_non_failure(EvaluationState.ERROR))


class TestVerdictEnum(unittest.TestCase):
    def test_all_verdicts_valid(self):
        from app.models.strategy_opportunity_lifecycle import Verdict
        for v in [Verdict.NOT_EVALUATED, Verdict.PASS, Verdict.WATCH,
                  Verdict.NEAR_MISS, Verdict.FAIL, Verdict.BLOCKED]:
            self.assertTrue(Verdict.is_valid(v))

    def test_unknown_verdict_invalid(self):
        from app.models.strategy_opportunity_lifecycle import Verdict
        self.assertFalse(Verdict.is_valid("UNKNOWN_VERDICT"))


# ---------------------------------------------------------------------------
# LifecycleClassification invariant validation
# ---------------------------------------------------------------------------
class TestLifecycleClassificationValidation(unittest.TestCase):
    def _valid_classification(self, **overrides):
        from app.models.strategy_opportunity_lifecycle import LifecycleClassification, LifecycleStage, EvaluationState, Verdict, RecommendedAction
        defaults = dict(
            lifecycle_stage=LifecycleStage.ACTIONABLE,
            evaluation_state=EvaluationState.STRUCTURE_COMPLETE,
            verdict=Verdict.PASS,
            recommended_action=RecommendedAction.ENTER,
            build_eligible=True,
            surface_eligible=True,
            entry_allowed=True,
            classification_reason="test",
        )
        defaults.update(overrides)
        return LifecycleClassification(**defaults)

    def test_valid_classification_passes(self):
        from app.services.strategy_opportunity_lifecycle_service import validate_lifecycle_classification
        c = self._valid_classification()
        self.assertEqual(validate_lifecycle_classification(c), [])

    def test_unknown_stage_fails(self):
        from app.services.strategy_opportunity_lifecycle_service import validate_lifecycle_classification
        c = self._valid_classification(lifecycle_stage="BOGUS")
        errors = validate_lifecycle_classification(c)
        self.assertTrue(any("lifecycle_stage" in e for e in errors))

    def test_deferred_budget_with_fail_verdict_is_invalid(self):
        from app.models.strategy_opportunity_lifecycle import Verdict, EvaluationState
        from app.services.strategy_opportunity_lifecycle_service import validate_lifecycle_classification
        c = self._valid_classification(
            evaluation_state=EvaluationState.DEFERRED_BUDGET,
            verdict=Verdict.FAIL,
            entry_allowed=False,
        )
        errors = validate_lifecycle_classification(c)
        self.assertTrue(any("FAIL" in e and "DEFERRED_BUDGET" in e for e in errors), errors)

    def test_expected_missing_with_fail_verdict_is_invalid(self):
        from app.models.strategy_opportunity_lifecycle import Verdict, EvaluationState
        from app.services.strategy_opportunity_lifecycle_service import validate_lifecycle_classification
        c = self._valid_classification(
            evaluation_state=EvaluationState.EXPECTED_MISSING,
            verdict=Verdict.FAIL,
            entry_allowed=False,
            build_eligible=False,
            surface_eligible=False,
        )
        errors = validate_lifecycle_classification(c)
        self.assertTrue(any("FAIL" in e for e in errors), errors)

    def test_entry_allowed_requires_surface_eligible(self):
        from app.services.strategy_opportunity_lifecycle_service import validate_lifecycle_classification
        c = self._valid_classification(entry_allowed=True, surface_eligible=False)
        errors = validate_lifecycle_classification(c)
        self.assertTrue(any("entry_allowed" in e for e in errors))

    def test_surface_eligible_requires_build_eligible(self):
        from app.services.strategy_opportunity_lifecycle_service import validate_lifecycle_classification
        c = self._valid_classification(surface_eligible=True, build_eligible=False, entry_allowed=False)
        errors = validate_lifecycle_classification(c)
        self.assertTrue(any("surface_eligible" in e for e in errors))

    def test_post_event_entry_allowed_is_invalid(self):
        from app.models.strategy_opportunity_lifecycle import LifecycleStage
        from app.services.strategy_opportunity_lifecycle_service import validate_lifecycle_classification
        c = self._valid_classification(lifecycle_stage=LifecycleStage.POST_EVENT, entry_allowed=True)
        errors = validate_lifecycle_classification(c)
        self.assertTrue(any("entry_allowed" in e for e in errors))


# ---------------------------------------------------------------------------
# CalendarEvolutionPolicy construction
# ---------------------------------------------------------------------------
class TestCalendarEvolutionPolicy(unittest.TestCase):
    def _default_policy(self, **overrides):
        from app.models.calendar_evolution_policy import CalendarEvolutionPolicy
        defaults = dict(
            discovery_start_event_dte=0,
            discovery_end_event_dte=35,
            build_start_event_dte=24,
            surface_start_event_dte=14,
            ideal_entry_min_event_dte=6,
            ideal_entry_max_event_dte=12,
            late_entry_event_dte=4,
        )
        defaults.update(overrides)
        return CalendarEvolutionPolicy(**defaults)

    def test_valid_policy_constructs(self):
        p = self._default_policy()
        self.assertEqual(p.discovery_end_event_dte, 35)
        self.assertEqual(p.build_start_event_dte, 24)
        self.assertEqual(p.surface_start_event_dte, 14)
        self.assertEqual(p.policy_version, "33A.1.calendar.v1")

    def test_inverted_discovery_end_build_start_raises(self):
        """discovery_end must be >= build_start."""
        from app.models.calendar_evolution_policy import CalendarEvolutionPolicy
        with self.assertRaises(ValueError):
            CalendarEvolutionPolicy(
                discovery_start_event_dte=0,
                discovery_end_event_dte=20,  # less than build_start=24
                build_start_event_dte=24,
                surface_start_event_dte=14,
                ideal_entry_min_event_dte=6,
                ideal_entry_max_event_dte=12,
                late_entry_event_dte=4,
            )

    def test_inverted_build_surface_raises(self):
        """build_start must be >= surface_start."""
        from app.models.calendar_evolution_policy import CalendarEvolutionPolicy
        with self.assertRaises(ValueError):
            CalendarEvolutionPolicy(
                discovery_start_event_dte=0,
                discovery_end_event_dte=35,
                build_start_event_dte=10,  # less than surface_start=14
                surface_start_event_dte=14,
                ideal_entry_min_event_dte=6,
                ideal_entry_max_event_dte=12,
                late_entry_event_dte=4,
            )

    def test_inverted_entry_min_max_raises(self):
        from app.models.calendar_evolution_policy import CalendarEvolutionPolicy
        with self.assertRaises(ValueError):
            CalendarEvolutionPolicy(
                discovery_start_event_dte=0,
                discovery_end_event_dte=35,
                build_start_event_dte=24,
                surface_start_event_dte=14,
                ideal_entry_min_event_dte=13,  # greater than max=12
                ideal_entry_max_event_dte=12,
                late_entry_event_dte=4,
            )

    def test_negative_discovery_start_raises(self):
        from app.models.calendar_evolution_policy import CalendarEvolutionPolicy
        with self.assertRaises(ValueError):
            CalendarEvolutionPolicy(
                discovery_start_event_dte=-1,
                discovery_end_event_dte=35,
                build_start_event_dte=24,
                surface_start_event_dte=14,
                ideal_entry_min_event_dte=6,
                ideal_entry_max_event_dte=12,
                late_entry_event_dte=4,
            )

    def test_is_in_discovery_window(self):
        p = self._default_policy()
        self.assertTrue(p.is_in_discovery_window(0))
        self.assertTrue(p.is_in_discovery_window(20))
        self.assertTrue(p.is_in_discovery_window(35))
        self.assertFalse(p.is_in_discovery_window(36))
        self.assertFalse(p.is_in_discovery_window(-1))

    def test_is_build_eligible(self):
        p = self._default_policy()
        self.assertTrue(p.is_build_eligible(24))
        self.assertTrue(p.is_build_eligible(10))
        self.assertTrue(p.is_build_eligible(0))
        self.assertFalse(p.is_build_eligible(25))

    def test_is_surface_eligible(self):
        p = self._default_policy()
        self.assertTrue(p.is_surface_eligible(14))
        self.assertFalse(p.is_surface_eligible(15))

    def test_is_ideal_entry(self):
        p = self._default_policy()
        self.assertTrue(p.is_ideal_entry(6))
        self.assertTrue(p.is_ideal_entry(12))
        self.assertFalse(p.is_ideal_entry(5))
        self.assertFalse(p.is_ideal_entry(13))

    def test_is_late_entry(self):
        p = self._default_policy()
        self.assertTrue(p.is_late_entry(4))
        self.assertTrue(p.is_late_entry(5))
        self.assertFalse(p.is_late_entry(6))  # ideal, not late
        self.assertFalse(p.is_late_entry(3))  # too late

    def test_to_dict_has_all_keys(self):
        p = self._default_policy()
        d = p.to_dict()
        for key in ["policy_version", "discovery_start_event_dte", "discovery_end_event_dte",
                    "build_start_event_dte", "surface_start_event_dte",
                    "ideal_entry_min_event_dte", "ideal_entry_max_event_dte", "late_entry_event_dte"]:
            self.assertIn(key, d)


# ---------------------------------------------------------------------------
# load_calendar_evolution_policy — config integration
# ---------------------------------------------------------------------------
class TestLoadCalendarEvolutionPolicy(unittest.TestCase):
    def test_loads_from_config(self):
        from app.models.calendar_evolution_policy import load_calendar_evolution_policy
        policy = load_calendar_evolution_policy()
        # Verify it loaded without error and has correct version
        self.assertEqual(policy.policy_version, "33A.1.calendar.v1")

    def test_default_discovery_end_is_35(self):
        """Patch 33A.1: EARNINGS_DISCOVERY_END_DAYS default must be 35, not 21."""
        from app import config
        self.assertEqual(config.EARNINGS_DISCOVERY_END_DAYS, 35,
                         "EARNINGS_DISCOVERY_END_DAYS must default to 35 (Patch 33A.1 fix)")

    def test_default_discovery_start_is_0(self):
        """Patch 33A.1: EARNINGS_DISCOVERY_START_DAYS default must be 0, not 4."""
        from app import config
        self.assertEqual(config.EARNINGS_DISCOVERY_START_DAYS, 0,
                         "EARNINGS_DISCOVERY_START_DAYS must default to 0 (Patch 33A.1 fix)")

    def test_window_end_alias_matches(self):
        """EARNINGS_DISCOVERY_WINDOW_END_DAYS must alias EARNINGS_DISCOVERY_END_DAYS."""
        from app import config
        self.assertEqual(config.EARNINGS_DISCOVERY_WINDOW_END_DAYS, config.EARNINGS_DISCOVERY_END_DAYS)

    def test_build_start_configured(self):
        from app import config
        self.assertEqual(config.CALENDAR_STRUCTURE_BUILD_START_EVENT_DTE, 24)

    def test_surface_start_configured(self):
        from app import config
        self.assertEqual(config.CALENDAR_SURFACE_START_EVENT_DTE, 14)


# ---------------------------------------------------------------------------
# build_strategy_opportunity — canonical construction
# ---------------------------------------------------------------------------
class TestBuildStrategyOpportunity(unittest.TestCase):
    def _make_classification(self, **overrides):
        from app.models.strategy_opportunity_lifecycle import (
            LifecycleClassification, LifecycleStage, EvaluationState, Verdict, RecommendedAction,
        )
        defaults = dict(
            lifecycle_stage=LifecycleStage.ACTIONABLE,
            evaluation_state=EvaluationState.STRUCTURE_COMPLETE,
            verdict=Verdict.PASS,
            recommended_action=RecommendedAction.ENTER,
            build_eligible=True,
            surface_eligible=True,
            entry_allowed=True,
            classification_reason="test",
        )
        defaults.update(overrides)
        return LifecycleClassification(**defaults)

    def test_builds_opportunity_with_correct_fields(self):
        from app.services.strategy_opportunity_lifecycle_service import build_strategy_opportunity
        today = date.today()
        event_date = today + timedelta(days=10)
        c = self._make_classification()
        opp = build_strategy_opportunity(
            opportunity_id="earnings_calendar:NVDA:2026-08-01",
            strategy_id="earnings_calendar",
            ticker="NVDA",
            classification=c,
            event_date=event_date,
            evaluation_date=today,
        )
        self.assertEqual(opp.ticker, "NVDA")
        self.assertEqual(opp.strategy_id, "earnings_calendar")
        self.assertEqual(opp.clock.days_until_event, 10)
        self.assertTrue(opp.entry_allowed)
        self.assertTrue(opp.surface_eligible)

    def test_to_dict_serializes(self):
        from app.services.strategy_opportunity_lifecycle_service import build_strategy_opportunity
        today = date.today()
        event_date = today + timedelta(days=8)
        c = self._make_classification()
        opp = build_strategy_opportunity(
            opportunity_id="earnings_calendar:AAPL:2026-08-01",
            strategy_id="earnings_calendar",
            ticker="AAPL",
            classification=c,
            event_date=event_date,
        )
        d = opp.to_dict()
        self.assertIn("opportunity_id", d)
        self.assertIn("lifecycle_stage", d)
        self.assertIn("clock", d)
        self.assertIn("event_date", d["clock"])


# ---------------------------------------------------------------------------
# summarize_lifecycle_batch
# ---------------------------------------------------------------------------
class TestSummarizeLifecycleBatch(unittest.TestCase):
    def _make_opp(self, ticker, stage, state, verdict, entry_allowed=False, surface_eligible=False):
        from app.models.strategy_opportunity_lifecycle import (
            LifecycleClassification, RecommendedAction, StrategyOpportunity, OpportunityClock,
        )
        today = date.today()
        clock = OpportunityClock(
            event_date=today + timedelta(days=10),
            days_until_event=10,
            evaluation_date=today,
        )
        return StrategyOpportunity(
            opportunity_id=f"earnings_calendar:{ticker}:2026-08-01",
            strategy_id="earnings_calendar",
            ticker=ticker,
            lifecycle_stage=stage,
            evaluation_state=state,
            verdict=verdict,
            recommended_action=RecommendedAction.MONITOR,
            clock=clock,
            build_eligible=True,
            surface_eligible=surface_eligible,
            entry_allowed=entry_allowed,
        )

    def test_summary_counts(self):
        from app.models.strategy_opportunity_lifecycle import LifecycleStage, EvaluationState, Verdict
        from app.services.strategy_opportunity_lifecycle_service import summarize_lifecycle_batch
        opps = [
            self._make_opp("NVDA", LifecycleStage.ACTIONABLE, EvaluationState.STRUCTURE_COMPLETE, Verdict.PASS, entry_allowed=True, surface_eligible=True),
            self._make_opp("AAPL", LifecycleStage.DISCOVERED, EvaluationState.EXPECTED_MISSING, Verdict.NOT_EVALUATED),
            self._make_opp("TSLA", LifecycleStage.DEVELOPING, EvaluationState.BUILDING, Verdict.NOT_EVALUATED),
        ]
        s = summarize_lifecycle_batch(opps)
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["entry_allowed_count"], 1)
        self.assertEqual(s["surface_eligible_count"], 1)
        self.assertEqual(s["by_lifecycle_stage"][LifecycleStage.ACTIONABLE], 1)
        self.assertEqual(s["by_evaluation_state"][EvaluationState.EXPECTED_MISSING], 1)

    def test_empty_batch(self):
        from app.services.strategy_opportunity_lifecycle_service import summarize_lifecycle_batch
        s = summarize_lifecycle_batch([])
        self.assertEqual(s["total"], 0)
        self.assertEqual(s["entry_allowed_count"], 0)


# ---------------------------------------------------------------------------
# project_to_strategy_row
# ---------------------------------------------------------------------------
class TestProjectToStrategyRow(unittest.TestCase):
    def test_projects_to_flat_dict(self):
        from app.models.strategy_opportunity_lifecycle import (
            LifecycleClassification, LifecycleStage, EvaluationState, Verdict, RecommendedAction,
        )
        from app.services.strategy_opportunity_lifecycle_service import (
            build_strategy_opportunity, project_to_strategy_row,
        )
        today = date.today()
        c = LifecycleClassification(
            lifecycle_stage=LifecycleStage.SURFACED,
            evaluation_state=EvaluationState.STRUCTURE_COMPLETE,
            verdict=Verdict.WATCH,
            recommended_action=RecommendedAction.PREPARE,
            build_eligible=True,
            surface_eligible=True,
            entry_allowed=False,
            classification_reason="test",
        )
        opp = build_strategy_opportunity(
            opportunity_id="earnings_calendar:META:2026-08-15",
            strategy_id="earnings_calendar",
            ticker="META",
            classification=c,
            event_date=today + timedelta(days=13),
            evaluation_date=today,
        )
        row = project_to_strategy_row(opp)
        self.assertEqual(row["ticker"], "META")
        self.assertEqual(row["lifecycle_stage"], LifecycleStage.SURFACED)
        self.assertIn("event_date", row)
        self.assertIn("days_until_event", row)


if __name__ == "__main__":
    unittest.main()
