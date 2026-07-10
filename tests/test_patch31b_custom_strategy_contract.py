"""
Tests for ASA Patch 31B — Custom Strategy Definition Contract + Run Finalization Closeout.

Covers:
  TKT-31B-A  — Custom strategy model schema (31B.v1)
  TKT-31B-B  — Validator: structural, catalog, semantic
  TKT-31B-C  — Repository: CRUD + owner isolation + optimistic locking
  TKT-31B-D  — Compiler: cost class, data requirements preview
  TKT-31B-E  — API Blueprint routes registered in Flask app
  TKT-31B-F  — Calendar scan barrier join before finalization (analysis_service)
  TKT-31B-G  — Canonical rejected-row invariant in normalization service
  TKT-31B-H  — Lifecycle structure key cardinality (option_type + strike)
  TKT-31B-I  — FF pair result codes (CALCULATED, NON_POSITIVE_FORWARD_VARIANCE, etc.)
  TKT-31B-J  — Tradier account ID masking helper
"""
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

# Stub robin_stocks before any import that might pull it in.
if "robin_stocks.robinhood" not in sys.modules:
    _rs_stub = types.ModuleType("robin_stocks")
    _rh_stub = types.ModuleType("robin_stocks.robinhood")
    for _attr in ("login", "logout"):
        setattr(_rh_stub, _attr, lambda *a, **k: None)
    for _ns in ("account", "crypto", "options"):
        setattr(_rh_stub, _ns, types.SimpleNamespace())
    sys.modules["robin_stocks"] = _rs_stub
    sys.modules["robin_stocks.robinhood"] = _rh_stub


# ── TKT-31B-A: Custom strategy model ─────────────────────────────────────────

class CustomStrategyModelTests(unittest.TestCase):

    def test_new_factory_sets_schema_version(self):
        from app.models.custom_strategy_models import CustomStrategyDefinition, CUSTOM_STRATEGY_SCHEMA_VERSION
        defn = CustomStrategyDefinition.new(owner_id="u1", name="Test Strategy")
        self.assertEqual(defn.schema_version, CUSTOM_STRATEGY_SCHEMA_VERSION)
        self.assertEqual(defn.schema_version, "31B.v1")

    def test_new_factory_status_is_draft(self):
        from app.models.custom_strategy_models import CustomStrategyDefinition
        defn = CustomStrategyDefinition.new(owner_id="u1", name="Test")
        self.assertEqual(defn.status, "draft")

    def test_new_factory_version_starts_at_1(self):
        from app.models.custom_strategy_models import CustomStrategyDefinition
        defn = CustomStrategyDefinition.new(owner_id="u1", name="Test")
        self.assertEqual(defn.definition_version, 1)

    def test_new_factory_generates_unique_definition_ids(self):
        from app.models.custom_strategy_models import CustomStrategyDefinition
        a = CustomStrategyDefinition.new(owner_id="u1", name="A")
        b = CustomStrategyDefinition.new(owner_id="u1", name="B")
        self.assertNotEqual(a.definition_id, b.definition_id)

    def test_to_dict_round_trips(self):
        from app.models.custom_strategy_models import CustomStrategyDefinition
        defn = CustomStrategyDefinition.new(
            owner_id="u1", name="Round Trip",
            conditions=[{"logic": "AND", "conditions": []}],
        )
        d = defn.to_dict()
        self.assertEqual(d["owner_id"], "u1")
        self.assertEqual(d["name"], "Round Trip")
        self.assertIn("definition_id", d)
        self.assertIn("created_at", d)

    def test_new_default_output_signal_is_watch(self):
        from app.models.custom_strategy_models import CustomStrategyDefinition
        defn = CustomStrategyDefinition.new(owner_id="u1", name="Test")
        self.assertEqual(defn.output.get("signal"), "WATCH")


# ── TKT-31B-B: Validator ──────────────────────────────────────────────────────

class CustomStrategyValidatorStructuralTests(unittest.TestCase):

    def _validate(self, definition):
        from app.services.custom_strategy_validator import validate_custom_strategy
        return validate_custom_strategy(definition)

    def test_valid_minimal_definition_passes(self):
        result = self._validate({
            "name": "My Strategy",
            "conditions": [{"logic": "AND", "conditions": []}],
            "output": {"signal": "WATCH"},
        })
        self.assertTrue(result.valid)
        self.assertEqual(result.errors, [])

    def test_missing_name_fails(self):
        result = self._validate({
            "conditions": [],
            "output": {"signal": "WATCH"},
        })
        self.assertFalse(result.valid)
        codes = [e.code for e in result.errors]
        self.assertIn("MISSING_REQUIRED_FIELD", codes)

    def test_missing_conditions_fails(self):
        result = self._validate({
            "name": "Test",
            "output": {"signal": "WATCH"},
        })
        self.assertFalse(result.valid)
        codes = [e.code for e in result.errors]
        self.assertIn("MISSING_REQUIRED_FIELD", codes)

    def test_invalid_logic_operator_fails(self):
        result = self._validate({
            "name": "Test",
            "conditions": [{"logic": "XOR", "conditions": []}],
            "output": {"signal": "WATCH"},
        })
        self.assertFalse(result.valid)
        codes = [e.code for e in result.errors]
        self.assertIn("INVALID_LOGIC_OPERATOR", codes)

    def test_invalid_output_signal_fails(self):
        result = self._validate({
            "name": "Test",
            "conditions": [],
            "output": {"signal": "BUY_NOW"},
        })
        self.assertFalse(result.valid)
        codes = [e.code for e in result.errors]
        self.assertIn("INVALID_OUTPUT_SIGNAL", codes)

    def test_name_too_long_fails(self):
        result = self._validate({
            "name": "X" * 200,
            "conditions": [],
            "output": {"signal": "WATCH"},
        })
        self.assertFalse(result.valid)
        codes = [e.code for e in result.errors]
        self.assertIn("NAME_TOO_LONG", codes)

    def test_invalid_status_fails(self):
        result = self._validate({
            "name": "Test",
            "status": "pending_review",
            "conditions": [],
            "output": {"signal": "WATCH"},
        })
        self.assertFalse(result.valid)

    def test_valid_statuses_pass(self):
        for status in ("draft", "active", "archived"):
            result = self._validate({
                "name": "Test",
                "status": status,
                "conditions": [],
                "output": {"signal": "WATCH"},
            })
            self.assertTrue(result.valid, f"status={status} should be valid")


class CustomStrategyValidatorSemanticTests(unittest.TestCase):

    def _validate(self, definition):
        from app.services.custom_strategy_validator import validate_custom_strategy
        return validate_custom_strategy(definition)

    def test_between_min_exceeds_max_fails(self):
        result = self._validate({
            "name": "Test",
            "conditions": [{
                "logic": "AND",
                "conditions": [{
                    "field_id": "strategy.score",
                    "operator": "between",
                    "value": {"min": 90, "max": 50},
                }],
            }],
            "output": {"signal": "WATCH"},
        })
        self.assertFalse(result.valid)
        codes = [e.code for e in result.errors]
        self.assertIn("BETWEEN_MIN_EXCEEDS_MAX", codes)

    def test_between_min_equal_to_max_passes(self):
        result = self._validate({
            "name": "Test",
            "conditions": [{
                "logic": "AND",
                "conditions": [{
                    "field_id": "strategy.score",
                    "operator": "between",
                    "value": {"min": 50, "max": 50},
                }],
            }],
            "output": {"signal": "WATCH"},
        })
        self.assertTrue(result.valid)

    def test_negative_dte_fails(self):
        result = self._validate({
            "name": "Test",
            "conditions": [{
                "logic": "AND",
                "conditions": [{
                    "field_id": "options.front_dte",
                    "operator": "greater_than_or_equal",
                    "value": -1,
                }],
            }],
            "output": {"signal": "WATCH"},
        })
        self.assertFalse(result.valid)
        codes = [e.code for e in result.errors]
        self.assertIn("NEGATIVE_DTE", codes)

    def test_dry_run_override_to_false_forbidden(self):
        result = self._validate({
            "name": "Test",
            "conditions": [{
                "logic": "AND",
                "conditions": [{
                    "field_id": "dry_run",
                    "operator": "eq",
                    "value": False,
                }],
            }],
            "output": {"signal": "WATCH"},
        })
        self.assertFalse(result.valid)
        codes = [e.code for e in result.errors]
        self.assertIn("DRY_RUN_OVERRIDE_FORBIDDEN", codes)

    def test_to_dict_shape(self):
        from app.services.custom_strategy_validator import ValidationResult, ValidationError
        result = ValidationResult(
            valid=False,
            errors=[ValidationError("TEST_CODE", "Test message", "conditions[0]")],
        )
        d = result.to_dict()
        self.assertFalse(d["valid"])
        self.assertEqual(len(d["errors"]), 1)
        self.assertEqual(d["errors"][0]["code"], "TEST_CODE")


# ── TKT-31B-C: Repository ─────────────────────────────────────────────────────

class CustomStrategyRepositoryTests(unittest.TestCase):

    def _make_definition(self, owner_id="owner-1", name="Test Strategy"):
        from app.models.custom_strategy_models import CustomStrategyDefinition
        return CustomStrategyDefinition.new(owner_id=owner_id, name=name).to_dict()

    def test_create_and_get_round_trip(self):
        from app.services.custom_strategy_repository import CustomStrategyRepository
        with patch.object(CustomStrategyRepository, "_connect") as mock_connect:
            mock_conn = MagicMock()
            mock_connect.return_value.__enter__ = lambda s: mock_conn
            mock_connect.return_value.__exit__ = lambda s, *a: False
            defn = self._make_definition()
            repo = CustomStrategyRepository()
            repo.create(defn)
            mock_conn.execute.assert_called()

    def test_get_raises_not_found_for_wrong_owner(self):
        from app.services.custom_strategy_repository import (
            CustomStrategyRepository,
            CustomStrategyNotFoundError,
        )
        with patch.object(CustomStrategyRepository, "_connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.execute.return_value.fetchone.return_value = None
            mock_connect.return_value.__enter__ = lambda s: mock_conn
            mock_connect.return_value.__exit__ = lambda s, *a: False
            repo = CustomStrategyRepository()
            with self.assertRaises(CustomStrategyNotFoundError):
                repo.get("nonexistent-id", "wrong-owner")

    def test_update_raises_conflict_on_stale_version(self):
        import json
        from app.services.custom_strategy_repository import (
            CustomStrategyRepository,
            CustomStrategyConflictError,
        )
        defn = self._make_definition()
        defn["definition_version"] = 3
        mock_row = MagicMock()
        mock_row.__getitem__ = lambda s, key: {
            "definition_json": json.dumps(defn),
            "definition_version": 3,
            "status": "draft",
        }[key]

        with patch.object(CustomStrategyRepository, "_connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.execute.return_value.fetchone.return_value = mock_row
            mock_connect.return_value.__enter__ = lambda s: mock_conn
            mock_connect.return_value.__exit__ = lambda s, *a: False
            repo = CustomStrategyRepository()
            with self.assertRaises(CustomStrategyConflictError):
                repo.update("some-id", "owner-1", {"name": "Updated"}, expected_version=1)

    def test_owner_isolation_get(self):
        """Owner A cannot retrieve Owner B's definition."""
        from app.services.custom_strategy_repository import (
            CustomStrategyRepository,
            CustomStrategyNotFoundError,
        )
        with patch.object(CustomStrategyRepository, "_connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.execute.return_value.fetchone.return_value = None
            mock_connect.return_value.__enter__ = lambda s: mock_conn
            mock_connect.return_value.__exit__ = lambda s, *a: False
            repo = CustomStrategyRepository()
            with self.assertRaises(CustomStrategyNotFoundError):
                repo.get("def-b", "owner-a")


# ── TKT-31B-D: Compiler ──────────────────────────────────────────────────────

class CustomStrategyCompilerTests(unittest.TestCase):

    def test_compile_preview_is_never_executable(self):
        from app.services.custom_strategy_compiler import compile_preview
        preview = compile_preview({"conditions": [], "output": {"signal": "WATCH"}})
        self.assertFalse(preview["executable"])
        self.assertFalse(preview["provider_calls_triggered"])
        self.assertFalse(preview["broker_calls_triggered"])

    def test_compile_preview_returns_field_ids(self):
        from app.services.custom_strategy_compiler import compile_preview
        definition = {
            "conditions": [{
                "logic": "AND",
                "conditions": [{"field_id": "score", "operator": "gte", "value": 70}],
            }],
            "output": {"signal": "WATCH"},
        }
        preview = compile_preview(definition)
        self.assertIn("score", preview["field_ids_referenced"])

    def test_compile_preview_schema_version(self):
        from app.services.custom_strategy_compiler import compile_preview
        preview = compile_preview({"conditions": [], "output": {}})
        self.assertEqual(preview["schema_version"], "31B.v1")

    def test_empty_definition_cost_class_is_cheap(self):
        from app.services.custom_strategy_compiler import compile_preview
        preview = compile_preview({"conditions": [], "output": {}})
        self.assertEqual(preview["cost_class"], "cheap")

    def test_cost_class_rank_increases_with_complexity(self):
        from app.services.custom_strategy_compiler import _COST_CLASS_RANK
        self.assertLess(_COST_CLASS_RANK["cheap"], _COST_CLASS_RANK["moderate"])
        self.assertLess(_COST_CLASS_RANK["moderate"], _COST_CLASS_RANK["expensive"])
        self.assertLess(_COST_CLASS_RANK["expensive"], _COST_CLASS_RANK["unsupported"])


# ── TKT-31B-E: API Blueprint ─────────────────────────────────────────────────

class CustomStrategyAPIBlueprintTests(unittest.TestCase):

    def test_blueprint_registered_in_app(self):
        from app.main import app
        rule_names = {rule.endpoint for rule in app.url_map.iter_rules()}
        self.assertTrue(
            any("custom_strategy" in name for name in rule_names),
            f"custom_strategy blueprint not found in routes: {rule_names}",
        )

    def test_validate_route_exists(self):
        from app.main import app
        rules = [str(rule) for rule in app.url_map.iter_rules()]
        self.assertTrue(
            any("/api/custom-strategies/validate" in r for r in rules),
            f"validate route not found: {rules}",
        )

    def test_compile_preview_route_exists(self):
        from app.main import app
        rules = [str(rule) for rule in app.url_map.iter_rules()]
        self.assertTrue(
            any("/api/custom-strategies/compile-preview" in r for r in rules),
            f"compile-preview route not found: {rules}",
        )


# ── TKT-31B-F: Calendar scan barrier ─────────────────────────────────────────

class CalendarScanBarrierTests(unittest.TestCase):

    def test_run_calendar_scan_bg_updates_state(self):
        from app.services.analysis_service import _CALENDAR_SCAN_STATE, _run_calendar_scan_bg
        original_status = _CALENDAR_SCAN_STATE.get("status")
        _run_calendar_scan_bg(lambda: [{"ticker": "TEST"}])
        self.assertEqual(_CALENDAR_SCAN_STATE["status"], "complete")
        self.assertEqual(len(_CALENDAR_SCAN_STATE["candidates"]), 1)
        self.assertEqual(_CALENDAR_SCAN_STATE["candidates"][0]["ticker"], "TEST")

    def test_run_calendar_scan_bg_handles_exception(self):
        from app.services.analysis_service import _run_calendar_scan_bg
        def failing_scan():
            raise RuntimeError("scan error")
        _run_calendar_scan_bg(failing_scan)


# ── TKT-31B-G: Canonical rejected-row invariant ───────────────────────────────

class CanonicalRejectedRowInvariantTests(unittest.TestCase):

    def test_fail_verdict_forces_ineligible(self):
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        row = {
            "ticker": "AAPL",
            "verdict": "FAIL / NOT AN EARNINGS SETUP",
            "calendar_entry_allowed": True,
            "daily_opportunity_eligible": True,
        }
        result = normalize_strategy_row(row, "earnings_calendar")
        self.assertFalse(result["daily_opportunity_eligible"])
        self.assertFalse(result.get("can_enter_daily_opportunity", True))
        # eligibility_status may be "excluded" or "ineligible" depending on _decision_semantics.
        self.assertIn(result.get("eligibility_status"), {"ineligible", "excluded", "blocked"})

    def test_rejected_candidate_row_type_forces_ineligible(self):
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        row = {
            "ticker": "MSFT",
            "verdict": "WATCH / REVIEW",
            "row_type": "rejected_candidate",
            "calendar_entry_allowed": True,
            "daily_opportunity_eligible": True,
        }
        result = normalize_strategy_row(row, "earnings_calendar")
        self.assertFalse(result["daily_opportunity_eligible"])
        self.assertEqual(result.get("decision_class"), "rejected")
        self.assertFalse(result.get("journal_eligible", True))

    def test_action_type_cleared_for_rejected_entry_action(self):
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        row = {
            "ticker": "TSLA",
            "verdict": "FAIL / SCORE TOO LOW",
            "action_type": "calendar_entry",
        }
        result = normalize_strategy_row(row, "earnings_calendar")
        self.assertEqual(result.get("action_type"), "none")

    def test_pass_row_not_affected_by_invariant(self):
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        row = {
            "ticker": "NVDA",
            "verdict": "WATCH / REVIEW",
            "calendar_entry_allowed": True,
            "daily_opportunity_eligible": True,
        }
        result = normalize_strategy_row(row, "earnings_calendar")
        self.assertTrue(result["daily_opportunity_eligible"])

    def test_decision_class_rejected_triggers_invariant(self):
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        row = {
            "ticker": "AMD",
            "verdict": "WATCH / SIGNAL",
            "decision_class": "rejected",
            "calendar_entry_allowed": True,
            "daily_opportunity_eligible": True,
        }
        result = normalize_strategy_row(row, "earnings_calendar")
        self.assertFalse(result["daily_opportunity_eligible"])


# ── TKT-31B-H: Lifecycle structure key cardinality ───────────────────────────

class LifecycleCardinalityTests(unittest.TestCase):

    def test_lifecycle_reconciliation_call_and_put_distinct(self):
        from app.api.open_positions_api import _lifecycle_row_reconciliation, _structure_dedup_summary

        structures = [
            {
                "underlying": "SBUX", "structure_type": "calendar",
                "option_type": "call", "strike": 110.0,
                "front_expiration": "2026-08-21", "back_expiration": "2026-09-18",
            },
            {
                "underlying": "SBUX", "structure_type": "calendar",
                "option_type": "put", "strike": 110.0,
                "front_expiration": "2026-08-21", "back_expiration": "2026-09-18",
            },
        ]
        dedup = _structure_dedup_summary(structures)
        reconciliation = _lifecycle_row_reconciliation([], structures, dedup)
        # call and put are distinct structures; unique key count must be 2
        self.assertEqual(reconciliation["unique_structure_keys"], 2)
        self.assertEqual(dedup["duplicate_group_count"], 0)

    def test_lifecycle_reconciliation_key_fields_present(self):
        from app.api.open_positions_api import _lifecycle_row_reconciliation, _structure_dedup_summary
        reconciliation = _lifecycle_row_reconciliation([], [], _structure_dedup_summary([]))
        self.assertIn("option_type", reconciliation["key_fields"])
        self.assertIn("strike", reconciliation["key_fields"])
        self.assertIn("front_expiration", reconciliation["key_fields"])
        self.assertIn("back_expiration", reconciliation["key_fields"])

    def test_structure_dedup_same_ticker_option_type_strike_is_duplicate(self):
        from app.api.open_positions_api import _structure_dedup_summary
        structures = [
            {
                "underlying": "AAPL", "structure_type": "calendar",
                "option_type": "call", "strike": 200.0,
                "front_expiration": "2026-08-21", "back_expiration": "2026-09-18",
            },
            {
                "underlying": "AAPL", "structure_type": "calendar",
                "option_type": "call", "strike": 200.0,
                "front_expiration": "2026-08-21", "back_expiration": "2026-09-18",
            },
        ]
        dedup = _structure_dedup_summary(structures)
        self.assertEqual(dedup["duplicate_group_count"], 1)
        self.assertTrue(dedup["duplicate_warning"])


# ── TKT-31B-I: FF pair result codes ──────────────────────────────────────────

class FFPairResultCodeTests(unittest.TestCase):

    def test_valid_pair_returns_calculated_code(self):
        from app.services.forward_factor_service import _try_formula
        result, code = _try_formula(0.30, 0.35, 30, 60)
        self.assertIsNotNone(result)
        self.assertEqual(code, "CALCULATED")
        self.assertEqual(result.get("ff_pair_result_code"), "CALCULATED")

    def test_inverted_variance_returns_non_positive_code(self):
        from app.services.forward_factor_service import _try_formula
        result, code = _try_formula(0.60, 0.30, 30, 60)
        self.assertIsNone(result)
        self.assertEqual(code, "NON_POSITIVE_FORWARD_VARIANCE")

    def test_missing_front_iv_returns_missing_code(self):
        from app.services.forward_factor_service import _try_formula
        result, code = _try_formula(None, 0.35, 30, 60)
        self.assertIsNone(result)
        self.assertEqual(code, "MISSING_FRONT_IV")

    def test_missing_back_iv_returns_missing_code(self):
        from app.services.forward_factor_service import _try_formula
        result, code = _try_formula(0.30, None, 30, 60)
        self.assertIsNone(result)
        self.assertEqual(code, "MISSING_BACK_IV")

    def test_invalid_time_order_returns_code(self):
        from app.services.forward_factor_service import _try_formula
        result, code = _try_formula(0.30, 0.35, 60, 30)
        self.assertIsNone(result)
        self.assertEqual(code, "INVALID_TIME_ORDER")

    def test_calculated_result_has_variance_fields(self):
        from app.services.forward_factor_service import _try_formula
        result, code = _try_formula(0.30, 0.35, 30, 60)
        self.assertIn("front_total_variance", result)
        self.assertIn("back_total_variance", result)
        self.assertIn("forward_variance_numerator", result)
        self.assertGreater(result["front_total_variance"], 0)
        self.assertGreater(result["back_total_variance"], 0)
        self.assertGreater(result["forward_variance_numerator"], 0)

    def test_pair_audit_includes_result_code(self):
        from app.services.forward_factor_service import _pair_audit
        row = {
            "ticker": "AAPL",
            "front_expiration": "2026-08-21",
            "back_expiration": "2026-09-18",
            "forward_factor": 0.25,
            "diagnostic_raw_iv_forward_factor": None,
            "verdict": "PASS / FORWARD FACTOR POSITIVE",
            "ff_pair_result_code": "CALCULATED",
        }
        audit = _pair_audit(row, "selected")
        self.assertEqual(audit["ff_pair_result_code"], "CALCULATED")


# ── TKT-31B-J: Tradier account ID masking ────────────────────────────────────

class TradierAccountMaskingTests(unittest.TestCase):

    def test_mask_account_id_for_log_long_id(self):
        from app.providers.tradier_provider import _mask_account_id_for_log
        self.assertEqual(_mask_account_id_for_log("12345678"), "***5678")

    def test_mask_account_id_for_log_short_id(self):
        from app.providers.tradier_provider import _mask_account_id_for_log
        self.assertEqual(_mask_account_id_for_log("123"), "***")

    def test_mask_account_id_for_log_none(self):
        from app.providers.tradier_provider import _mask_account_id_for_log
        self.assertEqual(_mask_account_id_for_log(None), "***")

    def test_mask_account_id_for_log_empty_string(self):
        from app.providers.tradier_provider import _mask_account_id_for_log
        self.assertEqual(_mask_account_id_for_log(""), "***")

    def test_mask_account_id_for_log_preserves_last_4(self):
        from app.providers.tradier_provider import _mask_account_id_for_log
        masked = _mask_account_id_for_log("ABC9999")
        self.assertTrue(masked.endswith("9999"))
        self.assertTrue(masked.startswith("***"))


if __name__ == "__main__":
    unittest.main()
