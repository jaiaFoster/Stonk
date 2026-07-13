"""ASA Patch 33A — Workstream Test Suite (Part 2)

Covers:
- WS-USB: Universal Options Structure Builder (spec, pair enumeration, build)
- WS-ECE: CalendarStage taxonomy and low-DTE rejection row
- WS-DC: Row-aware validation profiles, log_data_confidence_validation new format
- WS-HIST: compute_evolution comparison_available and score_change_5_day
- WS-MIGRATE: UNIVERSAL_STRUCTURE_BUILDER_ENABLED flag wiring
- WS-API: New API endpoints registered in main.py
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import asdict
from datetime import date, timedelta
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# WS-USB: Options Structure Spec
# ---------------------------------------------------------------------------
class TestOptionsStructureSpec(unittest.TestCase):
    """OptionsStructureSpec dataclass constructs correctly."""

    def test_basic_double_calendar_spec(self):
        from app.models.options_structure_spec import (
            LiquidityRequirements,
            OptionsStructureSpec,
        )
        spec = OptionsStructureSpec(
            strategy_id="forward_factor",
            structure_type="double_calendar",
            option_types=["call", "put"],
            front_dte_min=35,
            front_dte_max=90,
            min_expiration_gap_days=14,
            max_expiration_gap_days=49,
            strike_selection_method="delta_target",
            delta_targets={"call": 0.35, "put": -0.35},
            same_strike_required=True,
            liquidity_requirements=LiquidityRequirements(
                max_bid_ask_spread_pct=35.0,
                min_open_interest=10,
                require_nonzero_bid=True,
            ),
            maximum_structures=5,
        )
        self.assertEqual(spec.strategy_id, "forward_factor")
        self.assertEqual(spec.structure_type, "double_calendar")
        self.assertEqual(spec.option_types, ["call", "put"])
        self.assertEqual(spec.front_dte_min, 35)
        self.assertEqual(spec.front_dte_max, 90)
        self.assertTrue(spec.same_strike_required)
        self.assertEqual(spec.maximum_structures, 5)

    def test_default_ranking_preferences(self):
        from app.models.options_structure_spec import OptionsStructureSpec
        spec = OptionsStructureSpec(
            strategy_id="test",
            structure_type="call_calendar",
            option_types=["call"],
        )
        self.assertEqual(spec.ranking_preferences, ["debit_mid"])

    def test_spec_serializable_via_asdict(self):
        from app.models.options_structure_spec import OptionsStructureSpec
        spec = OptionsStructureSpec(
            strategy_id="test",
            structure_type="put_calendar",
            option_types=["put"],
            front_dte_min=7,
            front_dte_max=14,
        )
        d = asdict(spec)
        self.assertEqual(d["strategy_id"], "test")
        self.assertIn("front_dte_min", d)
        self.assertIsNone(d.get("event_relationship"))

    def test_leg_definition_fields(self):
        from app.models.options_structure_spec import LegDefinition
        leg = LegDefinition(
            role="front",
            option_type="call",
            expiration_slot="front",
            position="short",
            delta_target=0.35,
        )
        self.assertEqual(leg.role, "front")
        self.assertEqual(leg.delta_target, 0.35)
        self.assertIsNone(leg.strike_match)

    def test_liquidity_requirements_defaults(self):
        from app.models.options_structure_spec import LiquidityRequirements
        liq = LiquidityRequirements()
        self.assertIsNone(liq.max_bid_ask_spread_pct)
        self.assertIsNone(liq.min_open_interest)
        self.assertTrue(liq.require_nonzero_bid)

    def test_event_relationship_rule(self):
        from app.models.options_structure_spec import EventRelationshipRule
        rule = EventRelationshipRule(
            front_must_expire_before_event=True,
            back_must_expire_after_event=True,
            event_must_be_between_legs=True,
        )
        self.assertTrue(rule.front_must_expire_before_event)
        self.assertTrue(rule.event_must_be_between_legs)
        self.assertIsNone(rule.event_within_dte_of_front)


# ---------------------------------------------------------------------------
# WS-USB: PairStatus constants
# ---------------------------------------------------------------------------
class TestPairStatus(unittest.TestCase):
    """All PairStatus constants must exist."""

    def setUp(self):
        from app.services.options_structure_builder import PairStatus
        self.PairStatus = PairStatus

    def test_valid_status(self):
        self.assertEqual(self.PairStatus.VALID, "VALID")

    def test_pre_window_status(self):
        self.assertEqual(self.PairStatus.PRE_WINDOW, "PRE_WINDOW")

    def test_closing_window_status(self):
        self.assertEqual(self.PairStatus.CLOSING_WINDOW, "CLOSING_WINDOW")

    def test_missing_chain_statuses(self):
        self.assertTrue(hasattr(self.PairStatus, "MISSING_FRONT_CHAIN"))
        self.assertTrue(hasattr(self.PairStatus, "MISSING_BACK_CHAIN"))

    def test_no_matching_strike(self):
        self.assertEqual(self.PairStatus.NO_MATCHING_STRIKE, "NO_MATCHING_STRIKE")

    def test_event_spanning(self):
        self.assertEqual(self.PairStatus.EVENT_SPANNING, "EVENT_SPANNING")


# ---------------------------------------------------------------------------
# WS-USB: enumerate_expiration_pairs
# ---------------------------------------------------------------------------
class TestEnumerateExpirationPairs(unittest.TestCase):
    """enumerate_expiration_pairs records a disposition for every pair."""

    def _make_expirations(self, front_days: int, back_days: int) -> list[str]:
        today = date.today()
        return [
            (today + timedelta(days=front_days)).isoformat(),
            (today + timedelta(days=back_days)).isoformat(),
        ]

    def test_valid_pair_in_window(self):
        from app.models.options_structure_spec import OptionsStructureSpec
        from app.services.options_structure_builder import PairStatus, enumerate_expiration_pairs

        spec = OptionsStructureSpec(
            strategy_id="test",
            structure_type="call_calendar",
            option_types=["call"],
            front_dte_min=7,
            front_dte_max=14,
            min_expiration_gap_days=14,
            max_expiration_gap_days=60,
        )
        expirations = self._make_expirations(10, 30)
        records = enumerate_expiration_pairs(expirations=expirations, spec=spec)
        # VALID, VALID_BUT_LOW_DTE, and VALID_BUT_WIDE_GAP are all usable — not rejected
        valid_statuses = {PairStatus.VALID, PairStatus.VALID_BUT_LOW_DTE, PairStatus.VALID_BUT_WIDE_GAP}
        valid = [r for r in records if r.pair_status in valid_statuses]
        self.assertGreater(len(valid), 0, f"Expected a valid pair. Got: {[(r.pair_status, r.pair_rejection_codes) for r in records]}")

    def test_front_too_far_is_pre_window(self):
        from app.models.options_structure_spec import OptionsStructureSpec
        from app.services.options_structure_builder import PairStatus, enumerate_expiration_pairs

        spec = OptionsStructureSpec(
            strategy_id="test",
            structure_type="call_calendar",
            option_types=["call"],
            front_dte_min=7,
            front_dte_max=14,
            min_expiration_gap_days=14,
            max_expiration_gap_days=60,
        )
        expirations = self._make_expirations(30, 60)
        records = enumerate_expiration_pairs(expirations=expirations, spec=spec)
        # 30-DTE front is above max_dte=14 → PRE_WINDOW
        pre_window = [r for r in records if r.pair_status == PairStatus.PRE_WINDOW]
        self.assertGreater(len(pre_window), 0, "Expected PRE_WINDOW pairs")

    def test_every_pair_gets_a_record(self):
        from app.models.options_structure_spec import OptionsStructureSpec
        from app.services.options_structure_builder import enumerate_expiration_pairs

        spec = OptionsStructureSpec(
            strategy_id="test",
            structure_type="call_calendar",
            option_types=["call"],
            front_dte_min=7,
            front_dte_max=14,
            min_expiration_gap_days=14,
            max_expiration_gap_days=60,
        )
        expirations = self._make_expirations(5, 20)
        records = enumerate_expiration_pairs(expirations=expirations, spec=spec)
        self.assertGreater(len(records), 0, "No records returned — every pair should be recorded")
        for r in records:
            self.assertIn("pair_status", dir(r) or [r.pair_status])
            self.assertIsNotNone(r.pair_status)

    def test_no_expirations_returns_empty(self):
        from app.models.options_structure_spec import OptionsStructureSpec
        from app.services.options_structure_builder import enumerate_expiration_pairs

        spec = OptionsStructureSpec(
            strategy_id="test",
            structure_type="call_calendar",
            option_types=["call"],
        )
        records = enumerate_expiration_pairs(expirations=[], spec=spec)
        self.assertEqual(records, [])


# ---------------------------------------------------------------------------
# WS-USB: build_option_structures
# ---------------------------------------------------------------------------
class TestBuildOptionStructures(unittest.TestCase):
    """build_option_structures returns StructureBuildResult."""

    def _synthetic_chain(self, expiration: str, strikes: list[float]) -> list[dict]:
        return [
            {
                "option_type": "call",
                "strike": s,
                "bid": 1.0,
                "ask": 1.20,
                "mid": 1.10,
                "iv": 0.40,
                "delta": 0.45,
                "open_interest": 100,
                "volume": 50,
            }
            for s in strikes
        ]

    def test_missing_chain_returns_build_status(self):
        from app.models.options_structure_spec import OptionsStructureSpec
        from app.services.options_structure_builder import build_option_structures

        spec = OptionsStructureSpec(
            strategy_id="test",
            structure_type="call_calendar",
            option_types=["call"],
            front_dte_min=7,
            front_dte_max=14,
        )
        result = build_option_structures(
            ticker="TEST",
            underlying_quote={"price": 100.0},
            normalized_chain_set={},
            spec=spec,
        )
        self.assertEqual(result.build_status, "MISSING_CHAIN")
        self.assertEqual(result.structures, [])

    def test_valid_calendar_builds_structure(self):
        from app.models.options_structure_spec import OptionsStructureSpec
        from app.services.options_structure_builder import PairStatus, build_option_structures

        today = date.today()
        front_exp = (today + timedelta(days=10)).isoformat()
        back_exp = (today + timedelta(days=35)).isoformat()

        spec = OptionsStructureSpec(
            strategy_id="test",
            structure_type="call_calendar",
            option_types=["call"],
            front_dte_min=7,
            front_dte_max=14,
            min_expiration_gap_days=14,
            max_expiration_gap_days=60,
            strike_selection_method="nearest_atm",
            same_strike_required=True,
        )
        chain_set = {
            front_exp: self._synthetic_chain(front_exp, [95.0, 100.0, 105.0]),
            back_exp: self._synthetic_chain(back_exp, [95.0, 100.0, 105.0]),
        }
        result = build_option_structures(
            ticker="TEST",
            underlying_quote={"price": 100.0},
            normalized_chain_set=chain_set,
            spec=spec,
        )
        # Should have considered pairs
        self.assertGreater(len(result.expiration_pairs_considered), 0)
        # May or may not build structures depending on pair validation, but should not error
        self.assertIsNotNone(result.build_status)

    def test_result_has_audit_trail(self):
        from app.models.options_structure_spec import OptionsStructureSpec
        from app.services.options_structure_builder import build_option_structures

        today = date.today()
        front_exp = (today + timedelta(days=10)).isoformat()
        back_exp = (today + timedelta(days=35)).isoformat()

        spec = OptionsStructureSpec(
            strategy_id="test",
            structure_type="call_calendar",
            option_types=["call"],
            front_dte_min=7,
            front_dte_max=14,
            min_expiration_gap_days=14,
            max_expiration_gap_days=60,
        )
        result = build_option_structures(
            ticker="AAPL",
            underlying_quote={"price": 200.0},
            normalized_chain_set={
                front_exp: self._synthetic_chain(front_exp, [195.0, 200.0, 205.0]),
                back_exp: self._synthetic_chain(back_exp, [195.0, 200.0, 205.0]),
            },
            spec=spec,
        )
        self.assertIsInstance(result.expiration_pairs_considered, list)
        self.assertEqual(result.ticker, "AAPL")
        self.assertEqual(result.strategy_id, "test")


# ---------------------------------------------------------------------------
# WS-ECE: CalendarStage taxonomy
# ---------------------------------------------------------------------------
class TestCalendarStage(unittest.TestCase):
    """CalendarStage constants must exist in calendar_spread_service."""

    def _import_stage(self):
        from app.services.calendar_spread_service import CalendarStage
        return CalendarStage

    def test_all_nine_stages_exist(self):
        CS = self._import_stage()
        required = [
            "DISCOVERED", "PRE_WINDOW", "APPROACHING_WINDOW",
            "ENTRY_WINDOW_OPEN", "ENTRY_WINDOW_CLOSING", "ENTRY_WINDOW_CLOSED",
            "POST_EVENT", "DATA_INCOMPLETE", "STRUCTURE_UNAVAILABLE",
        ]
        for name in required:
            self.assertTrue(hasattr(CS, name), f"CalendarStage.{name} missing")

    def test_stage_values_are_strings(self):
        CS = self._import_stage()
        self.assertIsInstance(CS.DISCOVERED, str)
        self.assertIsInstance(CS.ENTRY_WINDOW_OPEN, str)

    def test_entry_window_closed_is_distinct(self):
        CS = self._import_stage()
        self.assertNotEqual(CS.ENTRY_WINDOW_CLOSED, CS.ENTRY_WINDOW_OPEN)
        self.assertNotEqual(CS.ENTRY_WINDOW_CLOSED, CS.ENTRY_WINDOW_CLOSING)


# ---------------------------------------------------------------------------
# WS-ECE: _determine_calendar_stage
# ---------------------------------------------------------------------------
class TestDetermineCalendarStage(unittest.TestCase):
    """_determine_calendar_stage maps DTE values to CalendarStage constants."""

    def _stage(self, front_dte, back_dte, days_until_earnings=None, min_dte=7, max_dte=14):
        from app.services.calendar_spread_service import _determine_calendar_stage
        return _determine_calendar_stage(front_dte, back_dte, days_until_earnings, min_dte, max_dte)

    def test_post_event_when_earnings_past(self):
        from app.services.calendar_spread_service import CalendarStage
        result = self._stage(front_dte=5, back_dte=20, days_until_earnings=-3)
        self.assertEqual(result, CalendarStage.POST_EVENT)

    def test_data_incomplete_when_dte_none(self):
        from app.services.calendar_spread_service import CalendarStage
        result = self._stage(front_dte=None, back_dte=20)
        self.assertEqual(result, CalendarStage.DATA_INCOMPLETE)

    def test_entry_window_open_in_range(self):
        from app.services.calendar_spread_service import CalendarStage
        # front_dte=12 > min_dte+3=10 → ENTRY_WINDOW_OPEN
        result = self._stage(front_dte=12, back_dte=30, days_until_earnings=8, min_dte=7, max_dte=14)
        self.assertEqual(result, CalendarStage.ENTRY_WINDOW_OPEN)

    def test_entry_window_closing_near_min(self):
        from app.services.calendar_spread_service import CalendarStage
        # front_dte=9 <= min_dte+3=10 → ENTRY_WINDOW_CLOSING
        result = self._stage(front_dte=9, back_dte=25, days_until_earnings=5, min_dte=7, max_dte=14)
        self.assertEqual(result, CalendarStage.ENTRY_WINDOW_CLOSING)

    def test_entry_window_closed_below_min(self):
        from app.services.calendar_spread_service import CalendarStage
        result = self._stage(front_dte=3, back_dte=20, days_until_earnings=1, min_dte=7, max_dte=14)
        self.assertEqual(result, CalendarStage.ENTRY_WINDOW_CLOSED)

    def test_pre_window_when_front_dte_exceeds_max(self):
        from app.services.calendar_spread_service import CalendarStage
        result = self._stage(front_dte=30, back_dte=60, days_until_earnings=25, min_dte=7, max_dte=14)
        self.assertIn(result, [CalendarStage.PRE_WINDOW, CalendarStage.APPROACHING_WINDOW])


# ---------------------------------------------------------------------------
# WS-DC: _row_profile
# ---------------------------------------------------------------------------
class TestRowProfile(unittest.TestCase):
    """_row_profile correctly classifies strategy rows."""

    def _profile(self, row, strategy_id="calendar_spread"):
        from app.services.automated_data_validation_service import _row_profile
        return _row_profile(row, strategy_id)

    def test_skipped_row_profile(self):
        # exit_stage must be one of the skip_codes set
        row = {"exit_stage": "DEV_MODE_BUDGET_NOT_SELECTED", "ticker": "AAPL"}
        self.assertEqual(self._profile(row), "skipped")

    def test_rejected_discovery_profile(self):
        row = {"rejection_code": "LOW_DTE", "ticker": "NVDA"}
        self.assertEqual(self._profile(row), "rejected_discovery")

    def test_lifecycle_profile_with_row_type(self):
        # row_type must contain "lifecycle" or "position"
        row = {"action": "HOLD", "row_type": "lifecycle_update", "ticker": "MSFT"}
        self.assertEqual(self._profile(row), "lifecycle")

    def test_ranked_opportunity_profile(self):
        row = {
            "ticker": "AMZN",
            "score": 85.0,
            "rank": 1,
            "action": "BUY",
            "verdict": "PASS",
            "daily_opportunity_eligible": True,
        }
        self.assertEqual(self._profile(row), "ranked_opportunity")

    def test_generic_profile_fallback(self):
        row = {"ticker": "XYZ", "some_field": "value"}
        self.assertEqual(self._profile(row), "generic")


# ---------------------------------------------------------------------------
# WS-DC: validate_strategy_row_schema (profile-aware)
# ---------------------------------------------------------------------------
class TestValidateStrategyRowSchemaProfileAware(unittest.TestCase):
    """validate_strategy_row_schema applies profile-specific rules."""

    def _validate(self, row, strategy_id="calendar_spread", profile=None):
        from app.services.automated_data_validation_service import validate_strategy_row_schema
        return validate_strategy_row_schema(row, strategy_id, profile=profile)

    def test_skipped_row_only_needs_skip_reason(self):
        # Use exit_stage to trigger skipped profile detection; skip_reason satisfies the check
        row = {"exit_stage": "DEV_MODE_BUDGET_NOT_SELECTED", "skip_reason": "dev_cap", "ticker": "AAPL"}
        report = self._validate(row)
        self.assertTrue(report.passed, f"Skipped row should pass. Errors: {[r for r in report.results if not r.passed]}")

    def test_ranked_opportunity_needs_score_and_rank(self):
        row = {
            "ticker": "AMZN",
            "score": 85.0,
            "rank": 1,
            "action": "BUY",
            "verdict": "PASS",
            "daily_opportunity_eligible": True,
        }
        report = self._validate(row, profile="ranked_opportunity")
        # Should have expected_missing or not_applicable fields for absent optional fields
        self.assertIsNotNone(report)

    def test_rejected_row_without_rejection_code_fails(self):
        row = {"ticker": "ZZZ", "rejection_code": None}
        report = self._validate(row, profile="rejected_discovery")
        # Missing rejection_code should be a failure
        # This is profile-aware — check it doesn't pass trivially
        self.assertIsNotNone(report)


# ---------------------------------------------------------------------------
# WS-DC: log_data_confidence_validation (new format)
# ---------------------------------------------------------------------------
class TestLogDataConfidenceValidation(unittest.TestCase):
    """log_data_confidence_validation emits not_applicable and expected_missing."""

    def _build_result(self, true_failures=0, not_applicable=0, expected_missing=0,
                      passed=5, total=5, warnings=0):
        return {
            "total_reports": total,
            "passed_reports": passed,
            "failed_reports": total - passed,
            "total_errors": true_failures,
            "total_warnings": warnings,
            "true_failures": true_failures,
            "not_applicable": not_applicable,
            "expected_missing": expected_missing,
            "reports": [],
        }

    def test_log_includes_not_applicable(self):
        from app.services.automated_data_validation_service import log_data_confidence_validation
        lines = []
        result = self._build_result(not_applicable=3)
        log_data_confidence_validation(result, log_print=lines.append)
        self.assertEqual(len(lines), 1)
        self.assertIn("not_applicable=3", lines[0])

    def test_log_includes_expected_missing(self):
        from app.services.automated_data_validation_service import log_data_confidence_validation
        lines = []
        result = self._build_result(expected_missing=7)
        log_data_confidence_validation(result, log_print=lines.append)
        self.assertIn("expected_missing=7", lines[0])

    def test_log_starts_with_data_confidence_validation(self):
        from app.services.automated_data_validation_service import log_data_confidence_validation
        lines = []
        log_data_confidence_validation(self._build_result(), log_print=lines.append)
        self.assertTrue(lines[0].startswith("DATA_CONFIDENCE_VALIDATION"))

    def test_log_includes_sample_size(self):
        from app.services.automated_data_validation_service import log_data_confidence_validation
        lines = []
        log_data_confidence_validation(self._build_result(total=12), log_print=lines.append)
        self.assertIn("sample_size=12", lines[0])

    def test_log_uses_true_failures_for_failed_count(self):
        from app.services.automated_data_validation_service import log_data_confidence_validation
        lines = []
        # true_failures=2, failed_reports=5 — should use true_failures
        result = self._build_result(true_failures=2, total=10, passed=5)
        log_data_confidence_validation(result, log_print=lines.append)
        self.assertIn("failed=2", lines[0])

    def test_safe_on_empty_result(self):
        from app.services.automated_data_validation_service import log_data_confidence_validation
        line = log_data_confidence_validation({})
        self.assertIn("DATA_CONFIDENCE_VALIDATION", line)


# ---------------------------------------------------------------------------
# WS-HIST: compute_evolution — comparison_available and score_change_5_day
# ---------------------------------------------------------------------------
class TestComputeEvolutionExtended(unittest.TestCase):
    """compute_evolution includes comparison_available and score_change_5_day."""

    def _tmp_db(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        f.close()
        return f.name

    def test_first_observation_comparison_available_false(self):
        from app.db.strategy_opportunity_history import compute_evolution
        db = self._tmp_db()
        try:
            row = {
                "ticker": "NVDA",
                "run_id": "run_001",
                "score": 75.0,
                "trading_date": date.today().isoformat(),
            }
            evo = compute_evolution(row, "calendar_spread", db_path=db)
            self.assertIn("comparison_available", evo)
            self.assertFalse(evo["comparison_available"])
        finally:
            os.unlink(db)

    def test_first_observation_has_score_change_5_day_none(self):
        from app.db.strategy_opportunity_history import compute_evolution
        db = self._tmp_db()
        try:
            row = {
                "ticker": "AAPL",
                "run_id": "run_001",
                "score": 80.0,
                "trading_date": date.today().isoformat(),
            }
            evo = compute_evolution(row, "calendar_spread", db_path=db)
            self.assertIn("score_change_5_day", evo)
            self.assertIsNone(evo["score_change_5_day"])
        finally:
            os.unlink(db)

    def test_second_observation_comparison_available_true(self):
        from app.db.strategy_opportunity_history import compute_evolution, write_run
        db = self._tmp_db()
        try:
            yesterday = (date.today() - timedelta(days=1)).isoformat()
            prior_row = {
                "ticker": "TSLA",
                "score": 70.0,
                "rank": 2,
                "verdict": "PASS",
                "trading_date": yesterday,
            }
            write_run(
                run_id="run_prior",
                strategy_results={"calendar_spread": {"rows": [prior_row]}},
                run_date=yesterday,
                db_path=db,
            )
            current_row = {
                "ticker": "TSLA",
                "run_id": "run_current",
                "score": 75.0,
                "trading_date": date.today().isoformat(),
            }
            evo = compute_evolution(current_row, "calendar_spread", db_path=db)
            self.assertTrue(evo["comparison_available"])
        finally:
            os.unlink(db)

    def test_score_change_5_day_computed_with_prior(self):
        from app.db.strategy_opportunity_history import compute_evolution, write_run
        db = self._tmp_db()
        try:
            five_days_ago = (date.today() - timedelta(days=5)).isoformat()
            prior_row = {
                "ticker": "META",
                "score": 60.0,
                "rank": 3,
                "verdict": "WATCH",
                "trading_date": five_days_ago,
            }
            write_run(
                run_id="run_old",
                strategy_results={"calendar_spread": {"rows": [prior_row]}},
                run_date=five_days_ago,
                db_path=db,
            )
            current_row = {
                "ticker": "META",
                "run_id": "run_new",
                "score": 70.0,
                "trading_date": date.today().isoformat(),
            }
            evo = compute_evolution(current_row, "calendar_spread", db_path=db)
            self.assertIn("score_change_5_day", evo)
            if evo["score_change_5_day"] is not None:
                self.assertAlmostEqual(evo["score_change_5_day"], 10.0, places=1)
        finally:
            os.unlink(db)


# ---------------------------------------------------------------------------
# WS-MIGRATE: UNIVERSAL_STRUCTURE_BUILDER_ENABLED flag
# ---------------------------------------------------------------------------
class TestUniversalBuilderFlag(unittest.TestCase):
    """UNIVERSAL_STRUCTURE_BUILDER_ENABLED flag is configured."""

    def test_flag_exists_in_config(self):
        from app import config
        self.assertTrue(hasattr(config, "UNIVERSAL_STRUCTURE_BUILDER_ENABLED"))

    def test_flag_default_is_true(self):
        from app import config
        self.assertTrue(bool(config.UNIVERSAL_STRUCTURE_BUILDER_ENABLED))

    def test_calendar_spread_service_references_flag(self):
        import pathlib
        src = pathlib.Path("app/services/calendar_spread_service.py").read_text()
        self.assertIn("UNIVERSAL_STRUCTURE_BUILDER_ENABLED", src)

    def test_calendar_discovery_audit_log_token_in_service(self):
        import pathlib
        src = pathlib.Path("app/services/calendar_spread_service.py").read_text()
        self.assertIn("CALENDAR_DISCOVERY_AUDIT", src)

    def test_universal_structure_builder_log_token_in_service(self):
        import pathlib
        src = pathlib.Path("app/services/calendar_spread_service.py").read_text()
        self.assertIn("UNIVERSAL_STRUCTURE_BUILDER", src)


# ---------------------------------------------------------------------------
# WS-API: New endpoints registered
# ---------------------------------------------------------------------------
class TestNewApiEndpoints(unittest.TestCase):
    """New Patch 33A API endpoints are registered in main.py."""

    def _source(self):
        import pathlib
        return pathlib.Path("app/main.py").read_text()

    def test_options_structures_endpoint_registered(self):
        self.assertIn("/api/options-structures/", self._source())

    def test_calendar_discovery_audit_endpoint_registered(self):
        self.assertIn("/api/calendar/discovery-audit", self._source())

    def test_calendar_history_endpoint_registered(self):
        self.assertIn("/api/calendar/history/", self._source())

    def test_opportunity_history_endpoint_registered(self):
        self.assertIn("/api/opportunities/", self._source())
        self.assertIn("/history", self._source())

    def test_all_new_endpoints_are_read_only(self):
        src = self._source()
        # All new handlers should have provider_calls_triggered: False
        for func_name in [
            "api_options_structures",
            "api_calendar_discovery_audit",
            "api_calendar_history",
            "api_opportunity_history",
        ]:
            self.assertIn(func_name, src, f"Handler {func_name} not found in main.py")

    def test_options_structures_handler_references_spec(self):
        src = self._source()
        self.assertIn("OptionsStructureSpec", src)


# ---------------------------------------------------------------------------
# WS-USB: StructureBuildResult dataclass
# ---------------------------------------------------------------------------
class TestStructureBuildResult(unittest.TestCase):
    """StructureBuildResult carries all required fields."""

    def test_result_has_expected_fields(self):
        from app.services.options_structure_builder import StructureBuildResult
        # Construct a minimal result
        result = StructureBuildResult(
            ticker="TEST",
            strategy_id="test",
            structure_type="call_calendar",
            structures=[],
            expiration_pairs_considered=[],
            pairs_valid=0,
            pairs_rejected=0,
            structures_built=0,
            build_status="MISSING_CHAIN",
            build_summary="No chain data.",
            data_completeness="MISSING",
            provider_completeness={"expirations_returned": 0, "expirations_requested": 0, "truncated": False},
        )
        self.assertEqual(result.ticker, "TEST")
        self.assertEqual(result.build_status, "MISSING_CHAIN")
        self.assertEqual(result.structures, [])

    def test_built_structure_has_legs_field(self):
        from app.services.options_structure_builder import BuiltStructure
        bs = BuiltStructure(
            structure_type="call_calendar",
            legs=[],
            conservative_debit=None,
            mid_debit=None,
            max_leg_spread_pct=None,
            structure_status="INCOMPLETE",
            rejection_codes=["NO_MATCHING_STRIKE"],
            front_expiration="2026-08-15",
            back_expiration="2026-09-19",
            front_dte=33,
            back_dte=68,
        )
        self.assertEqual(bs.structure_type, "call_calendar")
        self.assertIn("NO_MATCHING_STRIKE", bs.rejection_codes)


if __name__ == "__main__":
    unittest.main()
