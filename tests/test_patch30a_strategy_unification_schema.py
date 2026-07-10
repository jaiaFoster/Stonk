"""
ASA Patch 30A — Universal Strategy Row Schema + Strategy Spec Registry
Tests covering:
  Lane 1: Universal strategy row schema (schema version, canonical fields)
  Lane 2: Shared gate model (make_gate, normalize_gate_status, helpers)
  Lane 3: Strategy spec registry (all 4 specs, FF dry-run, DO eligibility)
  Lane 4: Strategy row normalization service (all 4 strategies + edge cases)
  Lane 5: Per-strategy mapping (metrics, gates, daily_opportunity_eligible)
  Lane 8: Daily Opportunity regression (FF excluded, eligibility mirrors existing logic)
  Lane 9: Observation journal readiness (journal_eligible, observation_key)
  Lane 7: Public screener compatibility regression
  Safety invariants (CAVEMAN MODE)
"""
from __future__ import annotations

import py_compile
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Compile guard
# ─────────────────────────────────────────────────────────────────────────────

class TestCompile:
    def test_strategy_row_schema_compiles(self):
        py_compile.compile("app/services/strategy_row_schema.py", doraise=True)

    def test_strategy_gate_service_compiles(self):
        py_compile.compile("app/services/strategy_gate_service.py", doraise=True)

    def test_strategy_spec_registry_compiles(self):
        py_compile.compile("app/services/strategy_spec_registry.py", doraise=True)

    def test_strategy_row_normalization_service_compiles(self):
        py_compile.compile("app/services/strategy_row_normalization_service.py", doraise=True)

    def test_developer_snapshot_service_compiles(self):
        py_compile.compile("app/services/developer_snapshot_service.py", doraise=True)

    def test_main_compiles(self):
        py_compile.compile("app/main.py", doraise=True)


# ─────────────────────────────────────────────────────────────────────────────
# Lane 1: Universal strategy row schema constants
# ─────────────────────────────────────────────────────────────────────────────

class TestStrategyRowSchema:
    def test_schema_version_tracks_current_and_minimum_supported(self):
        from app.services.strategy_row_schema import (
            MINIMUM_SUPPORTED_STRATEGY_ROW_SCHEMA_VERSION,
            STRATEGY_ROW_SCHEMA_VERSION,
        )
        assert STRATEGY_ROW_SCHEMA_VERSION == "30J.v1"
        assert MINIMUM_SUPPORTED_STRATEGY_ROW_SCHEMA_VERSION == "30A.v1"

    def test_normalized_row_exclude_contains_large_fields(self):
        from app.services.strategy_row_schema import NORMALIZED_ROW_EXCLUDE
        for field in ("raw_chain_data", "options_chain", "debug_trace", "payload", "scenario_grid"):
            assert field in NORMALIZED_ROW_EXCLUDE, f"{field!r} missing from NORMALIZED_ROW_EXCLUDE"

    def test_canonical_required_fields_includes_core_fields(self):
        from app.services.strategy_row_schema import CANONICAL_REQUIRED_FIELDS
        for field in ("strategy_id", "verdict", "friendly_verdict", "gates", "metrics",
                      "data_quality", "daily_opportunity_eligible", "can_trade_live",
                      "strategy_row_schema_version"):
            assert field in CANONICAL_REQUIRED_FIELDS, f"{field!r} not in CANONICAL_REQUIRED_FIELDS"

    def test_strategy_family_constants_exist(self):
        from app.services.strategy_row_schema import (
            STRATEGY_FAMILY_OPTIONS_EVENT, STRATEGY_FAMILY_OPTIONS_SKEW,
            STRATEGY_FAMILY_OPTIONS_FORWARD, STRATEGY_FAMILY_EQUITY_MOMENTUM,
        )
        for val in (STRATEGY_FAMILY_OPTIONS_EVENT, STRATEGY_FAMILY_OPTIONS_SKEW,
                    STRATEGY_FAMILY_OPTIONS_FORWARD, STRATEGY_FAMILY_EQUITY_MOMENTUM):
            assert isinstance(val, str) and val


# ─────────────────────────────────────────────────────────────────────────────
# Lane 2: Shared gate model
# ─────────────────────────────────────────────────────────────────────────────

class TestGateModel:
    def _make(self, label="Check", status="pass", **kw):
        from app.services.strategy_gate_service import make_gate
        return make_gate(label, status, **kw)

    def test_make_gate_returns_required_fields(self):
        gate = self._make("Liquidity", "pass")
        for field in ("id", "label", "name", "status", "detail", "reason", "blocking", "sort_order"):
            assert field in gate, f"{field!r} missing from gate"

    def test_make_gate_backward_compat_name(self):
        gate = self._make("Earnings trust", "pass")
        assert gate["name"] == "Earnings trust"

    def test_make_gate_backward_compat_detail(self):
        gate = self._make("Earnings trust", "pass", reason="All clear.")
        assert gate["detail"] == "All clear."

    def test_make_gate_id_derived_from_label(self):
        gate = self._make("Above 50-day MA", "pass")
        assert gate["id"] == "above_50_day_ma"

    def test_make_gate_explicit_id_preserved(self):
        gate = self._make("Liquidity", "pass", id="liquidity_check")
        assert gate["id"] == "liquidity_check"

    def test_make_gate_fail_is_blocking_by_default(self):
        gate = self._make("Check", "fail")
        assert gate["blocking"] is True

    def test_make_gate_pass_is_not_blocking(self):
        gate = self._make("Check", "pass")
        assert gate["blocking"] is False

    def test_make_gate_dry_run_is_not_blocking(self):
        gate = self._make("Execution", "dry_run")
        assert gate["blocking"] is False

    def test_make_gate_explicit_blocking_override(self):
        gate = self._make("Watch gate", "watch", blocking=True)
        assert gate["blocking"] is True

    def test_make_gate_value_field(self):
        gate = self._make("Liquidity", "pass", value="OI 420")
        assert gate["value"] == "OI 420"

    def test_make_gate_sort_order_default(self):
        gate = self._make("Unknown gate name xyz", "pass")
        assert isinstance(gate["sort_order"], int)

    def test_make_gate_sort_order_for_known_id(self):
        from app.services.strategy_gate_service import make_gate
        gate = make_gate("Execution", "dry_run", id="execution")
        assert gate["sort_order"] == 90

    def test_normalize_gate_status_pass(self):
        from app.services.strategy_gate_service import normalize_gate_status
        for s in ("pass", "PASS", "ok", "green", "yes"):
            assert normalize_gate_status(s) == "pass", f"Expected pass for {s!r}"

    def test_normalize_gate_status_fail(self):
        from app.services.strategy_gate_service import normalize_gate_status
        for s in ("fail", "FAIL", "failed", "no", "false"):
            assert normalize_gate_status(s) == "fail", f"Expected fail for {s!r}"

    def test_normalize_gate_status_watch(self):
        from app.services.strategy_gate_service import normalize_gate_status
        for s in ("watch", "WATCH", "warn", "warning"):
            assert normalize_gate_status(s) == "watch", f"Expected watch for {s!r}"

    def test_normalize_gate_status_not_applicable(self):
        from app.services.strategy_gate_service import normalize_gate_status
        assert normalize_gate_status("not_applicable") == "not_applicable"
        assert normalize_gate_status("N/A") == "not_applicable"

    def test_normalize_gate_status_dry_run(self):
        from app.services.strategy_gate_service import normalize_gate_status
        assert normalize_gate_status("dry_run") == "dry_run"
        assert normalize_gate_status("DRY_RUN") == "dry_run"

    def test_normalize_gate_status_unknown_fallback(self):
        from app.services.strategy_gate_service import normalize_gate_status
        assert normalize_gate_status("MYSTERY_VALUE") == "unknown"

    def test_gate_status_rank_fail_worse_than_pass(self):
        from app.services.strategy_gate_service import gate_status_rank
        assert gate_status_rank("fail") < gate_status_rank("pass")

    def test_gate_status_rank_error_worst(self):
        from app.services.strategy_gate_service import gate_status_rank
        assert gate_status_rank("error") <= gate_status_rank("fail")

    def test_has_blocking_gate_failure_true(self):
        from app.services.strategy_gate_service import has_blocking_gate_failure, make_gate
        gates = [make_gate("Check", "pass"), make_gate("Hard fail", "fail")]
        assert has_blocking_gate_failure(gates) is True

    def test_has_blocking_gate_failure_false_all_pass(self):
        from app.services.strategy_gate_service import has_blocking_gate_failure, make_gate
        gates = [make_gate("A", "pass"), make_gate("B", "watch", blocking=False)]
        assert has_blocking_gate_failure(gates) is False

    def test_has_blocking_gate_failure_false_fail_not_blocking(self):
        from app.services.strategy_gate_service import has_blocking_gate_failure, make_gate
        gates = [make_gate("Soft fail", "fail", blocking=False)]
        assert has_blocking_gate_failure(gates) is False

    def test_summarize_gates_empty(self):
        from app.services.strategy_gate_service import summarize_gates
        summary = summarize_gates([])
        assert summary["total"] == 0

    def test_summarize_gates_worst_status(self):
        from app.services.strategy_gate_service import summarize_gates, make_gate
        gates = [make_gate("A", "pass"), make_gate("B", "fail"), make_gate("C", "watch")]
        summary = summarize_gates(gates)
        assert summary["worst_status"] == "fail"
        assert summary["fail_count"] == 1
        assert summary["pass_count"] == 1

    def test_summarize_gates_has_blocking_failure(self):
        from app.services.strategy_gate_service import summarize_gates, make_gate
        gates = [make_gate("Hard", "fail"), make_gate("Ok", "pass")]
        summary = summarize_gates(gates)
        assert summary["has_blocking_failure"] is True

    def test_gate_statuses_set_contains_expected_values(self):
        from app.services.strategy_gate_service import GATE_STATUSES
        for s in ("pass", "watch", "fail", "unknown", "skipped", "not_applicable", "dry_run", "error"):
            assert s in GATE_STATUSES


# ─────────────────────────────────────────────────────────────────────────────
# Lane 3: Strategy spec registry
# ─────────────────────────────────────────────────────────────────────────────

class TestStrategySpecRegistry:
    _ALL_IDS = ("earnings_calendar", "skew_momentum_vertical", "forward_factor_calendar", "stock_momentum")

    def test_all_four_strategy_specs_exist(self):
        from app.services.strategy_spec_registry import get_spec
        for sid in self._ALL_IDS:
            spec = get_spec(sid)
            assert spec is not None, f"Missing spec for {sid!r}"

    def test_strategy_ids_are_stable(self):
        from app.services.strategy_spec_registry import all_strategy_ids
        ids = all_strategy_ids()
        for sid in self._ALL_IDS:
            assert sid in ids

    def test_ff_is_dry_run(self):
        from app.services.strategy_spec_registry import get_spec
        spec = get_spec("forward_factor_calendar")
        assert spec["dry_run"] is True

    def test_ff_status_is_dry_run_or_research(self):
        from app.services.strategy_spec_registry import get_spec
        spec = get_spec("forward_factor_calendar")
        assert spec["status"] in ("dry_run", "research"), f"FF status must be dry_run/research, got: {spec['status']!r}"

    def test_ff_daily_opportunity_not_allowed(self):
        from app.services.strategy_spec_registry import is_daily_opportunity_allowed
        assert is_daily_opportunity_allowed("forward_factor_calendar") is False

    def test_earnings_calendar_is_active(self):
        from app.services.strategy_spec_registry import get_spec
        assert get_spec("earnings_calendar")["status"] == "active"

    def test_stock_momentum_is_active(self):
        from app.services.strategy_spec_registry import get_spec
        assert get_spec("stock_momentum")["status"] == "active"

    def test_earnings_calendar_daily_opportunity_allowed(self):
        from app.services.strategy_spec_registry import is_daily_opportunity_allowed
        assert is_daily_opportunity_allowed("earnings_calendar") is True

    def test_each_spec_has_required_fields(self):
        from app.services.strategy_spec_registry import all_specs
        required = ("strategy_id", "strategy_name", "strategy_family", "strategy_goal",
                    "status", "dry_run", "daily_opportunity_allowed", "gate_ids", "schema_version")
        for spec in all_specs():
            for field in required:
                assert field in spec, f"Spec for {spec.get('strategy_id')!r} missing {field!r}"

    def test_each_spec_has_gate_ids(self):
        from app.services.strategy_spec_registry import all_specs
        for spec in all_specs():
            assert isinstance(spec["gate_ids"], list) and spec["gate_ids"], (
                f"Spec {spec.get('strategy_id')!r} must have non-empty gate_ids"
            )

    def test_schema_version_matches_row_schema(self):
        from app.services.strategy_spec_registry import all_specs
        from app.services.strategy_row_schema import STRATEGY_ROW_SCHEMA_VERSION
        for spec in all_specs():
            assert spec["schema_version"] == STRATEGY_ROW_SCHEMA_VERSION

    def test_get_spec_unknown_returns_none(self):
        from app.services.strategy_spec_registry import get_spec
        assert get_spec("nonexistent_strategy") is None

    def test_is_dry_run_ff_true(self):
        from app.services.strategy_spec_registry import is_dry_run
        assert is_dry_run("forward_factor_calendar") is True

    def test_is_dry_run_calendar_false(self):
        from app.services.strategy_spec_registry import is_dry_run
        assert is_dry_run("earnings_calendar") is False

    def test_all_specs_returns_list(self):
        from app.services.strategy_spec_registry import all_specs
        specs = all_specs()
        assert isinstance(specs, list) and len(specs) == 4


# ─────────────────────────────────────────────────────────────────────────────
# Lane 4: Row normalization — shared behavior
# ─────────────────────────────────────────────────────────────────────────────

class TestRowNormalizationShared:
    def _normalize(self, row, strategy_id):
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        return normalize_strategy_row(row, strategy_id)

    def test_schema_version_added(self):
        row: dict[str, Any] = {}
        self._normalize(row, "stock_momentum")
        assert row.get("strategy_row_schema_version") == "30J.v1"

    def test_schema_version_not_overwritten(self):
        row: dict[str, Any] = {"strategy_row_schema_version": "old_version"}
        self._normalize(row, "stock_momentum")
        assert row["strategy_row_schema_version"] == "old_version"

    def test_strategy_id_set(self):
        row: dict[str, Any] = {}
        self._normalize(row, "earnings_calendar")
        assert row["strategy_id"] == "earnings_calendar"

    def test_strategy_name_from_spec(self):
        row: dict[str, Any] = {}
        self._normalize(row, "earnings_calendar")
        assert row.get("strategy_name") == "Earnings Calendar Spread"

    def test_strategy_family_from_spec(self):
        row: dict[str, Any] = {}
        self._normalize(row, "stock_momentum")
        assert row.get("strategy_family") == "equity_momentum"

    def test_metrics_dict_added(self):
        row: dict[str, Any] = {}
        self._normalize(row, "stock_momentum")
        assert isinstance(row.get("metrics"), dict)

    def test_data_quality_added(self):
        row: dict[str, Any] = {}
        self._normalize(row, "stock_momentum")
        assert "data_quality" in row

    def test_daily_opportunity_eligible_added(self):
        row: dict[str, Any] = {}
        self._normalize(row, "earnings_calendar")
        assert "daily_opportunity_eligible" in row

    def test_daily_opportunity_reason_added(self):
        row: dict[str, Any] = {}
        self._normalize(row, "earnings_calendar")
        assert "daily_opportunity_reason" in row

    def test_can_trade_live_defaults_false(self):
        row: dict[str, Any] = {}
        self._normalize(row, "earnings_calendar")
        assert row.get("can_trade_live") is False

    def test_dry_run_field_added(self):
        row: dict[str, Any] = {}
        self._normalize(row, "earnings_calendar")
        assert "dry_run" in row

    def test_journal_eligible_added(self):
        row: dict[str, Any] = {"ticker": "AAPL", "action": "EARNINGS CALENDAR CANDIDATE"}
        self._normalize(row, "earnings_calendar")
        assert "journal_eligible" in row

    def test_observation_key_added(self):
        row: dict[str, Any] = {"ticker": "AAPL", "action": "EARNINGS CALENDAR CANDIDATE"}
        self._normalize(row, "earnings_calendar")
        key = row.get("observation_key", "")
        assert key.startswith("earnings_calendar:AAPL")

    def test_observation_refs_added_as_list(self):
        row: dict[str, Any] = {}
        self._normalize(row, "stock_momentum")
        assert isinstance(row.get("observation_refs"), list)

    def test_friendly_verdict_added(self):
        row: dict[str, Any] = {}
        self._normalize(row, "stock_momentum")
        assert "friendly_verdict" in row

    def test_primary_reason_added(self):
        row: dict[str, Any] = {}
        self._normalize(row, "stock_momentum")
        assert "primary_reason" in row

    def test_gates_added_for_earnings_calendar(self):
        row: dict[str, Any] = {}
        self._normalize(row, "earnings_calendar")
        assert "gates" in row and isinstance(row["gates"], list)

    def test_gates_added_for_skew_with_requirements(self):
        row: dict[str, Any] = {"requirements": []}
        self._normalize(row, "skew_momentum_vertical")
        assert "gates" in row

    def test_gates_added_for_ff_with_ff_gates(self):
        row: dict[str, Any] = {"ff_gates": {
            "cheap_eligible": True, "chain_approved": True, "source_qualified": True,
            "diagnostic_model": True, "structure_built": True, "earnings_contaminated": False,
        }}
        self._normalize(row, "forward_factor_calendar")
        assert "gates" in row and len(row["gates"]) > 0

    def test_gates_have_required_fields(self):
        row: dict[str, Any] = {}
        self._normalize(row, "earnings_calendar")
        for gate in row.get("gates", []):
            assert "name" in gate, "Gate missing 'name' (backward compat)"
            assert "status" in gate
            assert "detail" in gate, "Gate missing 'detail' (backward compat)"
            assert "id" in gate, "Gate missing 'id' (30A field)"
            assert "label" in gate, "Gate missing 'label' (30A field)"
            assert "blocking" in gate, "Gate missing 'blocking' (30A field)"

    def test_gates_status_values_canonical(self):
        from app.services.strategy_gate_service import GATE_STATUSES
        row: dict[str, Any] = {}
        self._normalize(row, "earnings_calendar")
        for gate in row.get("gates", []):
            assert gate["status"] in GATE_STATUSES, f"Non-canonical status: {gate['status']!r}"

    def test_normalize_returns_row(self):
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        row: dict[str, Any] = {}
        result = normalize_strategy_row(row, "stock_momentum")
        assert result is row

    def test_legacy_fields_preserved(self):
        row: dict[str, Any] = {"ticker": "AAPL", "score": 75.0, "my_custom_field": "preserved"}
        self._normalize(row, "stock_momentum")
        assert row.get("ticker") == "AAPL"
        assert row.get("score") == 75.0
        assert row.get("my_custom_field") == "preserved"

    def test_malformed_row_does_not_crash(self):
        """Normalization must handle malformed rows without raising."""
        for bad_row in (
            {},
            {"ticker": None},
            {"verdict": 12345},
            {"requirements": "not a list"},
            {"ff_gates": "not a dict"},
        ):
            self._normalize(bad_row, "earnings_calendar")

    def test_unknown_strategy_id_returns_safe_fallback(self):
        row: dict[str, Any] = {"ticker": "AAPL"}
        self._normalize(row, "unknown_strategy_xyz")
        assert row.get("strategy_id") == "unknown_strategy_xyz"
        assert "strategy_row_schema_version" in row

    def test_verdict_backfilled_from_action_for_earnings_calendar(self):
        """earnings_calendar rows use action; verdict must be set so contract is satisfied."""
        row: dict[str, Any] = {"action": "EARNINGS CALENDAR CANDIDATE"}
        self._normalize(row, "earnings_calendar")
        assert row.get("verdict") == "EARNINGS CALENDAR CANDIDATE"

    def test_verdict_backfilled_from_action_for_stock_momentum(self):
        """stock_momentum rows use action; verdict must be set so contract is satisfied."""
        row: dict[str, Any] = {"action": "CONSIDER ADDING"}
        self._normalize(row, "stock_momentum")
        assert row.get("verdict") == "CONSIDER ADDING"

    def test_verdict_not_overwritten_when_already_present(self):
        """If a row already has verdict, it must not be replaced by action."""
        row: dict[str, Any] = {"verdict": "PASS / OK", "action": "something else"}
        self._normalize(row, "skew_momentum_vertical")
        assert row.get("verdict") == "PASS / OK"

    def test_gates_always_set_for_unknown_strategy(self):
        """Unknown strategies must produce an empty gates list, not a missing field."""
        row: dict[str, Any] = {"ticker": "AAPL"}
        self._normalize(row, "totally_unknown_strategy")
        assert "gates" in row
        assert isinstance(row["gates"], list)


# ─────────────────────────────────────────────────────────────────────────────
# Lane 5A: Earnings calendar per-strategy mapping
# ─────────────────────────────────────────────────────────────────────────────

class TestEarningsCalendarMapping:
    def _normalize(self, row):
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        return normalize_strategy_row(row, "earnings_calendar")

    def test_calendar_entry_allowed_maps_to_daily_opp_eligible(self):
        row: dict[str, Any] = {"calendar_entry_allowed": True, "ticker": "AAPL", "action": "EARNINGS CALENDAR CANDIDATE"}
        self._normalize(row)
        assert row["daily_opportunity_eligible"] is True

    def test_calendar_entry_not_allowed_means_not_eligible(self):
        row: dict[str, Any] = {"calendar_entry_allowed": False, "action": "FAIL"}
        self._normalize(row)
        assert row["daily_opportunity_eligible"] is False

    def test_metrics_include_iv_relationship(self):
        row: dict[str, Any] = {"iv_relationship_status": "favorable", "earnings_trust_label": "confirmed"}
        self._normalize(row)
        assert row["metrics"].get("iv_relationship_status") == "favorable"
        assert row["metrics"].get("earnings_trust_label") == "confirmed"

    def test_conflict_trust_makes_gate_blocking(self):
        row: dict[str, Any] = {"earnings_trust_label": "conflict_do_not_trade"}
        self._normalize(row)
        trust_gates = [g for g in row.get("gates", []) if "trust" in g.get("id", "")]
        assert any(g["blocking"] for g in trust_gates)

    def test_single_source_trust_is_watch_not_blocking(self):
        row: dict[str, Any] = {"earnings_trust_label": "single_source_verify"}
        self._normalize(row)
        trust_gates = [g for g in row.get("gates", []) if "trust" in g.get("id", "")]
        assert all(not g["blocking"] for g in trust_gates)
        assert all(g["status"] == "watch" for g in trust_gates)

    def test_confirmed_trust_label_maps_to_pass_gate(self):
        row: dict[str, Any] = {"earnings_trust_label": "confirmed"}
        self._normalize(row)
        trust_gates = [g for g in row.get("gates", []) if "trust" in g.get("id", "")]
        assert all(g["status"] == "pass" for g in trust_gates)

    def test_data_quality_good_when_confirmed(self):
        row: dict[str, Any] = {"earnings_trust_label": "confirmed"}
        self._normalize(row)
        assert row["data_quality"] == "good"

    def test_data_quality_conflict(self):
        row: dict[str, Any] = {"earnings_trust_label": "conflict_do_not_trade"}
        self._normalize(row)
        assert row["data_quality"] == "conflict"

    def test_friendly_verdict_eligible(self):
        row: dict[str, Any] = {"action": "EARNINGS CALENDAR CANDIDATE"}
        self._normalize(row)
        assert row["friendly_verdict"] == "Eligible"


# ─────────────────────────────────────────────────────────────────────────────
# Lane 5B: Skew momentum vertical per-strategy mapping
# ─────────────────────────────────────────────────────────────────────────────

class TestSkewMomentumMapping:
    def _normalize(self, row):
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        return normalize_strategy_row(row, "skew_momentum_vertical")

    def test_momentum_metrics_in_metrics(self):
        row: dict[str, Any] = {"momentum_status": "confirmed", "skew_status": "pass", "atm_iv": 0.42}
        self._normalize(row)
        assert row["metrics"]["momentum_status"] == "confirmed"
        assert row["metrics"]["skew_status"] == "pass"
        assert row["metrics"]["atm_iv"] == 0.42

    def test_friendly_verdict_pass(self):
        row: dict[str, Any] = {"verdict": "PASS / POSSIBLE ENTRY SETUP"}
        self._normalize(row)
        assert row["friendly_verdict"] == "Vertical candidate"

    def test_friendly_verdict_watch(self):
        row: dict[str, Any] = {"verdict": "WATCH / MOMENTUM NOT CONFIRMED"}
        self._normalize(row)
        assert row["friendly_verdict"] == "Near candidate"

    def test_friendly_verdict_fail(self):
        row: dict[str, Any] = {"verdict": "FAIL / DTE TOO SHORT"}
        self._normalize(row)
        assert row["friendly_verdict"] == "Did not qualify"

    def test_pass_verdict_eligible_for_daily_opp(self):
        row: dict[str, Any] = {"verdict": "PASS / POSSIBLE ENTRY SETUP"}
        self._normalize(row)
        assert row["daily_opportunity_eligible"] is True

    def test_watch_verdict_not_eligible_for_daily_opp(self):
        row: dict[str, Any] = {"verdict": "WATCH / MOMENTUM NOT CONFIRMED"}
        self._normalize(row)
        assert row["daily_opportunity_eligible"] is False

    def test_requirements_mapped_to_gates(self):
        req = {"name": "DTE check", "status": "PASS", "detail": "DTE OK", "code": "dte"}
        row: dict[str, Any] = {"requirements": [req], "verdict": "PASS / OK"}
        self._normalize(row)
        assert row.get("gates")
        gate = row["gates"][0]
        assert gate["status"] == "pass"
        assert gate["name"] == "DTE check"

    def test_requirement_fail_is_blocking(self):
        req = {"name": "Liquidity", "status": "FAIL", "detail": "Too wide", "code": "liquidity"}
        row: dict[str, Any] = {"requirements": [req], "verdict": "FAIL / ILLIQUID"}
        self._normalize(row)
        gate = row["gates"][0]
        assert gate["status"] == "fail"
        assert gate["blocking"] is True


# ─────────────────────────────────────────────────────────────────────────────
# Lane 5C: Forward Factor per-strategy mapping
# ─────────────────────────────────────────────────────────────────────────────

class TestForwardFactorMapping:
    def _normalize(self, row=None):
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        row = row or {}
        return normalize_strategy_row(row, "forward_factor_calendar")

    def test_dry_run_always_true(self):
        row = self._normalize()
        assert row["dry_run"] is True

    def test_can_trade_live_always_false(self):
        row = self._normalize()
        assert row["can_trade_live"] is False

    def test_can_enter_daily_opportunity_false(self):
        row = self._normalize()
        assert row.get("can_enter_daily_opportunity") is False

    def test_daily_opportunity_eligible_false(self):
        row = self._normalize()
        assert row["daily_opportunity_eligible"] is False

    def test_ff_metrics_extracted(self):
        row: dict[str, Any] = {
            "front_iv": 0.35, "back_iv": 0.28, "ex_earnings_iv": 0.30,
            "source_qualified": True, "chain_approved": True,
        }
        self._normalize(row)
        assert row["metrics"]["front_iv"] == 0.35
        assert row["metrics"]["back_iv"] == 0.28
        assert row["metrics"]["source_qualified"] is True

    def test_ff_gates_skipped_stage(self):
        row: dict[str, Any] = {"ff_candidate_stage": "cap_skip", "ff_gates": {}}
        self._normalize(row)
        gates = row.get("gates", [])
        assert any(g["status"] == "skipped" for g in gates)
        assert any(g["status"] == "dry_run" for g in gates)
        assert all(not g["blocking"] or g["status"] != "dry_run" for g in gates)

    def test_execution_gate_always_dry_run_not_blocking(self):
        row: dict[str, Any] = {"ff_gates": {
            "cheap_eligible": True, "chain_approved": True, "source_qualified": True,
            "diagnostic_model": True, "structure_built": True, "earnings_contaminated": False,
        }}
        self._normalize(row)
        exec_gates = [g for g in row.get("gates", []) if g.get("id") == "execution"]
        assert exec_gates
        assert exec_gates[0]["status"] == "dry_run"
        assert exec_gates[0]["blocking"] is False

    def test_friendly_verdict_skipped_cap(self):
        row: dict[str, Any] = {"ff_candidate_stage": "cap_skip"}
        self._normalize(row)
        assert row["friendly_verdict"] == "Skipped by limited scan"

    def test_strategy_name_is_forward_factor(self):
        row = self._normalize()
        assert row.get("strategy_name") == "Forward Factor Calendar"


# ─────────────────────────────────────────────────────────────────────────────
# Lane 5D: Stock momentum per-strategy mapping
# ─────────────────────────────────────────────────────────────────────────────

class TestStockMomentumMapping:
    def _make_row(self, action="CONSIDER ADDING", score=75.0, **overrides) -> dict[str, Any]:
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        row: dict[str, Any] = {
            "ticker": "AAPL",
            "score": score,
            "momentum_score": score,
            "action": action,
            "trend_status": "clean",
            "volume_status": "adequate",
            "price_action_status": "positive",
            "risk_status": "normal",
            "relative_strength": 72.0,
            **overrides,
        }
        normalize_strategy_row(row, "stock_momentum")
        return row

    def test_friendly_verdict_momentum_pass(self):
        row = self._make_row("CONSIDER ADDING")
        assert row["friendly_verdict"] == "Momentum Pass"

    def test_friendly_verdict_rejected(self):
        row = self._make_row("AVOID / WEAK TREND")
        assert row["friendly_verdict"] == "Rejected"

    def test_friendly_verdict_watch(self):
        row = self._make_row("WATCH / CONFIRM TREND")
        assert row["friendly_verdict"] == "Watch"

    def test_metrics_contain_momentum_score(self):
        row = self._make_row()
        assert row["metrics"]["momentum_score"] == 75.0

    def test_metrics_contain_trend_status(self):
        row = self._make_row()
        assert row["metrics"]["trend_status"] == "clean"

    def test_metrics_contain_relative_strength(self):
        row = self._make_row()
        assert row["metrics"]["relative_strength"] == 72.0

    def test_consider_adding_eligible_for_daily_opp(self):
        row = self._make_row("CONSIDER ADDING")
        assert row["daily_opportunity_eligible"] is True

    def test_add_on_pullback_eligible_for_daily_opp(self):
        row = self._make_row("ADD ON PULLBACK")
        assert row["daily_opportunity_eligible"] is True

    def test_avoid_not_eligible_for_daily_opp(self):
        row = self._make_row("AVOID / WEAK TREND")
        assert row["daily_opportunity_eligible"] is False

    def test_watch_not_eligible_for_daily_opp(self):
        row = self._make_row("WATCH / CONFIRM TREND")
        assert row["daily_opportunity_eligible"] is False

    def test_gates_include_trend_gates(self):
        row = self._make_row()
        gate_names = [g["name"] for g in row.get("gates", [])]
        assert any("50" in n or "MA" in n for n in gate_names)

    def test_entry_blockers_gate_added_when_present(self):
        row = self._make_row("WATCH / CONFIRM TREND", add_blockers=["Price below 200-day MA"])
        blocker_gates = [g for g in row.get("gates", []) if "blocker" in g.get("id", "").lower()]
        assert blocker_gates
        assert blocker_gates[0]["status"] == "fail"

    def test_strategy_family_is_equity_momentum(self):
        row = self._make_row()
        assert row.get("strategy_family") == "equity_momentum"


# ─────────────────────────────────────────────────────────────────────────────
# normalize_strategy_rows (batch function)
# ─────────────────────────────────────────────────────────────────────────────

class TestNormalizeStrategyRows:
    def test_empty_list_returns_empty(self):
        from app.services.strategy_row_normalization_service import normalize_strategy_rows
        assert normalize_strategy_rows([], "stock_momentum") == []

    def test_normalizes_all_rows(self):
        from app.services.strategy_row_normalization_service import normalize_strategy_rows
        rows = [{"ticker": "AAPL"}, {"ticker": "GOOG"}]
        result = normalize_strategy_rows(rows, "stock_momentum")
        assert len(result) == 2
        assert all(r.get("strategy_row_schema_version") == "30J.v1" for r in result)

    def test_does_not_mutate_originals(self):
        from app.services.strategy_row_normalization_service import normalize_strategy_rows
        original = {"ticker": "AAPL"}
        rows = [original]
        normalize_strategy_rows(rows, "stock_momentum")
        assert "strategy_row_schema_version" not in original

    def test_strips_large_excluded_fields(self):
        from app.services.strategy_row_normalization_service import normalize_strategy_rows
        rows = [{"ticker": "AAPL", "options_chain": {"huge": "data"}, "debug_trace": "long"}]
        result = normalize_strategy_rows(rows, "stock_momentum")
        assert "options_chain" not in result[0]
        assert "debug_trace" not in result[0]

    def test_skips_non_dict_rows(self):
        from app.services.strategy_row_normalization_service import normalize_strategy_rows
        result = normalize_strategy_rows([None, "string", {"ticker": "AAPL"}], "stock_momentum")
        assert len(result) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Lane 8: Daily Opportunity regression
# ─────────────────────────────────────────────────────────────────────────────

class TestDailyOpportunityRegression:
    def test_ff_excluded_from_daily_opportunity(self):
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        row: dict[str, Any] = {}
        normalize_strategy_row(row, "forward_factor_calendar")
        assert row["daily_opportunity_eligible"] is False

    def test_ff_daily_opportunity_reason_mentions_exclusion(self):
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        row: dict[str, Any] = {}
        normalize_strategy_row(row, "forward_factor_calendar")
        reason = row.get("daily_opportunity_reason", "").lower()
        assert "signal" in reason or "gated" in reason or "excluded" in reason

    def test_calendar_entry_allowed_true_means_eligible(self):
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        row: dict[str, Any] = {"calendar_entry_allowed": True}
        normalize_strategy_row(row, "earnings_calendar")
        assert row["daily_opportunity_eligible"] is True

    def test_calendar_entry_allowed_false_means_not_eligible(self):
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        row: dict[str, Any] = {"calendar_entry_allowed": False}
        normalize_strategy_row(row, "earnings_calendar")
        assert row["daily_opportunity_eligible"] is False

    def test_skew_pass_verdict_eligible(self):
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        row: dict[str, Any] = {"verdict": "PASS / OK"}
        normalize_strategy_row(row, "skew_momentum_vertical")
        assert row["daily_opportunity_eligible"] is True

    def test_skew_fail_verdict_not_eligible(self):
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        row: dict[str, Any] = {"verdict": "FAIL / DTE TOO SHORT"}
        normalize_strategy_row(row, "skew_momentum_vertical")
        assert row["daily_opportunity_eligible"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Lane 9: Observation journal readiness
# ─────────────────────────────────────────────────────────────────────────────

class TestObservationJournalReadiness:
    def _normalize(self, row, strategy_id):
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        return normalize_strategy_row(row, strategy_id)

    def test_journal_eligible_true_when_ticker_and_verdict(self):
        row: dict[str, Any] = {"ticker": "AAPL", "verdict": "PASS / OK"}
        self._normalize(row, "skew_momentum_vertical")
        assert row["journal_eligible"] is True

    def test_journal_eligible_false_when_no_ticker(self):
        row: dict[str, Any] = {"verdict": "PASS / OK"}
        self._normalize(row, "skew_momentum_vertical")
        assert row["journal_eligible"] is False

    def test_journal_eligible_false_when_no_verdict(self):
        row: dict[str, Any] = {"ticker": "AAPL"}
        self._normalize(row, "skew_momentum_vertical")
        assert row["journal_eligible"] is False

    def test_observation_key_format(self):
        row: dict[str, Any] = {"ticker": "AAPL", "action": "EARNINGS CALENDAR CANDIDATE"}
        self._normalize(row, "earnings_calendar")
        key = row["observation_key"]
        parts = key.split(":")
        assert parts[0] == "earnings_calendar"
        assert parts[1] == "AAPL"

    def test_observation_key_ff_format(self):
        row: dict[str, Any] = {"ticker": "MSFT"}
        self._normalize(row, "forward_factor_calendar")
        key = row["observation_key"]
        assert key.startswith("forward_factor_calendar:MSFT:")

    def test_observation_key_stock_format(self):
        row: dict[str, Any] = {"ticker": "TSLA", "action": "CONSIDER ADDING"}
        self._normalize(row, "stock_momentum")
        key = row["observation_key"]
        assert "stock_momentum:TSLA:stock_momentum:equity" in key

    def test_observation_refs_is_empty_list(self):
        row: dict[str, Any] = {"ticker": "AAPL"}
        self._normalize(row, "stock_momentum")
        assert row["observation_refs"] == []


# ─────────────────────────────────────────────────────────────────────────────
# Lane 7: Public screener compatibility regression
# ─────────────────────────────────────────────────────────────────────────────

class TestPublicScreenerRegression:
    def test_screener_renders(self):
        from app.main import app
        client = app.test_client()
        resp = client.get("/screener")
        assert resp.status_code == 200

    def test_screener_data_endpoint_no_private_data(self):
        from app.main import app
        client = app.test_client()
        resp = client.get("/screener/data")
        if resp.status_code == 200:
            body = resp.get_json() or {}
            for key in ("broker_auth_status", "user_run_id", "account_value"):
                assert key not in body, f"Private field {key!r} leaked to public screener"

    def test_screener_no_source_unspecified(self):
        from app.main import app
        client = app.test_client()
        resp = client.get("/screener")
        if resp.status_code == 200:
            assert "SOURCE_UNSPECIFIED" not in (resp.data or b"").decode("utf-8", errors="ignore")

    def test_build_public_gate_checklist_returns_canonical_statuses(self):
        from app.services.public_screener_gate_service import build_public_gate_checklist
        valid = {"pass", "watch", "fail", "unknown", "not_applicable", "dry_run", "skipped"}
        for strategy_id in ("forward_factor", "calendar", "skew", "stock_momentum"):
            row: dict[str, Any] = {"verdict": "PASS / OK", "action": "PASS"}
            gates = build_public_gate_checklist(row, strategy_id)
            for gate in gates:
                assert gate["status"] in valid, (
                    f"{strategy_id} gate {gate!r} has non-canonical status"
                )


# ─────────────────────────────────────────────────────────────────────────────
# Safety invariants (CAVEMAN MODE)
# ─────────────────────────────────────────────────────────────────────────────

class TestCavemanModeSafetyInvariants:
    def test_ff_dry_run_config_true(self):
        from app import config
        assert config.FORWARD_FACTOR_DRY_RUN is True

    def test_ff_spec_dry_run_true(self):
        from app.services.strategy_spec_registry import get_spec
        assert get_spec("forward_factor_calendar")["dry_run"] is True

    def test_ff_spec_daily_opportunity_not_allowed(self):
        from app.services.strategy_spec_registry import get_spec
        assert get_spec("forward_factor_calendar")["daily_opportunity_allowed"] is False

    def test_normalization_enforces_ff_can_trade_live_false(self):
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        row: dict[str, Any] = {}
        normalize_strategy_row(row, "forward_factor_calendar")
        assert row["can_trade_live"] is False

    def test_normalization_enforces_ff_dry_run_true(self):
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        row: dict[str, Any] = {}
        normalize_strategy_row(row, "forward_factor_calendar")
        assert row["dry_run"] is True

    def test_normalization_enforces_ff_can_enter_daily_opportunity_false(self):
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        row: dict[str, Any] = {}
        normalize_strategy_row(row, "forward_factor_calendar")
        assert row.get("can_enter_daily_opportunity") is False

    def test_no_trade_execution_in_config(self):
        from app import config
        assert not getattr(config, "TRADE_EXECUTION_ENABLED", False)

    def test_screener_no_provider_calls_triggered(self):
        from app.main import app
        client = app.test_client()
        resp = client.get("/screener/data")
        if resp.status_code == 200:
            data = resp.get_json() or {}
            assert data.get("provider_calls_triggered") is not True

    def test_earnings_trust_single_source_is_warning_not_block(self):
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        from app.services.strategy_gate_service import has_blocking_gate_failure
        row: dict[str, Any] = {"earnings_trust_label": "single_source_verify"}
        normalize_strategy_row(row, "earnings_calendar")
        trust_gates = [g for g in row.get("gates", []) if "trust" in g.get("id", "")]
        assert all(g["status"] == "watch" for g in trust_gates)
        assert not has_blocking_gate_failure(trust_gates)

    def test_earnings_trust_conflict_is_block(self):
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        from app.services.strategy_gate_service import has_blocking_gate_failure
        row: dict[str, Any] = {"earnings_trust_label": "conflict_do_not_trade"}
        normalize_strategy_row(row, "earnings_calendar")
        trust_gates = [g for g in row.get("gates", []) if "trust" in g.get("id", "")]
        assert has_blocking_gate_failure(trust_gates)
