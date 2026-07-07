"""
ASA Patch 29.7 — Path Clearer for Strategy Standardization
Tests covering:
  TKT-040: Python 3.11 f-string fix
  TKT-038: Payload bloat follow-through
  Patch 29H: Scan coverage endpoint + public label
  Patch 29J: Calendar expiration pair diagnostics
  Patch 29K: Lifecycle/positions unification
  Regression: safety invariants
"""
from __future__ import annotations

import py_compile
import importlib
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# TKT-040: Python 3.11 f-string fix
# ─────────────────────────────────────────────────────────────────────────────

class TestTKT040Python311Syntax:
    def test_main_py_compiles_under_python311(self):
        """app/main.py must compile under Python 3.11-compatible syntax."""
        result = py_compile.compile("app/main.py", doraise=True)
        assert result is not None or True  # compile succeeded without SyntaxError

    def test_main_module_importable(self):
        """app.main must import without SyntaxError (validates all top-level f-strings)."""
        import app.main  # noqa: F401 — import verifies no syntax errors at module level
        assert True

    def test_previously_failing_test_collects(self):
        """The test that previously failed collection due to SyntaxError now imports."""
        from app.main import _public_row_card  # noqa: F401
        assert callable(_public_row_card)

    def test_ff_dry_note_produces_correct_html(self):
        """_build_public_strategy_section must produce FF dry-run note for forward_factor_calendar."""
        from app.main import _build_public_strategy_section
        explainer = {
            "short": "test", "why": "test", "blocks": "test",
            "status": "test", "matters": "test",
        }
        result = _build_public_strategy_section(
            "FF Title", "forward_factor_calendar", {}, explainer, dry_run=False,
        )
        assert "dry-run mode" in result
        assert "PASS means volatility" in result

    def test_non_ff_strategy_has_no_dry_note(self):
        """Non-FF strategies must not include the FF dry-run note."""
        from app.main import _build_public_strategy_section
        explainer = {
            "short": "test", "why": "test", "blocks": "test",
            "status": "test", "matters": "test",
        }
        result = _build_public_strategy_section(
            "Stock", "stock_momentum", {}, explainer, dry_run=False,
        )
        assert "dry-run mode" not in result
        assert "volatility relationship" not in result


# ─────────────────────────────────────────────────────────────────────────────
# TKT-038: Payload bloat follow-through
# ─────────────────────────────────────────────────────────────────────────────

class TestTKT038PayloadBloatFollowthrough:
    def _make_strategy_result(self, row_count: int = 3, row_size: int = 50) -> dict[str, Any]:
        items = [{"ticker": f"TICK{i}", "verdict": "PASS", "data": "x" * row_size} for i in range(row_count)]
        return {"rows": items, "pass_count": row_count}

    def test_strategy_row_profile_in_payload_profile(self):
        from app.services.payload_profile_service import build_payload_size_profile
        cal = self._make_strategy_result(5)
        skew = self._make_strategy_result(3)
        result = build_payload_size_profile(
            payload="", positions=[], news=[], recommendations=[],
            snapshot={"_unified_calendar_engine": cal, "_strategy_results": {"skew_momentum_vertical": skew}},
            log=[],
        )
        assert "strategy_row_profile" in result
        srp = result["strategy_row_profile"]
        assert "strategy_results_bytes" in srp
        assert "calendar_rows_bytes" in srp
        assert "calendar_row_count" in srp
        assert "skew_rows_bytes" in srp
        assert "ff_rows_bytes" in srp
        assert "stock_rows_bytes" in srp
        assert srp["calendar_row_count"] == 5
        assert srp["skew_row_count"] == 3
        assert srp["strategy_results_bytes"] >= 0

    def test_payload_profile_includes_summary_json_bytes(self):
        from app.services.payload_profile_service import build_payload_size_profile
        report_summary = {"key": "value", "data": list(range(100))}
        result = build_payload_size_profile(
            payload="", positions=[], news=[], recommendations=[],
            snapshot={}, log=[], report_summary=report_summary,
        )
        assert result["summary_json_bytes"] > 0

    def test_payload_profile_includes_largest_top_level_keys(self):
        from app.services.payload_profile_service import build_payload_size_profile
        result = build_payload_size_profile(
            payload="x" * 500, positions=[], news=[], recommendations=[],
            snapshot={"_pipeline_status": {"data": "abc" * 1000}, "_small": "x"},
            log=[],
        )
        assert "largest_top_level_keys" in result
        keys = result["largest_top_level_keys"]
        assert isinstance(keys, list)
        assert any(k["key"] == "_pipeline_status" for k in keys)

    def test_payload_warning_triggers_above_1mb(self):
        from app.services.payload_profile_service import build_payload_warnings
        large_profile = {"summary_json_bytes": 1_100_000}
        warnings = build_payload_warnings(large_profile)
        # 29.8: level was renamed from "warn" to "warning" for tiered thresholds
        assert any(w["name"] == "payload_size_warning" and w["level"] in ("warn", "warning") for w in warnings)

    def test_payload_critical_warning_above_3mb(self):
        from app.services.payload_profile_service import build_payload_warnings
        huge_profile = {"summary_json_bytes": 3_500_000}
        warnings = build_payload_warnings(huge_profile)
        assert any(w["name"] == "payload_size_warning" and w["level"] == "critical" for w in warnings)

    def test_small_payload_no_warning(self):
        from app.services.payload_profile_service import build_payload_warnings
        result = build_payload_warnings({"summary_json_bytes": 500_000})
        assert not any(w["name"] == "payload_size_warning" for w in result)

    def test_provider_call_warning_above_threshold(self):
        from app.services.payload_profile_service import build_payload_warnings
        warnings = build_payload_warnings({}, provider_calls=201)
        assert any(w["name"] == "provider_call_warning" for w in warnings)

    def test_raw_fields_excluded_from_strategy_summary(self):
        from app.services.developer_snapshot_service import _STRATEGY_SUMMARY_EXCLUDE
        for field in ("raw_json", "raw_provider_payload", "full_chain", "options_chain",
                      "chain_snapshot", "provider_payload", "debug_trace", "lifecycle_log_full"):
            assert field in _STRATEGY_SUMMARY_EXCLUDE, f"{field} must be in _STRATEGY_SUMMARY_EXCLUDE"


# ─────────────────────────────────────────────────────────────────────────────
# Patch 29H: Scan coverage endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestPatch29HScanCoverage:
    def _prod_snapshot(self, ff_evaluated: int = 10, ff_skipped: int = 0, earnings: int = 8) -> dict[str, Any]:
        return {
            "_forward_factor_strategy": {
                "stage_counts": {
                    "universe": 41, "cheap_evaluated": ff_evaluated,
                    "skipped_dev_cap": ff_skipped, "skipped_provider_budget": 0, "chain_sets": 5,
                }
            },
            "_earnings_discovery_quality": {"passed_count": earnings},
            "_pipeline_status": {"run_mode": "prod"},
        }

    def test_no_data_returns_limited_scan(self):
        from app.services.scan_coverage_service import build_scan_coverage
        with patch("app.services.scan_coverage_service.ReportSnapshotRepository") as repo:
            repo.return_value.latest_success.return_value = None
            result = build_scan_coverage()
        assert result["status"] == "no_data"
        assert result["is_demo_quality_scan"] is False
        assert result["coverage_mode_label"] == "Limited scan"
        assert result["provider_calls_triggered"] is False

    def test_full_prod_scan_is_demo_quality(self):
        from app.services.scan_coverage_service import build_scan_coverage
        tradier = self._prod_snapshot()
        with patch("app.services.scan_coverage_service.ReportSnapshotRepository") as repo:
            repo.return_value.latest_success.return_value = {
                "run_id": "run-29h", "mode": "prod",
                "completed_at": "2026-07-06T10:00:00+00:00",
            }
            repo.return_value.load_summary.return_value = {
                "report_data": {"tradier_snapshot": tradier}
            }
            result = build_scan_coverage()
        assert result["is_demo_quality_scan"] is True
        assert result["coverage_mode_label"] == "Full production scan"
        assert result["demo_quality_blockers"] == []
        assert result["provider_calls_triggered"] is False

    def test_dev_cap_makes_scan_limited(self):
        from app.services.scan_coverage_service import build_scan_coverage
        tradier = self._prod_snapshot(ff_evaluated=4, ff_skipped=34)
        with patch("app.services.scan_coverage_service.ReportSnapshotRepository") as repo:
            repo.return_value.latest_success.return_value = {
                "run_id": "run-dev", "mode": "prod",
                "completed_at": "2026-07-06T10:00:00+00:00",
            }
            repo.return_value.load_summary.return_value = {
                "report_data": {"tradier_snapshot": tradier}
            }
            result = build_scan_coverage()
        assert result["is_demo_quality_scan"] is False
        assert result["coverage_mode_label"] == "Limited scan"
        assert any("dev cap" in b for b in result["demo_quality_blockers"])

    def test_low_ff_evaluated_blocks_demo_quality(self):
        from app.services.scan_coverage_service import _demo_quality_blockers
        blockers = _demo_quality_blockers(
            app_mode="prod", ff_skipped_dev_cap=0, ff_evaluated=4,
            earnings_passed=10, is_stale=False,
        )
        assert any("FF evaluated" in b for b in blockers)

    def test_low_earnings_blocks_demo_quality(self):
        from app.services.scan_coverage_service import _demo_quality_blockers
        blockers = _demo_quality_blockers(
            app_mode="prod", ff_skipped_dev_cap=0, ff_evaluated=10,
            earnings_passed=5, is_stale=False,
        )
        assert any("Earnings candidates" in b for b in blockers)

    def test_full_scan_no_blockers(self):
        from app.services.scan_coverage_service import _demo_quality_blockers
        blockers = _demo_quality_blockers(
            app_mode="prod", ff_skipped_dev_cap=0, ff_evaluated=12,
            earnings_passed=9, is_stale=False,
        )
        assert blockers == []

    def test_coverage_mode_label_in_screener_context(self):
        """Public screener context must include coverage_mode_label."""
        from app.main import _build_public_screener_context
        with patch("app.main._load_dashboard_core_report") as mock_load:
            snapshot = {"run_id": "r1", "completed_at": "2026-07-06T10:00:00+00:00", "mode": "prod"}
            tradier: dict[str, Any] = {
                "_pipeline_status": {"run_mode": "prod"},
                "_forward_factor_strategy": {"stage_counts": {"universe": 41, "cheap_evaluated": 10, "skipped_dev_cap": 0, "skipped_provider_budget": 0, "chain_sets": 4}},
                "_earnings_discovery_quality": {"passed_count": 8},
                "_earnings_calendar_strategy": {"items": []},
                "_strategy_results": {},
            }
            mock_load.return_value = (snapshot, {"tradier_snapshot": tradier}, tradier)
            ctx = _build_public_screener_context()
        assert ctx is not None
        assert "coverage_mode_label" in ctx["coverage"]
        assert ctx["coverage"]["coverage_mode_label"] in ("Full production scan", "Limited scan")

    def test_coverage_mode_label_is_limited_when_dev_cap_applied(self):
        from app.main import _build_public_screener_context
        with patch("app.main._load_dashboard_core_report") as mock_load:
            snapshot = {"run_id": "r1", "completed_at": "2026-07-06T10:00:00+00:00", "mode": "prod"}
            tradier: dict[str, Any] = {
                "_pipeline_status": {"run_mode": "prod"},
                "_forward_factor_strategy": {"stage_counts": {"universe": 41, "cheap_evaluated": 4, "skipped_dev_cap": 34, "skipped_provider_budget": 0, "chain_sets": 1}},
                "_earnings_discovery_quality": {"passed_count": 6},
                "_earnings_calendar_strategy": {"items": []},
                "_strategy_results": {},
            }
            mock_load.return_value = (snapshot, {"tradier_snapshot": tradier}, tradier)
            ctx = _build_public_screener_context()
        assert ctx["coverage"]["coverage_mode_label"] == "Limited scan"


# ─────────────────────────────────────────────────────────────────────────────
# Patch 29J: Calendar expiration pair diagnostics
# ─────────────────────────────────────────────────────────────────────────────

class TestPatch29JCalendarExpirationDiagnostics:
    def _near_miss_row(self) -> dict[str, Any]:
        return {
            "ticker": "CTAS",
            "verdict": "NEAR_MISS / EXPIRY_GAP",
            "expiry_near_miss": True,
            "expiry_gap_note": "Nearest expiry 2026-07-11 is 2d after earnings — holiday gap.",
            "expiration_pair_diagnostics": {
                "expiration_pair_status": "fail",
                "expiration_pair_reject_reason": "no_valid_expiration_pair",
                "actual_front_expiration_found": None,
                "actual_back_expiration_found": None,
                "tried_expirations": ["2026-07-11", "2026-07-18"],
                "min_expiration_gap_days": 14,
                "near_miss_expiry": "Nearest expiry 2026-07-11 is 2d after earnings.",
            },
            "criteria": [],
        }

    def _pass_row(self) -> dict[str, Any]:
        return {
            "ticker": "AAPL",
            "verdict": "PASS / ENTRY",
            "expiry_near_miss": False,
            "expiry_gap_note": "",
            "expiration_pair_diagnostics": {
                "expiration_pair_status": "pass",
                "actual_front_expiration_found": "2026-07-11",
                "actual_back_expiration_found": "2026-08-15",
                "front_before_earnings": True,
                "gap_days": 35,
                "min_expiration_gap_days": 14,
            },
            "criteria": [],
        }

    def test_near_miss_row_has_expiration_pair_diagnostics(self):
        row = self._near_miss_row()
        assert "expiration_pair_diagnostics" in row
        epd = row["expiration_pair_diagnostics"]
        assert epd["expiration_pair_status"] == "fail"
        assert epd["actual_front_expiration_found"] is None
        assert len(epd["tried_expirations"]) > 0

    def test_calendar_gate_checklist_shows_expiration_fail(self):
        from app.services.public_screener_gate_service import _calendar_gate_checklist
        row = self._near_miss_row()
        checklist = _calendar_gate_checklist(row)
        exp_gate = next((g for g in checklist if "xpiration" in g["name"]), None)
        assert exp_gate is not None, "Expected an expiration pair gate in checklist"
        assert exp_gate["status"] in ("fail", "watch")

    def test_calendar_gate_checklist_shows_expiration_pass(self):
        from app.services.public_screener_gate_service import _calendar_gate_checklist
        row = self._pass_row()
        checklist = _calendar_gate_checklist(row)
        exp_gate = next((g for g in checklist if "xpiration" in g["name"]), None)
        assert exp_gate is not None
        assert exp_gate["status"] == "pass"
        assert "2026-07-11" in exp_gate["detail"]

    def test_near_miss_gate_shows_reject_reason(self):
        from app.services.public_screener_gate_service import _calendar_gate_checklist
        row = {
            "ticker": "CTAS",
            "expiry_near_miss": False,
            "expiry_gap_note": "",
            "expiration_pair_diagnostics": {
                "expiration_pair_status": "near_miss",
                "expiration_pair_reject_reason": "front_leg_after_earnings_near_miss",
                "actual_front_expiration_found": "2026-07-11",
                "actual_back_expiration_found": "2026-08-15",
            },
            "criteria": [],
        }
        checklist = _calendar_gate_checklist(row)
        exp_gate = next((g for g in checklist if "xpiration" in g["name"]), None)
        assert exp_gate is not None
        assert exp_gate["status"] == "watch"

    def test_trade_row_forwards_expiration_pair_diagnostics(self):
        from app.services.unified_calendar_trade_engine_service import _build_new_trade_row
        event = {
            "ticker": "CTAS",
            "earnings_date": "2026-07-09",
            "earnings_time": "amc",
            "sources_seen": ["finnhub"],
            "checks": [],
            "expiry_near_miss": True,
            "expiry_gap_note": "Gap note",
            "expiration_pair_diagnostics": {
                "expiration_pair_status": "fail",
                "expiration_pair_reject_reason": "no_valid_expiration_pair",
            },
        }
        row = _build_new_trade_row(event, {}, {})
        assert "expiration_pair_diagnostics" in row
        assert row["expiration_pair_diagnostics"]["expiration_pair_status"] == "fail"

    def test_expiration_pair_diagnostics_empty_by_default(self):
        """Trade rows without quality precheck get an empty diagnostics dict."""
        from app.services.unified_calendar_trade_engine_service import _build_new_trade_row
        row = _build_new_trade_row({"ticker": "AAPL"}, {}, {})
        assert isinstance(row.get("expiration_pair_diagnostics"), dict)


# ─────────────────────────────────────────────────────────────────────────────
# Patch 29K: Lifecycle / positions unification
# ─────────────────────────────────────────────────────────────────────────────

class TestPatch29KLifecyclePositionsUnification:
    def _sbux_lifecycle_checks(self) -> dict[str, Any]:
        return {
            "source": "calendar_lifecycle_v1",
            "enabled": True,
            "has_data": True,
            "checks": [
                {
                    "ticker": "SBUX",
                    "action": "RECHECK BEFORE CLOSE",
                    "option_type": "call",
                    "strike": 90.0,
                    "front_expiration": "2026-07-11",
                    "back_expiration": "2026-08-15",
                    "front_dte": 5,
                    "back_dte": 40,
                    "short_leg_moneyness_pct": -1.5,
                    "short_leg_itm": False,
                    "short_leg_extrinsic_value": 0.35,
                    "assignment_risk_level": "Moderate",
                    "assignment_risk_reasons": ["Short leg near the money."],
                    "estimated_pnl_pct": 22.5,
                    "entry_debit_estimate": 1.20,
                    "target_debit": 1.80,
                    "stop_debit": 0.60,
                    "current_mid_debit": 1.47,
                    "reasons": ["Entry debit available."],
                    "risks": ["Short front leg inside review window."],
                    "short_front_leg": {"strike": 90.0, "expiration": "2026-07-11"},
                    "long_back_leg": {"strike": 90.0, "expiration": "2026-08-15"},
                }
            ],
            "summary": {"overall_action": "RECHECK BEFORE CLOSE"},
        }

    def test_lifecycle_summary_extracts_from_report(self):
        from app.api.advisor import _lifecycle_summary_from_report
        report = {
            "tradier_snapshot": {
                "_calendar_lifecycle_checks": self._sbux_lifecycle_checks()
            }
        }
        result = _lifecycle_summary_from_report(report)
        assert result["has_data"] is True
        assert result["active_calendar_count"] == 1
        assert len(result["calendar_structures"]) == 1
        sbux = result["calendar_structures"][0]
        assert sbux["ticker"] == "SBUX"
        assert sbux["assignment_risk"] == "Moderate"
        assert sbux["recheck_before_close"] is True

    def test_lifecycle_summary_no_data_returns_empty(self):
        from app.api.advisor import _lifecycle_summary_from_report
        result = _lifecycle_summary_from_report(None)
        assert result["has_data"] is False
        assert result["active_calendar_count"] == 0
        assert result["calendar_structures"] == []

    def test_lifecycle_structure_exposes_assignment_risk(self):
        from app.api.advisor import _lifecycle_check_to_structure
        check = self._sbux_lifecycle_checks()["checks"][0]
        structure = _lifecycle_check_to_structure(check)
        assert structure["assignment_risk"] == "Moderate"
        assert structure["short_leg_extrinsic_value"] == 0.35
        assert structure["short_leg_extrinsic_value_status"] == "available"
        assert structure["lifecycle_status"] == "RECHECK BEFORE CLOSE"

    def test_lifecycle_structure_extrinsic_unavailable_when_none(self):
        from app.api.advisor import _lifecycle_check_to_structure
        check = {"ticker": "SBUX", "action": "HOLD / MONITOR", "short_leg_extrinsic_value": None}
        structure = _lifecycle_check_to_structure(check)
        assert structure["short_leg_extrinsic_value_status"] == "unavailable"
        assert structure["short_leg_extrinsic_value"] is None

    def test_overlay_lifecycle_enriches_options_positions(self):
        from app.api.advisor import _overlay_lifecycle
        options = [{"ticker": "SBUX", "strategy_type": "earnings_calendar", "legs": [{"strike": 90.0}]}]
        checks = self._sbux_lifecycle_checks()["checks"]
        _overlay_lifecycle(options, checks)
        assert options[0]["lifecycle_status"] == "RECHECK BEFORE CLOSE"
        assert options[0]["assignment_risk"] == "Moderate"
        assert options[0]["recheck_before_close"] is True
        assert options[0]["short_leg_extrinsic_value"] == 0.35

    def test_overlay_lifecycle_noop_when_no_checks(self):
        from app.api.advisor import _overlay_lifecycle
        options = [{"ticker": "SBUX", "strategy_type": "earnings_calendar", "legs": []}]
        _overlay_lifecycle(options, [])
        assert "lifecycle_status" not in options[0]

    def test_empty_positions_payload_has_lifecycle_fields(self):
        from app.api.advisor import _empty_positions_payload
        payload = _empty_positions_payload(snapshot=None, personalized=False)
        assert "active_calendar_count" in payload
        assert "calendar_structures" in payload
        assert "lifecycle_status" in payload
        assert payload["active_calendar_count"] == 0
        assert payload["calendar_structures"] == []


# ─────────────────────────────────────────────────────────────────────────────
# Regression: safety invariants
# ─────────────────────────────────────────────────────────────────────────────

class TestPatch297RegressionGuard:
    def test_ff_remains_dry_run(self):
        from app import config
        assert config.FORWARD_FACTOR_DRY_RUN is True

    def test_earnings_conflict_blocks(self):
        from app.services.earnings_trust_service import normalize_earnings_trust
        row = normalize_earnings_trust({
            "earnings_date": "2026-07-09",
            "sources_seen": ["finnhub", "reference"],
            "earnings_source_conflict": True,
        })
        assert row["calendar_entry_allowed"] is False
        assert row["earnings_trust_label"] == "conflict_do_not_trade"

    def test_single_source_still_warns_not_blocks(self):
        from app.services.earnings_trust_service import normalize_earnings_trust
        row = normalize_earnings_trust({
            "earnings_date": "2026-07-09",
            "sources_seen": ["finnhub"],
        })
        assert row["calendar_entry_allowed"] is True
        assert row["earnings_trust_label"] == "single_source_verify"

    def test_read_only_flag_on_scan_coverage_no_data(self):
        from app.services.scan_coverage_service import build_scan_coverage
        with patch("app.services.scan_coverage_service.ReportSnapshotRepository") as repo:
            repo.return_value.latest_success.return_value = None
            result = build_scan_coverage()
        assert result["provider_calls_triggered"] is False

    def test_read_only_flag_on_developer_snapshot(self):
        from app.services.developer_snapshot_service import build_developer_snapshot
        with patch("app.services.developer_snapshot_service.ReportSnapshotRepository") as repo:
            repo.return_value.latest_success.return_value = None
            result = build_developer_snapshot("latest")
        assert result.get("provider_calls_triggered") is False

    def test_trade_execution_remains_disabled(self):
        from app import config
        assert not getattr(config, "TRADE_EXECUTION_ENABLED", False)

    def test_no_raw_forbidden_fields_in_strategy_summary_exclude(self):
        from app.services.developer_snapshot_service import _STRATEGY_SUMMARY_EXCLUDE
        for field in ("raw_json", "raw_provider_payload", "options_chain", "debug_trace"):
            assert field in _STRATEGY_SUMMARY_EXCLUDE

    def test_scan_coverage_endpoint_registered_in_admin_blueprint(self):
        import app.main as main_module
        app_obj = main_module.app
        endpoint_map = {r.endpoint: r.rule for r in app_obj.url_map.iter_rules()}
        assert any("scan-coverage" in rule for rule in endpoint_map.values()), (
            "/api/admin/scan-coverage must be registered"
        )
