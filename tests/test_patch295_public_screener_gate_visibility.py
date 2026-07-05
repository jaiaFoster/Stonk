"""ASA Patch 29.5 — Public Screener Credibility + Universal Gate Visibility.

TKT-031A/C/D/E, TKT-032A/B, TKT-035A

CAVEMAN MODE: These tests verify ONLY presentation-layer behaviour.
No strategy scoring, thresholds, gates, execution, or broker behaviour is touched.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ff_row(**kwargs) -> dict:
    """Minimal FF candidate row for testing."""
    base = {
        "ticker": "AAPL",
        "verdict": "PASS / POSSIBLE ENTRY SETUP",
        "ff_candidate_stage": "selected",
        "signal_tier": "SOURCE_QUALIFIED_POSITIVE",
        "source_iv_status": "SOURCE_QUALIFIED",
        "can_enter_daily_opportunity": False,
        "ff_gates": {
            "cheap_eligible": True,
            "chain_approved": True,
            "source_qualified": True,
            "diagnostic_model": True,
            "structure_built": True,
            "earnings_contaminated": False,
        },
    }
    base.update(kwargs)
    return base


def _cal_row(**kwargs) -> dict:
    base = {
        "ticker": "MSFT",
        "action": "PASS / IDEAL ENTRY",
        "entry_timing": "IDEAL",
        "days_until_earnings": 10,
        "can_enter_daily_opportunity": False,
        "criteria": [
            {"code": "liquidity", "name": "Liquidity", "status": "PASS", "detail": ""},
            {"code": "dte", "name": "DTE", "status": "PASS", "detail": ""},
        ],
    }
    base.update(kwargs)
    return base


def _skew_row(**kwargs) -> dict:
    base = {
        "ticker": "NVDA",
        "verdict": "PASS / POSSIBLE ENTRY SETUP",
        "direction": "BULLISH",
        "can_enter_daily_opportunity": False,
        "requirements": [
            {"code": "liquidity", "name": "Liquidity", "status": "PASS", "detail": ""},
            {"code": "skew", "name": "Skew", "status": "PASS", "detail": ""},
        ],
    }
    base.update(kwargs)
    return base


def _stock_row(**kwargs) -> dict:
    base = {
        "ticker": "AMD",
        "action": "CONSIDER ADDING",
        "score": 72.0,
        "can_enter_daily_opportunity": False,
        "market_metrics": {"above_sma_50": True, "above_sma_200": True},
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# TKT-031A: Limited coverage scan labelling (FF skipped rows)
# ---------------------------------------------------------------------------

class TestLimitedCoverageScanLabel:
    def test_cap_skip_stage_returns_limited_scan(self):
        from app.services.public_screener_gate_service import public_verdict_label
        row = _ff_row(ff_candidate_stage="cap_skip", verdict="SKIPPED / DEV CAP")
        label, _ = public_verdict_label(row, "forward_factor")
        assert label == "Skipped by limited scan"

    def test_budget_skipped_stage_returns_limited_scan(self):
        from app.services.public_screener_gate_service import public_verdict_label
        row = _ff_row(ff_candidate_stage="budget_skipped", verdict="SKIPPED / PROVIDER BUDGET")
        label, _ = public_verdict_label(row, "forward_factor")
        assert label == "Skipped by limited scan"

    def test_recent_fail_skip_stage_returns_limited_scan(self):
        from app.services.public_screener_gate_service import public_verdict_label
        row = _ff_row(ff_candidate_stage="recent_fail_skip", verdict="SKIPPED / RECENT REPEAT FAILURE")
        label, _ = public_verdict_label(row, "forward_factor")
        assert label == "Skipped by limited scan"

    def test_dev_cap_verdict_without_stage_returns_limited_scan(self):
        from app.services.public_screener_gate_service import public_verdict_label
        row = _ff_row(ff_candidate_stage="", verdict="SKIPPED / DEV CAP")
        label, _ = public_verdict_label(row, "forward_factor")
        assert label == "Skipped by limited scan"

    def test_pass_verdict_returns_signal_candidate(self):
        from app.services.public_screener_gate_service import public_verdict_label
        row = _ff_row(ff_candidate_stage="selected", verdict="PASS / POSSIBLE ENTRY SETUP")
        label, _ = public_verdict_label(row, "forward_factor")
        assert label == "Signal candidate"

    def test_watch_verdict_returns_near_candidate(self):
        from app.services.public_screener_gate_service import public_verdict_label
        row = _ff_row(ff_candidate_stage="fetched", verdict="WATCH / NEAR POSITIVE FF SIGNAL")
        label, _ = public_verdict_label(row, "forward_factor")
        assert label == "Near candidate"

    def test_fail_verdict_returns_did_not_qualify(self):
        from app.services.public_screener_gate_service import public_verdict_label
        row = _ff_row(ff_candidate_stage="haircut_gate_fail", verdict="FAIL / HAIRCUT GATE")
        label, _ = public_verdict_label(row, "forward_factor")
        assert label == "Did not qualify"

    def test_original_label_preserved(self):
        from app.services.public_screener_gate_service import public_verdict_label
        row = _ff_row(ff_candidate_stage="cap_skip", verdict="SKIPPED / DEV CAP")
        _, original = public_verdict_label(row, "forward_factor")
        assert original == "SKIPPED / DEV CAP"


# ---------------------------------------------------------------------------
# TKT-031C: Stock momentum label mapping
# ---------------------------------------------------------------------------

class TestStockMomentumLabelMapping:
    @pytest.mark.parametrize("action,expected", [
        ("CONSIDER ADDING", "Momentum Pass"),
        ("ADD ON PULLBACK", "Momentum Pass"),
        ("WATCH / CONFIRM TREND", "Watch"),
        ("WATCH / RESEARCH", "Watch"),
        ("STARTER ONLY / WAIT FOR PULLBACK", "Watch"),
        ("TACTICAL ONLY / DO NOT CHASE", "Tactical Watch"),
        ("HOLD / DO NOT ADD", "Tactical Watch"),
        ("AVOID / WEAK TREND", "Rejected"),
    ])
    def test_action_maps_to_public_label(self, action, expected):
        from app.services.public_screener_gate_service import public_verdict_label
        row = _stock_row(action=action)
        label, _ = public_verdict_label(row, "stock_momentum")
        assert label == expected

    def test_unknown_action_fallback(self):
        from app.services.public_screener_gate_service import public_verdict_label
        row = _stock_row(action="SOME NEW ACTION / UNKNOWN")
        label, _ = public_verdict_label(row, "stock_momentum")
        assert isinstance(label, str) and len(label) > 0


# ---------------------------------------------------------------------------
# TKT-031D: Daily Opportunity reason per strategy
# ---------------------------------------------------------------------------

class TestDailyOpportunityReason:
    def test_ff_always_returns_dry_run_message(self):
        from app.services.public_screener_gate_service import public_daily_opportunity_reason
        reason = public_daily_opportunity_reason(_ff_row(), "forward_factor")
        assert "signal-only" in reason.lower() or "dry-run" in reason.lower() or "gated" in reason.lower()

    def test_stock_returns_options_not_applicable(self):
        from app.services.public_screener_gate_service import public_daily_opportunity_reason
        reason = public_daily_opportunity_reason(_stock_row(), "stock_momentum")
        assert "stock-only" in reason.lower() or "options" in reason.lower()

    def test_calendar_liquidity_fail_gives_liquidity_reason(self):
        from app.services.public_screener_gate_service import public_daily_opportunity_reason
        row = _cal_row(action="FAIL / OPTIONS ILLIQUID", criteria=[
            {"code": "liquidity", "name": "Liquidity", "status": "FAIL", "detail": ""},
        ])
        reason = public_daily_opportunity_reason(row, "calendar")
        assert "illiquid" in reason.lower() or "liquid" in reason.lower()

    def test_calendar_dte_fail_gives_timing_reason(self):
        from app.services.public_screener_gate_service import public_daily_opportunity_reason
        row = _cal_row(action="FAIL / DTE", criteria=[
            {"code": "dte", "name": "DTE", "status": "FAIL", "detail": ""},
        ])
        reason = public_daily_opportunity_reason(row, "calendar")
        assert "timing" in reason.lower() or "expir" in reason.lower() or "window" in reason.lower()

    def test_skew_liquidity_fail_gives_liquidity_reason(self):
        from app.services.public_screener_gate_service import public_daily_opportunity_reason
        row = _skew_row(requirements=[
            {"code": "liquidity", "name": "Liquidity", "status": "FAIL", "detail": ""},
        ])
        reason = public_daily_opportunity_reason(row, "skew")
        assert "illiquid" in reason.lower() or "liquid" in reason.lower()

    def test_skew_no_chain_gives_vertical_reason(self):
        from app.services.public_screener_gate_service import public_daily_opportunity_reason
        row = _skew_row(requirements=[
            {"code": "no_chain", "name": "Options chain", "status": "FAIL", "detail": ""},
        ])
        reason = public_daily_opportunity_reason(row, "skew")
        assert "vertical" in reason.lower() or "chain" in reason.lower()


# ---------------------------------------------------------------------------
# TKT-031E: Universal Gate Checklist
# ---------------------------------------------------------------------------

class TestUniversalGateChecklist:
    def test_ff_selected_row_has_all_six_gates(self):
        from app.services.public_screener_gate_service import build_public_gate_checklist
        gates = build_public_gate_checklist(_ff_row(), "forward_factor")
        assert len(gates) == 6

    def test_ff_execution_gate_is_dry_run(self):
        from app.services.public_screener_gate_service import build_public_gate_checklist
        gates = build_public_gate_checklist(_ff_row(), "forward_factor")
        exec_gate = next((g for g in gates if "execution" in g["name"].lower()), None)
        assert exec_gate is not None
        assert exec_gate["status"] == "dry_run"

    def test_ff_cap_skip_gates_are_skipped_or_na(self):
        from app.services.public_screener_gate_service import build_public_gate_checklist
        row = _ff_row(ff_candidate_stage="cap_skip")
        gates = build_public_gate_checklist(row, "forward_factor")
        statuses = {g["status"] for g in gates}
        assert "skipped" in statuses or "not_applicable" in statuses

    def test_ff_failed_gate_reflects_fail_status(self):
        from app.services.public_screener_gate_service import build_public_gate_checklist
        row = _ff_row(ff_gates={
            "cheap_eligible": False,
            "chain_approved": False,
            "source_qualified": False,
            "diagnostic_model": False,
            "structure_built": False,
            "earnings_contaminated": False,
        })
        gates = build_public_gate_checklist(row, "forward_factor")
        cov_gate = gates[0]
        assert cov_gate["status"] == "fail"

    def test_calendar_checklist_reflects_criteria(self):
        from app.services.public_screener_gate_service import build_public_gate_checklist
        row = _cal_row(criteria=[
            {"code": "liquidity", "name": "Liquidity", "status": "PASS", "detail": ""},
            {"code": "dte", "name": "DTE", "status": "FAIL", "detail": "DTE too short"},
        ])
        gates = build_public_gate_checklist(row, "calendar")
        assert len(gates) == 2
        assert gates[0]["status"] == "pass"
        assert gates[1]["status"] == "fail"

    def test_skew_checklist_reflects_requirements(self):
        from app.services.public_screener_gate_service import build_public_gate_checklist
        row = _skew_row(requirements=[
            {"code": "liquidity", "name": "Liquidity", "status": "PASS", "detail": ""},
            {"code": "skew", "name": "Skew", "status": "FAIL", "detail": "Not rich enough"},
        ])
        gates = build_public_gate_checklist(row, "skew")
        assert gates[0]["status"] == "pass"
        assert gates[1]["status"] == "fail"

    def test_stock_checklist_has_three_gates(self):
        from app.services.public_screener_gate_service import build_public_gate_checklist
        gates = build_public_gate_checklist(_stock_row(), "stock_momentum")
        assert len(gates) == 3

    def test_stock_above50_true_gives_pass(self):
        from app.services.public_screener_gate_service import build_public_gate_checklist
        row = _stock_row(market_metrics={"above_sma_50": True, "above_sma_200": True})
        gates = build_public_gate_checklist(row, "stock_momentum")
        assert gates[0]["status"] == "pass"

    def test_stock_above200_false_gives_fail(self):
        from app.services.public_screener_gate_service import build_public_gate_checklist
        row = _stock_row(market_metrics={"above_sma_50": True, "above_sma_200": False})
        gates = build_public_gate_checklist(row, "stock_momentum")
        assert gates[1]["status"] == "fail"

    def test_stock_consider_adding_verdict_is_pass(self):
        from app.services.public_screener_gate_service import build_public_gate_checklist
        row = _stock_row(action="CONSIDER ADDING")
        gates = build_public_gate_checklist(row, "stock_momentum")
        verdict_gate = gates[2]
        assert verdict_gate["status"] == "pass"

    def test_stock_tactical_verdict_is_watch(self):
        from app.services.public_screener_gate_service import build_public_gate_checklist
        row = _stock_row(action="TACTICAL ONLY / DO NOT CHASE")
        gates = build_public_gate_checklist(row, "stock_momentum")
        verdict_gate = gates[2]
        assert verdict_gate["status"] == "watch"

    def test_gate_dict_has_required_keys(self):
        from app.services.public_screener_gate_service import build_public_gate_checklist
        gates = build_public_gate_checklist(_ff_row(), "forward_factor")
        for g in gates:
            assert "name" in g
            assert "status" in g
            assert "detail" in g


# ---------------------------------------------------------------------------
# TKT-032A: FF source label mapping
# ---------------------------------------------------------------------------

class TestFFSourceLabelMapping:
    def test_source_qualified_maps_correctly(self):
        from app.services.public_screener_gate_service import public_ff_source_label
        row = _ff_row(source_iv_status="SOURCE_QUALIFIED")
        assert public_ff_source_label(row) == "Source qualified"

    def test_earnings_contaminated_maps_correctly(self):
        from app.services.public_screener_gate_service import public_ff_source_label
        row = _ff_row(source_iv_status="EARNINGS_CONTAMINATED")
        assert "earnings" in public_ff_source_label(row).lower()

    def test_source_unavailable_maps_correctly(self):
        from app.services.public_screener_gate_service import public_ff_source_label
        row = _ff_row(source_iv_status="SOURCE_UNAVAILABLE")
        assert "unavailable" in public_ff_source_label(row).lower()

    def test_source_unspecified_maps_correctly(self):
        from app.services.public_screener_gate_service import public_ff_source_label
        row = _ff_row(source_iv_status="SOURCE_UNSPECIFIED")
        assert "not evaluated" in public_ff_source_label(row).lower()

    def test_cap_skip_returns_limited_scan_label(self):
        from app.services.public_screener_gate_service import public_ff_source_label
        row = _ff_row(ff_candidate_stage="cap_skip", source_iv_status="SOURCE_UNSPECIFIED",
                      verdict="SKIPPED / DEV CAP")
        result = public_ff_source_label(row)
        assert "limited scan" in result.lower()

    def test_missing_source_iv_falls_back_to_not_evaluated(self):
        from app.services.public_screener_gate_service import public_ff_source_label
        row = _ff_row(source_iv_status="")
        result = public_ff_source_label(row)
        assert "not evaluated" in result.lower()


# ---------------------------------------------------------------------------
# TKT-032B: FF grouping into 3 buckets
# ---------------------------------------------------------------------------

class TestFFGrouping:
    def test_cap_skip_goes_to_skipped(self):
        from app.services.public_screener_gate_service import ff_grouping
        rows = [_ff_row(ff_candidate_stage="cap_skip")]
        groups = ff_grouping(rows)
        assert len(groups["skipped"]) == 1
        assert len(groups["evaluated"]) == 0

    def test_budget_skipped_goes_to_skipped(self):
        from app.services.public_screener_gate_service import ff_grouping
        rows = [_ff_row(ff_candidate_stage="budget_skipped")]
        groups = ff_grouping(rows)
        assert len(groups["skipped"]) == 1

    def test_recent_fail_skip_goes_to_skipped(self):
        from app.services.public_screener_gate_service import ff_grouping
        rows = [_ff_row(ff_candidate_stage="recent_fail_skip")]
        groups = ff_grouping(rows)
        assert len(groups["skipped"]) == 1

    def test_source_qualified_positive_goes_to_evaluated(self):
        from app.services.public_screener_gate_service import ff_grouping
        rows = [_ff_row(signal_tier="SOURCE_QUALIFIED_POSITIVE", ff_candidate_stage="selected")]
        groups = ff_grouping(rows)
        assert len(groups["evaluated"]) == 1
        assert len(groups["skipped"]) == 0

    def test_diagnostic_positive_goes_to_evaluated(self):
        from app.services.public_screener_gate_service import ff_grouping
        rows = [_ff_row(signal_tier="DIAGNOSTIC_POSITIVE", ff_candidate_stage="fetched")]
        groups = ff_grouping(rows)
        assert len(groups["evaluated"]) == 1

    def test_haircut_gate_fail_goes_to_rejected(self):
        from app.services.public_screener_gate_service import ff_grouping
        rows = [_ff_row(ff_candidate_stage="haircut_gate_fail", verdict="FAIL / HAIRCUT GATE",
                        signal_tier="NEGATIVE_OR_BLOCKED")]
        groups = ff_grouping(rows)
        assert len(groups["rejected"]) == 1

    def test_mixed_rows_partition_correctly(self):
        from app.services.public_screener_gate_service import ff_grouping
        rows = [
            _ff_row(ticker="A", ff_candidate_stage="selected", signal_tier="SOURCE_QUALIFIED_POSITIVE",
                    verdict="PASS / POSSIBLE ENTRY SETUP"),
            _ff_row(ticker="B", ff_candidate_stage="cap_skip", verdict="SKIPPED / DEV CAP",
                    signal_tier="NOT_EVALUATED"),
            _ff_row(ticker="C", ff_candidate_stage="haircut_gate_fail", verdict="FAIL / HAIRCUT GATE",
                    signal_tier="NEGATIVE_OR_BLOCKED"),
        ]
        groups = ff_grouping(rows)
        assert len(groups["evaluated"]) == 1
        assert len(groups["skipped"]) == 1
        assert len(groups["rejected"]) == 1

    def test_empty_rows_returns_three_empty_lists(self):
        from app.services.public_screener_gate_service import ff_grouping
        groups = ff_grouping([])
        assert groups == {"evaluated": [], "skipped": [], "rejected": []}


# ---------------------------------------------------------------------------
# TKT-035A: No "rejected examples" placeholder text
# ---------------------------------------------------------------------------

class TestNoRejectedExamplesLanguage:
    def test_rejected_group_empty_message_is_precise(self):
        """The empty-group message must not use the vague 'No rejected examples this run.' phrase."""
        from app.main import _build_screener_ff_html
        from app.services import public_screener_gate_service as svc
        html = _build_screener_ff_html([], svc)
        assert "No rejected examples this run" not in html

    def test_rejected_group_empty_message_is_present(self):
        """When no rejected rows exist, a specific message should be shown."""
        from app.main import _build_screener_ff_html
        from app.services import public_screener_gate_service as svc
        html = _build_screener_ff_html([], svc)
        assert "risk filter" in html.lower() or "rejected" in html.lower()
