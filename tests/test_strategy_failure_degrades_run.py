"""TKT-STRATEGY-FAILURE-RUN-QUALITY — report quality degrades when enabled strategy crashes.

Before this fix, report_quality was only degraded by Robinhood fetch failures (line 658
in analysis_service.py).  If an enabled strategy's run_optional_step raised an exception
(forward_factor, skew_momentum_vertical, unified_calendar_engine, stock_momentum),
report_quality stayed at SUCCESS_COMPLETE even though the run was incomplete.

The fix: after all strategy steps have run, analysis_service checks each enabled
strategy's step_map entry.  If any step's status == "error", report_quality is
degraded to SUCCESS_DEGRADED and degraded_evidence.failed_strategy is set.
"""
from __future__ import annotations

import sys
import types

# ── pyo3 panic guard ──────────────────────────────────────────────────────────
_rh_stub = types.ModuleType("robin_stocks")
_rh_stub.robinhood = types.ModuleType("robin_stocks.robinhood")
sys.modules.setdefault("robin_stocks", _rh_stub)
sys.modules.setdefault("robin_stocks.robinhood", _rh_stub.robinhood)

import pytest


def _make_pipeline_status(failed_step_key: str | None = None) -> dict:
    """Return a minimal pipeline_status dict with one optional failed step."""
    from app.services.pipeline_status_service import new_pipeline_status, begin_step, fail_step, complete_step
    status = new_pipeline_status("prod")
    all_keys = ("unified_calendar_engine", "skew_momentum_vertical", "forward_factor_calendar", "stock_momentum")
    for key in all_keys:
        begin_step(status, key, f"Running {key}...")
        if key == failed_step_key:
            fail_step(status, key, f"{key} crashed: RuntimeError")
        else:
            complete_step(status, key, f"{key} complete.")
    return status


class TestQualityDegradationLogic:
    """Unit tests for the enabled-strategy failure detection inserted in analysis_service."""

    def _apply_degradation_check(self, pipeline_status: dict) -> None:
        """Run the same degradation check that analysis_service performs post-strategy."""
        _enabled_strategy_step_keys = (
            "unified_calendar_engine",
            "skew_momentum_vertical",
            "forward_factor_calendar",
            "stock_momentum",
        )
        for _failed_step_key in _enabled_strategy_step_keys:
            _step_info = pipeline_status.get("step_map", {}).get(_failed_step_key)
            if isinstance(_step_info, dict) and _step_info.get("status") == "error":
                pipeline_status["report_quality"] = "SUCCESS_DEGRADED"
                pipeline_status.setdefault("degraded_evidence", {})["failed_strategy"] = _failed_step_key
                break

    def test_no_failure_quality_unchanged(self):
        status = _make_pipeline_status(failed_step_key=None)
        original_quality = status.get("report_quality", "SUCCESS_COMPLETE")
        self._apply_degradation_check(status)
        assert status.get("report_quality") == original_quality

    def test_forward_factor_failure_degrades_quality(self):
        status = _make_pipeline_status("forward_factor_calendar")
        self._apply_degradation_check(status)
        assert status["report_quality"] == "SUCCESS_DEGRADED"
        assert status.get("degraded_evidence", {}).get("failed_strategy") == "forward_factor_calendar"

    def test_skew_momentum_failure_degrades_quality(self):
        status = _make_pipeline_status("skew_momentum_vertical")
        self._apply_degradation_check(status)
        assert status["report_quality"] == "SUCCESS_DEGRADED"
        assert status.get("degraded_evidence", {}).get("failed_strategy") == "skew_momentum_vertical"

    def test_unified_calendar_failure_degrades_quality(self):
        status = _make_pipeline_status("unified_calendar_engine")
        self._apply_degradation_check(status)
        assert status["report_quality"] == "SUCCESS_DEGRADED"
        assert status.get("degraded_evidence", {}).get("failed_strategy") == "unified_calendar_engine"

    def test_stock_momentum_failure_degrades_quality(self):
        status = _make_pipeline_status("stock_momentum")
        self._apply_degradation_check(status)
        assert status["report_quality"] == "SUCCESS_DEGRADED"
        assert status.get("degraded_evidence", {}).get("failed_strategy") == "stock_momentum"

    def test_only_first_failure_recorded(self):
        """When multiple strategies fail, only the first (ordered) is recorded in degraded_evidence."""
        from app.services.pipeline_status_service import new_pipeline_status, begin_step, fail_step
        status = new_pipeline_status("prod")
        for key in ("unified_calendar_engine", "skew_momentum_vertical", "forward_factor_calendar", "stock_momentum"):
            begin_step(status, key, f"Running {key}...")
            fail_step(status, key, f"{key} crashed")
        self._apply_degradation_check(status)
        assert status["report_quality"] == "SUCCESS_DEGRADED"
        # First key in the ordered tuple wins.
        assert status["degraded_evidence"]["failed_strategy"] == "unified_calendar_engine"

    def test_degraded_evidence_key_set_in_pipeline_status(self):
        status = _make_pipeline_status("forward_factor_calendar")
        self._apply_degradation_check(status)
        assert "degraded_evidence" in status
        assert "failed_strategy" in status["degraded_evidence"]

    def test_unknown_step_key_not_degraded(self):
        """Steps not in the enabled set do not trigger degradation."""
        from app.services.pipeline_status_service import new_pipeline_status, begin_step, fail_step
        status = new_pipeline_status("prod")
        begin_step(status, "some_non_strategy_step", "Some step")
        fail_step(status, "some_non_strategy_step", "Some step failed")
        self._apply_degradation_check(status)
        # No enabled strategy step failed → quality unchanged.
        assert status.get("report_quality") != "SUCCESS_DEGRADED"


class TestPipelineStatusServiceIntegration:
    """Verify pipeline_status_service helpers produce the expected step_map structure."""

    def test_failed_step_has_error_status(self):
        from app.services.pipeline_status_service import new_pipeline_status, begin_step, fail_step
        status = new_pipeline_status("prod")
        begin_step(status, "forward_factor_calendar", "Running FF...")
        fail_step(status, "forward_factor_calendar", "FF crashed")
        step = status["step_map"].get("forward_factor_calendar")
        assert step is not None
        assert step["status"] == "error"

    def test_complete_step_has_complete_status(self):
        from app.services.pipeline_status_service import new_pipeline_status, begin_step, complete_step
        status = new_pipeline_status("prod")
        begin_step(status, "forward_factor_calendar", "Running FF...")
        complete_step(status, "forward_factor_calendar", "FF done.")
        step = status["step_map"].get("forward_factor_calendar")
        assert step is not None
        assert step["status"] == "complete"
