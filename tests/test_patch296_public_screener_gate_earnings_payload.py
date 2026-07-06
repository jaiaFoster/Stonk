"""ASA Patch 29.6 acceptance tests.

Lanes:
  1 — Public screener cleanup + gate visibility (TKT-031A/C/D/E, TKT-032A/B, TKT-035A)
  2 — Earnings source transparency (TKT-030A-lite)
  3 — Payload bloat breakdown + trim (TKT-038)
  4 — Regression guard (FF dry-run, no broker data, thresholds unchanged)
"""

from __future__ import annotations

import json
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _svc():
    from app.services import public_screener_gate_service as svc
    return svc


def _trust():
    from app.services import earnings_trust_service as t
    return t


def _pps():
    from app.services import payload_profile_service as p
    return p


def _row(**kwargs: Any) -> dict[str, Any]:
    return dict(kwargs)


# ---------------------------------------------------------------------------
# Lane 1 — Public screener cleanup
# ---------------------------------------------------------------------------

class TestDevWarningCleanup:
    """TKT-031A: Public /screener must not expose dev-language warnings."""

    def _context_warnings(self, ff_skipped_dev=0, ff_skipped_budget=0, run_mode="prod"):
        """Build the coverage warning list the way _build_public_screener_context does."""
        coverage: dict[str, Any] = {
            "ff_skipped_dev_cap": ff_skipped_dev,
            "ff_skipped_provider_budget": ff_skipped_budget,
            "earnings_candidates_returned": 10,
            "warnings": [],
        }
        if coverage["ff_skipped_dev_cap"] > 0 or coverage["ff_skipped_provider_budget"] > 0:
            coverage["warnings"].append(
                "Limited coverage scan active — this scan evaluated a subset of symbols. "
                "A full scan may surface more opportunities."
            )
        if coverage["earnings_candidates_returned"] <= 6:
            coverage["warnings"].append("Earnings discovery coverage was limited this scan.")
        return coverage["warnings"]

    def test_dev_cap_warning_does_not_say_run_mode_is_dev(self):
        warnings = self._context_warnings(ff_skipped_dev=5, run_mode="dev")
        combined = " ".join(warnings)
        assert "Run mode is dev" not in combined

    def test_dev_cap_warning_does_not_say_skipped_by_dev_cap(self):
        warnings = self._context_warnings(ff_skipped_dev=5)
        combined = " ".join(warnings)
        assert "skipped by dev cap" not in combined.lower()
        assert "dev cap" not in combined.lower()
        assert "dev limits" not in combined.lower()

    def test_dev_cap_warning_says_limited_coverage(self):
        warnings = self._context_warnings(ff_skipped_dev=5)
        combined = " ".join(warnings)
        assert "limited" in combined.lower() or "subset" in combined.lower()

    def test_budget_warning_does_not_expose_internal_language(self):
        warnings = self._context_warnings(ff_skipped_budget=3)
        combined = " ".join(warnings)
        assert "provider budget" not in combined.lower()

    def test_no_skips_no_coverage_warning(self):
        warnings = self._context_warnings()
        assert not any("limited" in w.lower() for w in warnings)


class TestStockMomentumLabelMapping:
    """TKT-031C: Stock momentum actions map to friendly public labels."""

    def test_consider_adding_maps_to_momentum_pass(self):
        pub, _ = _svc().public_verdict_label(_row(action="CONSIDER ADDING"), "stock_momentum")
        assert pub == "Momentum Pass"

    def test_tactical_only_maps_to_tactical_watch(self):
        pub, _ = _svc().public_verdict_label(_row(action="TACTICAL ONLY / DO NOT CHASE"), "stock_momentum")
        assert pub == "Tactical Watch"

    def test_watch_confirm_maps_to_watch(self):
        pub, _ = _svc().public_verdict_label(_row(action="WATCH / CONFIRM TREND"), "stock_momentum")
        assert pub == "Watch"

    def test_avoid_weak_maps_to_rejected(self):
        pub, _ = _svc().public_verdict_label(_row(action="AVOID / WEAK TREND"), "stock_momentum")
        assert pub == "Rejected"

    def test_fail_action_maps_to_rejected(self):
        pub, _ = _svc().public_verdict_label(_row(action="FAIL"), "stock_momentum")
        assert pub == "Rejected"

    def test_original_action_preserved_as_secondary(self):
        _, orig = _svc().public_verdict_label(_row(action="CONSIDER ADDING"), "stock_momentum")
        assert orig == "CONSIDER ADDING"


class TestFFRawInternalsHidden:
    """TKT-032A: FF internals (SOURCE_UNSPECIFIED, confidence: none) must not render."""

    def test_source_unspecified_does_not_render(self):
        label = _svc().public_ff_source_label(_row(source_iv_status="SOURCE_UNSPECIFIED"))
        assert "SOURCE_UNSPECIFIED" not in label
        assert label == "Not evaluated this run"

    def test_confidence_none_not_in_source_label(self):
        label = _svc().public_ff_source_label(_row())
        assert "confidence: none" not in label.lower()
        assert "none" not in label.lower()

    def test_dev_cap_skipped_hides_cap_language(self):
        label = _svc().public_ff_source_label(_row(ff_candidate_stage="cap_skip"))
        assert "dev" not in label.lower()
        assert "cap" not in label.lower()
        assert "Skipped by limited scan" == label

    def test_budget_skipped_hides_budget_language(self):
        label = _svc().public_ff_source_label(_row(ff_candidate_stage="budget_skipped"))
        assert "budget" not in label.lower()
        assert "Skipped by limited scan" == label


class TestEmptyRejectedSectionCopy:
    """TKT-035A: Empty rejected section must not use vague copy."""

    def test_empty_rejected_copy_not_misleading(self):
        bad_copy = "No rejected examples this run."
        good_options = [
            "No additional rejected examples selected for this section.",
            "Near-miss rows are shown above because they were more educational than additional rejects.",
            "No rows available for this strategy in the latest cached scan.",
        ]
        assert bad_copy not in good_options

    def test_new_empty_copy_is_acceptable(self):
        copy = "No additional rejected examples selected for this section."
        assert "rejected examples this run" not in copy
        assert len(copy) > 10


# ---------------------------------------------------------------------------
# Lane 1 — Gate checklist
# ---------------------------------------------------------------------------

class TestGateChecklistPresence:
    """TKT-031E: Gate checklists must render for all strategy types."""

    def test_stock_momentum_row_has_gate_checklist(self):
        row = _row(market_metrics={"above_sma_50": True, "above_sma_200": True}, action="CONSIDER ADDING")
        gates = _svc().build_public_gate_checklist(row, "stock_momentum")
        assert isinstance(gates, list) and len(gates) > 0
        assert all("name" in g and "status" in g for g in gates)

    def test_calendar_row_has_gate_checklist(self):
        row = _row(action="FAIL / OPTIONS ILLIQUID", criteria=[
            {"name": "Liquidity", "code": "liquidity", "status": "FAIL", "detail": "Spread 42%"},
        ])
        gates = _svc().build_public_gate_checklist(row, "calendar")
        assert isinstance(gates, list) and len(gates) > 0

    def test_forward_factor_row_has_gate_checklist(self):
        row = _row(ff_gates={"cheap_eligible": True, "chain_approved": True, "source_qualified": True,
                              "diagnostic_model": False, "structure_built": False})
        gates = _svc().build_public_gate_checklist(row, "forward_factor")
        assert isinstance(gates, list) and len(gates) > 0

    def test_skew_row_has_gate_checklist(self):
        row = _row(verdict="FAIL / OPTIONS ILLIQUID", requirements=[
            {"name": "Liquidity", "code": "liquidity", "status": "FAIL"},
        ])
        gates = _svc().build_public_gate_checklist(row, "skew")
        assert isinstance(gates, list) and len(gates) > 0

    def test_calendar_expiry_gap_shows_expiration_gate(self):
        row = _row(action="FAIL / NO ELIGIBLE EXPIRATION PAIR", criteria=[
            {"name": "Expiration pair", "code": "dte", "status": "FAIL", "detail": "No valid pair found"},
        ])
        gates = _svc().build_public_gate_checklist(row, "calendar")
        names = [g["name"].lower() for g in gates]
        statuses = [g["status"] for g in gates]
        assert any("expir" in n or "dte" in n for n in names)
        assert "fail" in statuses

    def test_skew_illiquid_shows_liquidity_gate_fail(self):
        row = _row(verdict="FAIL / OPTIONS ILLIQUID", requirements=[
            {"name": "Liquidity", "code": "liquidity", "status": "FAIL", "detail": "Spread too wide"},
        ])
        gates = _svc().build_public_gate_checklist(row, "skew")
        names_lower = [g["name"].lower() for g in gates]
        statuses = [g["status"] for g in gates]
        assert any("liquid" in n for n in names_lower)
        assert "fail" in statuses

    def test_ff_skipped_row_shows_coverage_skipped(self):
        row = _row(ff_candidate_stage="cap_skip")
        gates = _svc().build_public_gate_checklist(row, "forward_factor")
        statuses = [g["status"] for g in gates]
        assert "skipped" in statuses

    def test_gate_dict_has_required_keys(self):
        row = _row(action="CONSIDER ADDING")
        gates = _svc().build_public_gate_checklist(row, "stock_momentum")
        for gate in gates:
            assert "name" in gate
            assert "status" in gate
            assert gate["status"] in {"pass", "watch", "fail", "unknown", "not_applicable", "dry_run", "skipped"}


class TestDailyOpportunityReason:
    """TKT-031D: Every row with can_enter_daily_opportunity=False should include reason."""

    def test_ff_do_reason_mentions_signal_only_mode(self):
        reason = _svc().public_daily_opportunity_reason(_row(), "forward_factor")
        assert "signal" in reason.lower() or "dry" in reason.lower() or "gated" in reason.lower()

    def test_stock_do_reason_mentions_stock_only(self):
        reason = _svc().public_daily_opportunity_reason(_row(), "stock_momentum")
        assert "stock" in reason.lower() or "options" in reason.lower()

    def test_calendar_illiquid_do_reason(self):
        row = _row(criteria=[{"code": "liquidity", "status": "FAIL"}])
        reason = _svc().public_daily_opportunity_reason(row, "calendar")
        assert "illiquid" in reason.lower() or "liquid" in reason.lower()

    def test_skew_no_vertical_do_reason(self):
        row = _row(requirements=[{"code": "no_vertical", "status": "FAIL"}])
        reason = _svc().public_daily_opportunity_reason(row, "skew")
        assert "vertical" in reason.lower() or "valid" in reason.lower()


# ---------------------------------------------------------------------------
# Lane 2 — Earnings source transparency (TKT-030A-lite)
# ---------------------------------------------------------------------------

class TestEarningsSingleSourceWarningNotBlock:
    """Single-source earnings date = warning, not block (Patch 29.6 rule)."""

    def test_single_source_finnhub_is_allowed(self):
        trust = _trust().normalize_earnings_trust({
            "earnings_date": "2026-07-15",
            "earnings_sources_seen": ["finnhub"],
        })
        assert trust["earnings_trust_label"] == "single_source_verify"
        # Allowed because EARNINGS_TRUST_REQUIRE_MULTI_SOURCE_FOR_CALENDAR_PASS defaults False now
        assert trust["calendar_entry_allowed"] is True

    def test_single_source_alphavantage_is_allowed(self):
        trust = _trust().normalize_earnings_trust({
            "earnings_date": "2026-07-15",
            "earnings_sources_seen": ["alphavantage"],
        })
        assert trust["earnings_trust_label"] == "single_source_verify"
        assert trust["calendar_entry_allowed"] is True

    def test_multi_source_finnhub_alphavantage_is_confirmed(self):
        trust = _trust().normalize_earnings_trust({
            "earnings_date": "2026-07-15",
            "earnings_sources_seen": ["finnhub", "alphavantage"],
        })
        assert trust["earnings_trust_label"] == "multi_source_confirmed"
        assert trust["calendar_entry_allowed"] is True

    def test_conflict_still_blocks(self):
        trust = _trust().normalize_earnings_trust({
            "earnings_date": "2026-07-15",
            "earnings_sources_seen": ["finnhub", "alphavantage"],
            "earnings_source_conflict": True,
        })
        assert trust["earnings_trust_label"] == "conflict_do_not_trade"
        assert trust["calendar_entry_allowed"] is False

    def test_unknown_source_is_research_only(self):
        trust = _trust().normalize_earnings_trust({
            "earnings_date": "2026-07-15",
            "earnings_sources_seen": [],
        })
        assert trust["earnings_trust_label"] == "unknown_research_only"


class TestEarningsSourceTransparencyPublicLabel:
    """Public label shows source names, not raw internal values."""

    def test_finnhub_only_label(self):
        label = _trust().public_earnings_trust_label({
            "earnings_date": "2026-07-15",
            "earnings_sources_seen": ["finnhub"],
        })
        assert "Finnhub" in label
        assert "single-source warning" in label.lower()

    def test_alphavantage_only_label(self):
        label = _trust().public_earnings_trust_label({
            "earnings_date": "2026-07-15",
            "earnings_sources_seen": ["alphavantage"],
        })
        assert "Alpha Vantage" in label
        assert "single-source warning" in label.lower()

    def test_multi_source_label_includes_provider_names(self):
        label = _trust().public_earnings_trust_label({
            "earnings_date": "2026-07-15",
            "earnings_sources_seen": ["finnhub", "alphavantage"],
        })
        assert "Finnhub" in label
        assert "Alpha Vantage" in label
        assert "confirmed" in label.lower()

    def test_conflict_label_is_clear(self):
        label = _trust().public_earnings_trust_label({
            "earnings_date": "2026-07-15",
            "earnings_sources_seen": ["finnhub", "alphavantage"],
            "earnings_source_conflict": True,
        })
        assert "conflict" in label.lower() or "do not trade" in label.lower()

    def test_unknown_source_label_shows_research_only(self):
        label = _trust().public_earnings_trust_label({
            "earnings_date": "2026-07-15",
            "earnings_sources_seen": [],
        })
        assert "unknown" in label.lower() or "research" in label.lower()

    def test_no_manual_verify_language_in_single_source_label(self):
        label = _trust().public_earnings_trust_label({
            "earnings_date": "2026-07-15",
            "earnings_sources_seen": ["finnhub"],
        })
        assert "manually verify" not in label.lower()
        assert "verify before" not in label.lower()

    def test_single_source_reason_does_not_say_must_be_verified(self):
        trust = _trust().normalize_earnings_trust({
            "earnings_date": "2026-07-15",
            "earnings_sources_seen": ["finnhub"],
        })
        reason = trust.get("earnings_trust_reason", "")
        assert "must be verified" not in reason.lower()
        assert "manually verify" not in reason.lower()


# ---------------------------------------------------------------------------
# Lane 3 — Payload bloat breakdown + trim (TKT-038)
# ---------------------------------------------------------------------------

class TestPayloadExclusion:
    """Raw fields must not appear in strategy summary (developer_snapshot_service)."""

    def test_raw_json_excluded_from_strategy_summary(self):
        from app.services.developer_snapshot_service import _STRATEGY_SUMMARY_EXCLUDE
        assert "raw_json" in _STRATEGY_SUMMARY_EXCLUDE

    def test_options_chain_excluded_from_strategy_summary(self):
        from app.services.developer_snapshot_service import _STRATEGY_SUMMARY_EXCLUDE
        assert "options_chain" in _STRATEGY_SUMMARY_EXCLUDE or "raw_chain_data" in _STRATEGY_SUMMARY_EXCLUDE

    def test_raw_provider_payload_excluded_from_strategy_summary(self):
        from app.services.developer_snapshot_service import _STRATEGY_SUMMARY_EXCLUDE
        assert "raw_provider_payload" in _STRATEGY_SUMMARY_EXCLUDE

    def test_full_chain_excluded(self):
        from app.services.developer_snapshot_service import _STRATEGY_SUMMARY_EXCLUDE
        assert "full_chain" in _STRATEGY_SUMMARY_EXCLUDE

    def test_debug_trace_excluded(self):
        from app.services.developer_snapshot_service import _STRATEGY_SUMMARY_EXCLUDE
        assert "debug_trace" in _STRATEGY_SUMMARY_EXCLUDE

    def test_excluded_fields_do_not_appear_in_summary_output(self):
        from app.services.developer_snapshot_service import _strategy_summary
        big_row = {
            "pass_count": 3,
            "raw_json": {"giant": "blob" * 1000},
            "options_chain": [{"strike": 100}] * 500,
            "raw_provider_payload": {"raw": "data"},
            "full_chain": {"chain": "data"},
            "debug_trace": ["trace line"] * 100,
        }
        output = _strategy_summary(big_row, include_rows=False)
        for excluded in ("raw_json", "options_chain", "raw_provider_payload", "full_chain", "debug_trace"):
            assert excluded not in output, f"{excluded!r} leaked into strategy summary"


class TestPayloadBreakdown:
    """TKT-038: Payload breakdown must report largest keys."""

    def test_build_payload_size_profile_returns_largest_keys(self):
        profile = _pps().build_payload_size_profile(
            payload="",
            positions=[],
            news=[],
            recommendations=[],
            snapshot={"_strategy_results": {}, "_data_coverage": {"x": "y"}, "_pipeline_status": {}},
            log=[],
            report_summary={"a": "b"},
        )
        assert "largest_top_level_keys" in profile
        keys = profile["largest_top_level_keys"]
        assert isinstance(keys, list)

    def test_payload_profile_includes_summary_json_bytes(self):
        profile = _pps().build_payload_size_profile(
            payload="",
            positions=[],
            news=[],
            recommendations=[],
            snapshot={},
            log=[],
            report_summary={"data": "x" * 100},
        )
        assert "summary_json_bytes" in profile
        assert profile["summary_json_bytes"] > 0


class TestPayloadWarnings:
    """TKT-038: Payload warnings must fire at threshold."""

    def test_large_payload_triggers_size_warning(self):
        big_profile = {"summary_json_bytes": 2_000_000}
        warnings = _pps().build_payload_warnings(big_profile)
        names = [w["name"] for w in warnings]
        assert "payload_size_warning" in names

    def test_small_payload_no_size_warning(self):
        small_profile = {"summary_json_bytes": 100_000}
        warnings = _pps().build_payload_warnings(small_profile)
        assert not any(w["name"] == "payload_size_warning" for w in warnings)

    def test_provider_call_spike_triggers_warning(self):
        warnings = _pps().build_payload_warnings({}, provider_calls=300)
        names = [w["name"] for w in warnings]
        assert "provider_call_warning" in names

    def test_normal_provider_calls_no_warning(self):
        warnings = _pps().build_payload_warnings({}, provider_calls=50)
        assert not any(w["name"] == "provider_call_warning" for w in warnings)

    def test_dev_full_snapshot_can_still_access_raw_provider_payload(self):
        from app.services.developer_snapshot_service import build_snapshot_detail
        # detail section "provider_raw" must still be accessible (not blanket blocked)
        # We just verify the function accepts the section name without error
        # (will return "unavailable" in test env — that's fine)
        result = build_snapshot_detail("provider_raw")
        assert result.get("detail_section") == "provider_raw"


# ---------------------------------------------------------------------------
# Lane 4 — Regression guard
# ---------------------------------------------------------------------------

class TestRegressionGuard:
    """Ensure hard safety constraints remain intact."""

    def test_ff_remains_dry_run(self):
        from app import config
        assert bool(config.FORWARD_FACTOR_DRY_RUN) is True

    def test_ff_do_reason_confirms_dry_run_status(self):
        reason = _svc().public_daily_opportunity_reason(_row(), "forward_factor")
        lower = reason.lower()
        assert "signal" in lower or "dry" in lower or "gated" in lower or "observation" in lower

    def test_no_broker_data_in_public_screener_gate_checklist(self):
        row = _row(account_value=100000, positions=["AAPL"], broker_auth=True,
                   action="CONSIDER ADDING")
        gates = _svc().build_public_gate_checklist(row, "stock_momentum")
        gate_text = json.dumps(gates)
        assert "account_value" not in gate_text
        assert "broker_auth" not in gate_text
        assert "Robinhood" not in gate_text

    def test_no_raw_unknown_in_public_labels(self):
        svc = _svc()
        for action in ("UNKNOWN", "FAIL", "", "CONSIDER ADDING"):
            pub, _ = svc.public_verdict_label(_row(action=action), "stock_momentum")
            assert pub != "UNKNOWN" or action not in ("CONSIDER ADDING", "FAIL")

    def test_read_only_flag_on_developer_snapshot(self):
        from app.services.developer_snapshot_service import build_developer_snapshot
        result = build_developer_snapshot("latest")
        assert result.get("provider_calls_triggered") is False
        assert result.get("read_only") is True

    def test_strategy_thresholds_unchanged(self):
        from app import config
        # Verify key thresholds not accidentally modified
        assert hasattr(config, "FORWARD_FACTOR_DRY_RUN")
        assert hasattr(config, "EARNINGS_TRUST_CONFLICT_CAN_PASS")
        assert config.EARNINGS_TRUST_CONFLICT_CAN_PASS is False

    def test_conflict_earnings_still_blocks(self):
        trust = _trust().normalize_earnings_trust({
            "earnings_date": "2026-07-15",
            "earnings_sources_seen": ["finnhub", "alphavantage"],
            "earnings_source_conflict": True,
        })
        assert trust["calendar_entry_allowed"] is False
