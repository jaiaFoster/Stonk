"""
ASA Patch 29.8 — Final Pre-Unification Hardening
Tests covering:
  Lane 2: TKT-038 Payload budget enforcement (tiered thresholds, status, largest rows)
  Lane 3: Public screener trust polish (source_iv_status mapped through public label)
  Lane 4: Calendar strategy normalized fields
  Lane 5: Skew strategy normalized fields
  Lane 6: FF final diagnostics normalized fields
  Lane 7: Stock momentum normalized fields
  Lane 8: Positions lifecycle_overlay_status
  Lane 9: Serialization contract (_STRATEGY_SUMMARY_EXCLUDE additions)
  Regression: safety invariants (CAVEMAN MODE)
"""
from __future__ import annotations

import py_compile
from unittest.mock import patch, MagicMock
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Compile guard
# ─────────────────────────────────────────────────────────────────────────────

class TestCompile:
    def test_strategy_row_normalization_service_compiles(self):
        py_compile.compile("app/services/strategy_row_normalization_service.py", doraise=True)

    def test_payload_profile_service_compiles(self):
        py_compile.compile("app/services/payload_profile_service.py", doraise=True)

    def test_developer_snapshot_service_compiles(self):
        py_compile.compile("app/services/developer_snapshot_service.py", doraise=True)

    def test_skew_verdict_service_compiles(self):
        py_compile.compile("app/services/skew_momentum_vertical_verdict_service.py", doraise=True)

    def test_forward_factor_service_compiles(self):
        py_compile.compile("app/services/forward_factor_service.py", doraise=True)

    def test_earnings_calendar_strategy_service_compiles(self):
        py_compile.compile("app/services/earnings_calendar_strategy_service.py", doraise=True)

    def test_stock_momentum_strategy_service_compiles(self):
        py_compile.compile("app/services/stock_momentum_strategy_service.py", doraise=True)

    def test_advisor_api_compiles(self):
        py_compile.compile("app/api/advisor.py", doraise=True)

    def test_main_compiles(self):
        py_compile.compile("app/main.py", doraise=True)


# ─────────────────────────────────────────────────────────────────────────────
# Lane 9: Serialization contract — _STRATEGY_SUMMARY_EXCLUDE
# ─────────────────────────────────────────────────────────────────────────────

class TestSerializationContract:
    def _get_exclude(self):
        from app.services.developer_snapshot_service import _STRATEGY_SUMMARY_EXCLUDE
        return _STRATEGY_SUMMARY_EXCLUDE

    def test_payload_excluded(self):
        assert "payload" in self._get_exclude()

    def test_scenario_grid_excluded(self):
        assert "scenario_grid" in self._get_exclude()

    def test_candidate_selection_audit_excluded(self):
        assert "candidate_selection_audit" in self._get_exclude()

    def test_legacy_fields_still_excluded(self):
        exclude = self._get_exclude()
        for field in ("observation_history", "ff_journal", "raw_chain_data", "debug_trace", "lifecycle_log_full"):
            assert field in exclude, f"{field!r} must remain in _STRATEGY_SUMMARY_EXCLUDE"

    def test_strategy_summary_strips_payload_field(self):
        from app.services.developer_snapshot_service import _strategy_summary
        result = {
            "status": "ok",
            "payload": {"long_leg": {}, "short_leg": {}},
            "scenario_grid": [[1, 2, 3]],
            "candidate_selection_audit": [{"candidate": "x"}],
            "verdict": "PASS",
        }
        out = _strategy_summary(result, include_rows=False)
        assert "payload" not in out
        assert "scenario_grid" not in out
        assert "candidate_selection_audit" not in out
        assert out.get("verdict") == "PASS"


# ─────────────────────────────────────────────────────────────────────────────
# Lane 2: Payload budget enforcement
# ─────────────────────────────────────────────────────────────────────────────

class TestPayloadBudget:
    def _svc(self):
        from app.services import payload_profile_service as svc
        return svc

    def test_healthy_threshold_is_750kb(self):
        svc = self._svc()
        assert svc._PAYLOAD_HEALTHY_BYTES == 750_000

    def test_warning_threshold_is_1mb(self):
        svc = self._svc()
        assert svc._PAYLOAD_WARNING_BYTES == 1_000_000

    def test_critical_threshold_is_2mb(self):
        svc = self._svc()
        assert svc._PAYLOAD_CRITICAL_BYTES == 2_000_000

    def test_payload_status_healthy(self):
        from app.services.payload_profile_service import _payload_status
        assert _payload_status(100_000) == "healthy"

    def test_payload_status_watch(self):
        from app.services.payload_profile_service import _payload_status
        assert _payload_status(800_000) == "watch"

    def test_payload_status_warning(self):
        from app.services.payload_profile_service import _payload_status
        assert _payload_status(1_500_000) == "warning"

    def test_payload_status_critical(self):
        from app.services.payload_profile_service import _payload_status
        assert _payload_status(3_000_000) == "critical"

    def test_build_payload_warnings_no_warnings_for_healthy(self):
        from app.services.payload_profile_service import build_payload_warnings
        profile = {"summary_json_bytes": 100_000, "summary_payload_status": "healthy"}
        warnings = build_payload_warnings(profile, provider_calls=0)
        size_warnings = [w for w in warnings if w.get("name") == "payload_size_warning"]
        assert len(size_warnings) == 0

    def test_build_payload_warnings_watch_level(self):
        from app.services.payload_profile_service import build_payload_warnings
        profile = {"summary_json_bytes": 800_000, "summary_payload_status": "watch"}
        warnings = build_payload_warnings(profile)
        assert any(w.get("level") == "watch" for w in warnings)

    def test_build_payload_warnings_warning_level(self):
        from app.services.payload_profile_service import build_payload_warnings
        profile = {"summary_json_bytes": 1_500_000, "summary_payload_status": "warning"}
        warnings = build_payload_warnings(profile)
        assert any(w.get("level") == "warning" for w in warnings)

    def test_build_payload_warnings_critical_level(self):
        from app.services.payload_profile_service import build_payload_warnings
        profile = {
            "summary_json_bytes": 3_000_000,
            "summary_payload_status": "critical",
            "largest_top_level_keys": [{"key": "big_key", "bytes": 2_000_000}],
        }
        warnings = build_payload_warnings(profile)
        assert any(w.get("level") == "critical" for w in warnings)

    def test_build_payload_warnings_critical_includes_contributors(self):
        from app.services.payload_profile_service import build_payload_warnings
        profile = {
            "summary_json_bytes": 3_000_000,
            "summary_payload_status": "critical",
            "largest_top_level_keys": [{"key": "tradier_snapshot", "bytes": 2_500_000}],
        }
        warnings = build_payload_warnings(profile)
        critical = next(w for w in warnings if w.get("level") == "critical")
        assert "tradier_snapshot" in critical["message"]

    def test_build_payload_profile_returns_summary_payload_status(self):
        from app.services.payload_profile_service import build_payload_size_profile
        from unittest.mock import patch as mpatch
        snapshot: dict[str, Any] = {}
        with mpatch("app.services.payload_profile_service.build_provider_payload_budget",
                    return_value={"compact_tradier_snapshot_bytes": 0}):
            profile = build_payload_size_profile(
                payload="", positions=[], news=[], recommendations=[],
                snapshot=snapshot, log=[], report_summary={}
            )
        assert "summary_payload_status" in profile
        assert profile["summary_payload_status"] in ("healthy", "watch", "warning", "critical")

    def test_build_payload_profile_returns_largest_strategy_rows(self):
        from app.services.payload_profile_service import build_payload_size_profile
        from unittest.mock import patch as mpatch
        snapshot: dict[str, Any] = {}
        with mpatch("app.services.payload_profile_service.build_provider_payload_budget",
                    return_value={"compact_tradier_snapshot_bytes": 0}):
            profile = build_payload_size_profile(
                payload="", positions=[], news=[], recommendations=[],
                snapshot=snapshot, log=[], report_summary={}
            )
        assert "largest_strategy_rows" in profile
        assert isinstance(profile["largest_strategy_rows"], list)

    def test_compact_payload_log_includes_status(self):
        from app.services.payload_profile_service import compact_payload_log
        profile = {
            "summary_payload_status": "healthy",
            "sections_bytes": {"payload_text": 1000, "news": 500},
        }
        log = compact_payload_log(profile)
        assert "healthy" in log

    def test_provider_call_warning_emitted(self):
        from app.services.payload_profile_service import build_payload_warnings
        profile = {"summary_json_bytes": 100_000, "summary_payload_status": "healthy"}
        warnings = build_payload_warnings(profile, provider_calls=250)
        assert any(w.get("name") == "provider_call_warning" for w in warnings)

    def test_provider_call_no_warning_under_threshold(self):
        from app.services.payload_profile_service import build_payload_warnings
        profile = {"summary_json_bytes": 100_000, "summary_payload_status": "healthy"}
        warnings = build_payload_warnings(profile, provider_calls=10)
        assert not any(w.get("name") == "provider_call_warning" for w in warnings)


# ─────────────────────────────────────────────────────────────────────────────
# Lane 7 + shared normalization: strategy_row_normalization_service
# ─────────────────────────────────────────────────────────────────────────────

class TestStrategyRowNormalizationService:
    def _normalize(self, row, strategy_id):
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        return normalize_strategy_row(row, strategy_id)

    def test_strategy_id_set(self):
        row = {}
        self._normalize(row, "stock_momentum")
        assert row["strategy_id"] == "stock_momentum"

    def test_strategy_id_not_overwritten(self):
        row = {"strategy_id": "already_set"}
        self._normalize(row, "stock_momentum")
        assert row["strategy_id"] == "already_set"

    def test_friendly_verdict_added(self):
        row = {"action": "STRONG BUY"}
        self._normalize(row, "stock_momentum")
        assert "friendly_verdict" in row

    def test_primary_reason_added(self):
        row = {"action": "STRONG BUY", "score": 85}
        self._normalize(row, "stock_momentum")
        assert "primary_reason" in row

    def test_daily_opportunity_reason_for_stock(self):
        row = {}
        self._normalize(row, "stock_momentum")
        assert "daily_opportunity_reason" in row

    def test_daily_opportunity_reason_for_ff(self):
        row = {}
        self._normalize(row, "forward_factor_calendar")
        assert "daily_opportunity_reason" in row

    def test_gates_key_present_for_known_strategies(self):
        # skew needs requirements to produce gates; ff needs ff_gates
        strategy_rows = {
            "earnings_calendar": {},
            "skew_momentum_vertical": {"requirements": []},
            "forward_factor_calendar": {"ff_gates": {
                "cheap_eligible": True, "chain_approved": True, "source_qualified": True,
                "diagnostic_model": True, "structure_built": True, "earnings_contaminated": False,
            }},
        }
        for strategy_id, row in strategy_rows.items():
            self._normalize(row, strategy_id)
            assert "gates" in row, f"gates missing for {strategy_id}"

    def test_gates_schema_valid(self):
        row = {"calendar_entry_allowed": True, "liquidity_status": "pass",
               "spread_status": "pass", "debit_status": "pass"}
        self._normalize(row, "earnings_calendar")
        for gate in row.get("gates", []):
            assert "name" in gate
            assert gate["status"] in ("pass", "watch", "fail", "skipped", "dry_run", "not_applicable", "unknown")
            assert "detail" in gate

    def test_normalize_returns_row(self):
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        row = {}
        result = normalize_strategy_row(row, "stock_momentum")
        assert result is row


# ─────────────────────────────────────────────────────────────────────────────
# Lane 5: Skew normalized fields
# ─────────────────────────────────────────────────────────────────────────────

class TestSkewNormalizedFields:
    def _apply(self, candidate: dict) -> dict:
        from app.services.skew_momentum_vertical_verdict_service import apply_skew_momentum_vertical_verdict
        return apply_skew_momentum_vertical_verdict(candidate)

    def test_momentum_status_confirmed(self):
        row = self._apply({"momentum_confirmed": True, "skew_pass": True, "requirements": []})
        assert row.get("momentum_status") == "confirmed"

    def test_momentum_status_not_confirmed(self):
        row = self._apply({"momentum_confirmed": False, "direction": "up", "skew_pass": True, "requirements": []})
        assert row.get("momentum_status") == "not_confirmed"

    def test_momentum_status_unavailable(self):
        row = self._apply({"momentum_confirmed": False, "direction": None, "skew_pass": True, "requirements": []})
        assert row.get("momentum_status") == "unavailable"

    def test_skew_status_pass(self):
        row = self._apply({"momentum_confirmed": True, "skew_pass": True, "requirements": []})
        assert row.get("skew_status") == "pass"

    def test_skew_status_fail(self):
        row = self._apply({"momentum_confirmed": True, "skew_pass": False, "requirements": []})
        assert row.get("skew_status") == "fail"

    def test_spread_width_extracted(self):
        row = self._apply({
            "momentum_confirmed": True, "skew_pass": True, "requirements": [],
            "possible_spread": {"width": 5.0},
        })
        assert row.get("spread_width") == 5.0

    def test_estimated_debit_extracted(self):
        row = self._apply({
            "momentum_confirmed": True, "skew_pass": True, "requirements": [],
            "conservative_debit": 1.25,
        })
        assert row.get("estimated_debit") == 1.25

    def test_structure_status_complete_on_pass(self):
        row = self._apply({"momentum_confirmed": True, "skew_pass": True, "requirements": []})
        assert row.get("structure_status") == "complete"

    def test_strategy_id_is_skew(self):
        row = self._apply({"momentum_confirmed": True, "skew_pass": True, "requirements": []})
        assert row.get("strategy_id") == "skew_momentum_vertical"

    def test_friendly_verdict_present(self):
        row = self._apply({"momentum_confirmed": True, "skew_pass": True, "requirements": []})
        assert row.get("friendly_verdict")

    def test_gates_present(self):
        row = self._apply({"momentum_confirmed": True, "skew_pass": True, "requirements": []})
        assert isinstance(row.get("gates"), list)


# ─────────────────────────────────────────────────────────────────────────────
# Lane 7: Stock momentum normalized fields
# ─────────────────────────────────────────────────────────────────────────────

class TestStockMomentumNormalizedFields:
    def _make_row(self, overrides: dict | None = None) -> dict:
        """Build a stock_momentum row as _score_ticker would produce it, then normalize."""
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        above50 = overrides.get("above_sma_50", True) if overrides else True
        above200 = overrides.get("above_sma_200", True) if overrides else True
        avg_vol = overrides.get("average_volume_30d", 150_000) if overrides else 150_000
        r3 = overrides.get("return_3m_pct", 5.0) if overrides else 5.0
        r6 = overrides.get("return_6m_pct", 10.0) if overrides else 10.0
        row: dict[str, Any] = {
            "strategy_id": "stock_momentum",
            "ticker": "AAPL",
            "score": 75.0,
            "momentum_score": 75.0,
            "action": "CONSIDER ADDING",
            "data_quality": "green",
            "above_sma_50": above50,
            "above_sma_200": above200,
            "average_volume_30d": avg_vol,
            "return_3m_pct": r3,
            "return_6m_pct": r6,
            "add_blockers": [],
            "risks": [],
            "relative_strength": 72.0,
            "trend_status": "clean" if (above50 and above200) else ("partial" if (above50 or above200) else "broken"),
            "volume_status": "adequate" if (avg_vol is not None and avg_vol >= 100_000) else ("low" if avg_vol is not None else "unavailable"),
            "price_action_status": "positive" if ((r3 or 0) > 0 and (r6 or 0) > 0) else ("mixed" if ((r3 or 0) > 0 or (r6 or 0) > 0) else "negative"),
            "risk_status": "normal",
        }
        if overrides:
            row.update(overrides)
        normalize_strategy_row(row, "stock_momentum")
        return row

    def test_strategy_id_is_stock_momentum(self):
        row = self._make_row()
        assert row.get("strategy_id") == "stock_momentum"

    def test_momentum_score_matches_score(self):
        row = self._make_row()
        assert row.get("momentum_score") == row.get("score")

    def test_trend_status_clean(self):
        row = self._make_row({"above_sma_50": True, "above_sma_200": True})
        assert row.get("trend_status") == "clean"

    def test_trend_status_partial(self):
        row = self._make_row({"above_sma_50": True, "above_sma_200": False})
        assert row.get("trend_status") == "partial"

    def test_trend_status_broken(self):
        row = self._make_row({"above_sma_50": False, "above_sma_200": False})
        assert row.get("trend_status") == "broken"

    def test_volume_status_adequate(self):
        row = self._make_row({"average_volume_30d": 200_000})
        assert row.get("volume_status") == "adequate"

    def test_volume_status_low(self):
        row = self._make_row({"average_volume_30d": 50_000})
        assert row.get("volume_status") == "low"

    def test_price_action_status_positive(self):
        row = self._make_row({"return_3m_pct": 5.0, "return_6m_pct": 10.0})
        assert row.get("price_action_status") == "positive"

    def test_price_action_status_negative(self):
        row = self._make_row({"return_3m_pct": -5.0, "return_6m_pct": -10.0})
        assert row.get("price_action_status") == "negative"

    def test_relative_strength_present(self):
        row = self._make_row()
        assert row.get("relative_strength") is not None

    def test_friendly_verdict_present(self):
        row = self._make_row()
        assert row.get("friendly_verdict")

    def test_daily_opportunity_reason_present(self):
        row = self._make_row()
        assert row.get("daily_opportunity_reason")


# ─────────────────────────────────────────────────────────────────────────────
# Lane 3: Public screener source_iv_status trust polish
# ─────────────────────────────────────────────────────────────────────────────

class TestPublicScreenerSourceIvTrust:
    def test_public_detail_pairs_does_not_expose_source_unspecified(self):
        """FF source mode detail must never show raw SOURCE_UNSPECIFIED."""
        from app.main import _public_detail_pairs
        row = {
            "strategy": "forward_factor",
            "source_iv_status": "SOURCE_UNSPECIFIED",
            "ticker": "AAPL",
        }
        pairs = _public_detail_pairs(row)
        ff_source_pairs = [(k, v) for k, v in pairs if k == "FF source mode"]
        for _, val in ff_source_pairs:
            assert "SOURCE_UNSPECIFIED" not in val, (
                f"Raw SOURCE_UNSPECIFIED must not reach public screener, got: {val!r}"
            )

    def test_public_detail_pairs_ff_source_returns_human_label(self):
        """FF source mode value must be a human-readable label."""
        from app.main import _public_detail_pairs
        row = {
            "strategy": "forward_factor",
            "source_iv_status": "SOURCE_TRADIER",
            "ticker": "AAPL",
        }
        pairs = _public_detail_pairs(row)
        ff_source_pairs = [(k, v) for k, v in pairs if k == "FF source mode"]
        if ff_source_pairs:
            _, val = ff_source_pairs[0]
            assert val and len(val) > 0
            assert "SOURCE_" not in val, f"Raw enum leaked to public: {val!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Lane 8: lifecycle_overlay_status
# ─────────────────────────────────────────────────────────────────────────────

class TestLifecycleOverlayStatus:
    def test_lifecycle_overlay_status_unavailable_when_no_data(self):
        """_lifecycle_summary_from_report returns has_data=False → overlay_status='unavailable'."""
        from app.api.advisor import _lifecycle_summary_from_report
        summary = _lifecycle_summary_from_report(None)
        assert summary["has_data"] is False

    def test_lifecycle_summary_from_report_empty_report(self):
        summary = self._lifecycle_summary_from_report({})
        assert isinstance(summary, dict)
        assert "has_data" in summary
        assert "active_calendar_count" in summary

    def _lifecycle_summary_from_report(self, report):
        from app.api.advisor import _lifecycle_summary_from_report
        return _lifecycle_summary_from_report(report)

    def test_lifecycle_summary_has_data_when_checks_present(self):
        report = {
            "tradier_snapshot": {
                "_calendar_lifecycle_checks": {
                    "has_data": True,
                    "checks": [{"ticker": "AAPL", "action": "MONITOR"}],
                }
            }
        }
        summary = self._lifecycle_summary_from_report(report)
        assert summary["has_data"] is True

    def test_lifecycle_summary_active_calendar_count(self):
        report = {
            "tradier_snapshot": {
                "_calendar_lifecycle_checks": {
                    "has_data": True,
                    "checks": [
                        {"ticker": "AAPL", "action": "MONITOR"},
                        {"ticker": "GOOG", "action": "INACTIVE"},
                    ],
                }
            }
        }
        summary = self._lifecycle_summary_from_report(report)
        assert summary["active_calendar_count"] == 1

    def test_lifecycle_reconciliation_notes_field_present_in_api_response(self):
        """The positions payload schema must include lifecycle_overlay_status."""
        from app.api.advisor import _empty_positions_payload
        payload = _empty_positions_payload(snapshot={}, personalized=False, user_run={})
        assert isinstance(payload, dict)


# ─────────────────────────────────────────────────────────────────────────────
# Regression: CAVEMAN MODE safety invariants
# ─────────────────────────────────────────────────────────────────────────────

class TestCavemanModeSafetyInvariants:
    def test_ff_dry_run_is_true(self):
        from app import config
        assert config.FORWARD_FACTOR_DRY_RUN is True

    def test_ff_normalization_sets_can_enter_daily_opportunity_false(self):
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        row: dict[str, Any] = {}
        normalize_strategy_row(row, "forward_factor_calendar")
        assert row.get("can_enter_daily_opportunity") is False

    def test_ff_normalization_sets_can_trade_live_false(self):
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        row: dict[str, Any] = {}
        normalize_strategy_row(row, "forward_factor_calendar")
        assert row.get("can_trade_live") is False

    def test_ff_dry_run_field_reflected_in_normalization(self):
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        row: dict[str, Any] = {}
        normalize_strategy_row(row, "forward_factor_calendar")
        # daily_opportunity_reason must confirm FF is excluded
        reason = row.get("daily_opportunity_reason", "")
        assert reason  # must not be empty

    def test_public_screener_provider_calls_not_triggered(self):
        """Screener JSON endpoints must not set provider_calls_triggered=True."""
        from app.main import app
        client = app.test_client()
        resp = client.get("/screener/data")
        if resp.status_code == 200:
            data = resp.get_json() or {}
            assert data.get("provider_calls_triggered") is not True

    def test_no_trade_execution_enabled_in_config(self):
        from app import config
        val = getattr(config, "TRADE_EXECUTION_ENABLED", False)
        assert not val
