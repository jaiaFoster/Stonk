"""
Tests for Patch 32B — Data Confidence UI Integration, Provider Reconciliation,
and Calendar Discovery Audit.

Coverage:
  - EarningsReconciliationService: build_earnings_reconciliation, log function
  - PipelineProvenanceService: market, options, earnings, position builders
  - CalendarAuditService: build_calendar_audit, log functions
  - DataConfidenceRunReports DB: write/read
  - data_confidence_api.py: batch endpoint
  - data_provenance.py: write_provenance_batch_list, get_field_provenance_batch
  - report_service.py: data_confidence_popover, _calendar_audit_panel_html
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path):
    """Return a temp DB path for isolated tests."""
    return str(tmp_path / "test_provenance.db")


def _make_earnings_discovery(items: list[dict]) -> dict:
    return {
        "items": items,
        "events_by_ticker": {i.get("ticker"): i for i in items},
        "tickers": [i.get("ticker") for i in items],
    }


def _make_quality_result(passed: list, rejected: list) -> dict:
    return {
        "passed_items": passed,
        "rejected_items": rejected,
        "items": passed + rejected,
        "tickers": [i.get("ticker") for i in passed],
        "summary": {
            "raw_event_count": len(passed) + len(rejected),
            "checked_count": len(passed) + len(rejected),
            "passed_count": len(passed),
            "rejected_count": len(rejected),
        },
    }


# ─── 1. EarningsReconciliationService ─────────────────────────────────────────

class TestEarningsReconciliation:
    def test_build_empty(self):
        from app.services.earnings_reconciliation_service import build_earnings_reconciliation
        result = build_earnings_reconciliation({}, ["finnhub", "alphavantage"])
        assert result["total_events"] == 0
        assert result["conflict_count"] == 0
        assert len(result["provider_reports"]) == 2

    def test_builds_from_sources_seen(self):
        from app.services.earnings_reconciliation_service import build_earnings_reconciliation
        disc = _make_earnings_discovery([
            {"ticker": "AAPL", "earnings_date": "2025-01-15", "sources_seen": ["finnhub", "alphavantage"], "is_timestamp_confirmed": True},
            {"ticker": "MSFT", "earnings_date": "2025-01-20", "sources_seen": ["finnhub"], "is_timestamp_confirmed": False},
        ])
        result = build_earnings_reconciliation(disc, ["finnhub", "alphavantage"])
        assert result["total_events"] == 2
        assert result["multi_source_count"] == 1
        # finnhub saw both events
        fh = next(r for r in result["provider_reports"] if r["provider"] == "finnhub")
        assert fh["items"] == 2
        assert fh["status"] == "ok"

    def test_alpha_vantage_gets_no_session_note(self):
        from app.services.earnings_reconciliation_service import build_earnings_reconciliation
        disc = _make_earnings_discovery([
            {"ticker": "AAPL", "earnings_date": "2025-01-15", "sources_seen": ["alphavantage"], "is_timestamp_confirmed": False},
        ])
        result = build_earnings_reconciliation(disc, ["finnhub", "alphavantage"])
        av = next(r for r in result["provider_reports"] if r["provider"] == "alphavantage")
        assert av.get("note") == "session_data_unsupported_by_csv_endpoint"
        assert av["status"] == "ok_no_session"

    def test_conflict_counted(self):
        from app.services.earnings_reconciliation_service import build_earnings_reconciliation
        disc = _make_earnings_discovery([
            {"ticker": "AAPL", "earnings_date": "2025-01-15", "sources_seen": ["finnhub"], "earnings_source_conflict": True},
        ])
        result = build_earnings_reconciliation(disc, ["finnhub"])
        assert result["conflict_count"] == 1

    def test_log_emits_line(self):
        from app.services.earnings_reconciliation_service import build_earnings_reconciliation, log_earnings_provider_reconciliation
        disc = _make_earnings_discovery([
            {"ticker": "AAPL", "earnings_date": "2025-01-15", "sources_seen": ["finnhub"]},
        ])
        recon = build_earnings_reconciliation(disc, ["finnhub", "alphavantage"])
        lines = []
        log_earnings_provider_reconciliation(recon, log_print=lines.append)
        assert len(lines) == 1
        assert lines[0].startswith("EARNINGS_PROVIDER_RECONCILIATION")
        assert "provider=finnhub" in lines[0]
        assert "conflicts=" in lines[0]

    def test_run_earnings_reconciliation_returns_report(self):
        from app.services.earnings_reconciliation_service import run_earnings_reconciliation
        disc = _make_earnings_discovery([])
        lines = []
        result = run_earnings_reconciliation(disc, ["finnhub"], log_print=lines.append)
        assert "provider_reports" in result
        assert len(lines) == 1

    def test_log_safe_on_empty_reconciliation(self):
        from app.services.earnings_reconciliation_service import log_earnings_provider_reconciliation
        lines = []
        log_earnings_provider_reconciliation({}, log_print=lines.append)
        assert len(lines) == 1

    def test_provider_error_tracked(self):
        from app.services.earnings_reconciliation_service import build_earnings_reconciliation
        disc = {
            "items": [
                {
                    "ticker": "AAPL",
                    "earnings_date": "2025-01-15",
                    "sources_seen": ["finnhub"],
                    "provider_errors": ["alphavantage: rate_limit: HTTP 429"],
                }
            ]
        }
        result = build_earnings_reconciliation(disc, ["finnhub", "alphavantage"])
        av = next(r for r in result["provider_reports"] if r["provider"] == "alphavantage")
        assert av["status"] == "rate_limit"
        assert "error" in av


# ─── 2. PipelineProvenanceService ─────────────────────────────────────────────

class TestPipelineProvenance:
    def test_build_market_provenance_with_data(self):
        from app.services.pipeline_provenance_service import build_market_provenance
        quote = {"last": 150.0, "bid": 149.5, "ask": 150.5}
        result = build_market_provenance("AAPL", quote)
        assert "market.last_price" in result
        assert result["market.last_price"].selected_value == 150.0
        assert result["market.last_price"].confidence_level == "LOW"
        assert "market.bid" in result
        assert "market.ask" in result
        assert "market.mid" in result
        mid = result["market.mid"].selected_value
        assert abs(mid - 150.0) < 0.01

    def test_build_market_provenance_missing(self):
        from app.services.pipeline_provenance_service import build_market_provenance
        result = build_market_provenance("AAPL", {})
        assert result["market.last_price"].confidence_level == "UNKNOWN"
        assert result["market.mid"].confidence_level == "UNKNOWN"

    def test_build_market_provenance_mid_calculated(self):
        from app.services.pipeline_provenance_service import build_market_provenance
        result = build_market_provenance("AAPL", {"bid": 10.0, "ask": 12.0})
        mid = result["market.mid"]
        assert mid.is_calculated is True
        assert mid.selected_value == 11.0

    def test_build_options_leg_provenance_full(self):
        from app.services.pipeline_provenance_service import build_options_leg_provenance
        leg = {
            "bid": 1.50, "ask": 1.70, "last": 1.60,
            "volume": 500, "open_interest": 2000,
            "implied_volatility": 0.45,
            "delta": -0.35, "gamma": 0.08, "theta": -0.02, "vega": 0.15, "rho": -0.03,
        }
        result = build_options_leg_provenance("AAPL", "2025-01-17", "put", 150.0, leg)
        assert "options.bid" in result
        assert result["options.bid"].selected_value == 1.50
        assert "options.iv" in result
        assert result["options.iv"].is_calculated is True
        assert result["options.iv"].calculation_method == "EXACT_MODEL"
        assert "options.mid" in result
        assert abs(result["options.mid"].selected_value - 1.60) < 0.01

    def test_build_options_leg_provenance_empty(self):
        from app.services.pipeline_provenance_service import build_options_leg_provenance
        result = build_options_leg_provenance("AAPL", "2025-01-17", "call", 150.0, {})
        assert result["options.bid"].confidence_level == "UNKNOWN"
        assert result["options.iv"].confidence_level == "UNKNOWN"

    def test_build_position_provenance(self):
        from app.services.pipeline_provenance_service import build_position_provenance
        pos = {"ticker": "AAPL", "current_price": 155.0, "quantity": 10, "average_buy_price": 140.0, "market_value": 1550.0}
        result = build_position_provenance("AAPL", pos)
        assert "position.current_price" in result
        assert result["position.current_price"].selected_value == 155.0
        assert result["position.current_price"].selected_provider == "robinhood"
        assert result["position.quantity"].selected_value == 10

    def test_wire_pipeline_provenance_noop_on_empty(self):
        from app.services.pipeline_provenance_service import wire_pipeline_provenance
        result = wire_pipeline_provenance(
            run_id="test-run",
            strategy_id="test",
            tradier_snapshot={},
            earnings_events={},
            positions=[],
            configured_providers=["finnhub"],
            db_enabled=False,
        )
        assert result["records_built"] == 0
        assert result["errors"] == 0

    def test_wire_pipeline_provenance_with_data(self):
        from app.services.pipeline_provenance_service import wire_pipeline_provenance
        snap = {
            "AAPL": {
                "quote": {"last": 150.0, "bid": 149.5, "ask": 150.5},
            }
        }
        result = wire_pipeline_provenance(
            run_id="test-run",
            strategy_id="test",
            tradier_snapshot=snap,
            earnings_events={},
            positions=[],
            configured_providers=["finnhub"],
            db_enabled=False,
        )
        assert result["records_built"] >= 5  # last, bid, ask, mid, quote_timestamp


# ─── 3. CalendarAuditService ──────────────────────────────────────────────────

class TestCalendarAudit:
    def test_build_calendar_audit_empty(self):
        from app.services.calendar_audit_service import build_calendar_audit
        result = build_calendar_audit(None, None, None, None)
        funnel = result["funnel"]
        assert funnel["raw_events"] == 0
        assert funnel["quality_passed"] == 0
        assert funnel["ranked"] == 0

    def test_build_calendar_audit_with_data(self):
        from app.services.calendar_audit_service import build_calendar_audit
        disc = _make_earnings_discovery([
            {"ticker": "AAPL", "earnings_date": "2025-01-15"},
            {"ticker": "MSFT", "earnings_date": "2025-01-20"},
            {"ticker": "NVDA", "earnings_date": "2025-01-22"},
        ])
        qual = _make_quality_result(
            passed=[{"ticker": "AAPL", "score": 75, "entry_window_status": "ENTRY_WINDOW_OPEN"}],
            rejected=[{"ticker": "MSFT", "rejection_reason": "liquidity_fail"}, {"ticker": "NVDA", "rejection_reason": "no_expirations"}],
        )
        candidates = [{"ticker": "AAPL", "score": 72, "scanner_verdict": "WATCH"}]
        ranking = {"items": [{"ticker": "AAPL", "final_verdict": "WATCH", "ranking_score": 72}]}
        result = build_calendar_audit(disc, qual, candidates, ranking)
        funnel = result["funnel"]
        assert funnel["raw_events"] == 3
        assert funnel["quality_passed"] == 1
        assert funnel["scanner_candidates"] == 1
        assert funnel["ranked"] == 1
        ticker_audit = result["ticker_audit"]
        assert "AAPL" in ticker_audit
        assert ticker_audit["AAPL"]["exit_stage"] == "ranked"
        assert "MSFT" in ticker_audit
        assert ticker_audit["MSFT"]["exit_stage"] == "quality"
        assert ticker_audit["MSFT"]["exit_reason"] == "liquidity_fail"

    def test_log_calendar_discovery_audit(self):
        from app.services.calendar_audit_service import build_calendar_audit, log_calendar_discovery_audit
        disc = _make_earnings_discovery([{"ticker": "AAPL", "earnings_date": "2025-01-15"}])
        qual = _make_quality_result([{"ticker": "AAPL"}], [])
        audit = build_calendar_audit(disc, qual, [], {})
        lines = []
        line = log_calendar_discovery_audit(audit, log_print=lines.append)
        assert line.startswith("CALENDAR_DISCOVERY_AUDIT")
        assert "raw_events=1" in line
        assert "quality_passed=1" in line

    def test_log_calendar_ticker_audit(self):
        from app.services.calendar_audit_service import build_calendar_audit, log_calendar_ticker_audit
        disc = _make_earnings_discovery([{"ticker": "AAPL", "earnings_date": "2025-01-15"}])
        qual = _make_quality_result([{"ticker": "AAPL", "score": 75}], [])
        candidates = [{"ticker": "AAPL", "scanner_verdict": "WATCH"}]
        audit = build_calendar_audit(disc, qual, candidates, {})
        lines = []
        result_lines = log_calendar_ticker_audit(audit, log_print=lines.append)
        assert len(lines) >= 1
        assert any("CALENDAR_TICKER_AUDIT" in l for l in lines)
        assert any("ticker=AAPL" in l for l in lines)

    def test_run_calendar_audit_returns_dict(self):
        from app.services.calendar_audit_service import run_calendar_audit
        disc = _make_earnings_discovery([{"ticker": "AAPL", "earnings_date": "2025-01-15"}])
        lines = []
        result = run_calendar_audit(disc, None, [], {}, run_mode="dev", log_print=lines.append)
        assert "funnel" in result
        assert any("CALENDAR_DISCOVERY_AUDIT" in l for l in lines)

    def test_run_calendar_audit_safe_on_bad_input(self):
        from app.services.calendar_audit_service import run_calendar_audit
        result = run_calendar_audit(None, None, None, None, log_print=lambda x: None)
        assert isinstance(result, dict)


# ─── 4. DataConfidenceRunReports DB ───────────────────────────────────────────

class TestDataConfidenceRunReports:
    def test_write_and_read_run_report(self, tmp_path):
        from app.db.data_confidence_run_reports import write_run_report, get_run_report, _db_path
        suite = {
            "total_reports": 5,
            "passed_reports": 4,
            "failed_reports": 1,
            "total_errors": 1,
            "total_warnings": 2,
            "validation_passed": False,
        }
        db_file = str(tmp_path / "test_run_reports.db")
        import app.db.data_confidence_run_reports as _mod
        original_db = _mod._db_path

        def mock_db_path():
            return db_file
        _mod._db_path = mock_db_path
        try:
            ok = write_run_report("run-001", "pipeline", suite)
            assert ok is True
            result = get_run_report("run-001")
            assert result is not None
            assert result["run_id"] == "run-001"
            assert result["total_errors"] == 1
            assert result["validation_passed"] is False
            assert "report" in result
        finally:
            _mod._db_path = original_db

    def test_get_nonexistent_run_report(self, tmp_path):
        from app.db.data_confidence_run_reports import get_run_report, _db_path
        import app.db.data_confidence_run_reports as _mod
        db_file = str(tmp_path / "test_run_reports.db")
        orig = _mod._db_path
        _mod._db_path = lambda: db_file
        try:
            result = get_run_report("does-not-exist")
            assert result is None
        finally:
            _mod._db_path = orig

    def test_write_replaces_duplicate(self, tmp_path):
        from app.db.data_confidence_run_reports import write_run_report, get_run_report
        import app.db.data_confidence_run_reports as _mod
        db_file = str(tmp_path / "test_run_reports.db")
        orig = _mod._db_path
        _mod._db_path = lambda: db_file
        try:
            write_run_report("run-dup", "pipeline", {"total_errors": 5, "total_reports": 1, "passed_reports": 0, "failed_reports": 1, "total_warnings": 0, "validation_passed": False})
            write_run_report("run-dup", "pipeline", {"total_errors": 0, "total_reports": 2, "passed_reports": 2, "failed_reports": 0, "total_warnings": 0, "validation_passed": True})
            result = get_run_report("run-dup")
            assert result["total_errors"] == 0
            assert result["validation_passed"] is True
        finally:
            _mod._db_path = orig


# ─── 5. data_provenance batch functions ───────────────────────────────────────

class TestDataProvenanceBatch:
    def test_write_provenance_batch_list(self, tmp_path):
        import app.db.data_provenance as dp
        db_file = str(tmp_path / "prov.db")
        orig = dp._db_path
        dp._db_path = lambda: db_file
        try:
            rows = [
                {
                    "run_id": "run-1",
                    "strategy_id": "test",
                    "row_id": "AAPL:market",
                    "ticker": "AAPL",
                    "field_id": "market.last_price",
                    "selected_value": "150.0",
                    "selected_provider": "tradier",
                    "confidence_level": "LOW",
                    "provenance_json": json.dumps({"field_id": "market.last_price", "confidence_level": "LOW"}),
                },
                {
                    "run_id": "run-1",
                    "strategy_id": "test",
                    "row_id": "AAPL:market",
                    "ticker": "AAPL",
                    "field_id": "market.bid",
                    "selected_value": "149.5",
                    "selected_provider": "tradier",
                    "confidence_level": "LOW",
                    "provenance_json": None,
                },
            ]
            count = dp.write_provenance_batch_list(rows)
            assert count == 2
            fetched = dp.get_field_provenance(run_id="run-1", field_id="market.last_price", db_path=db_file)
            assert len(fetched) == 1
            assert fetched[0]["selected_value"] == "150.0"
        finally:
            dp._db_path = orig

    def test_get_field_provenance_batch_with_cursor(self, tmp_path):
        import app.db.data_provenance as dp
        db_file = str(tmp_path / "prov2.db")
        orig = dp._db_path
        dp._db_path = lambda: db_file
        try:
            rows = [
                {
                    "run_id": "run-2",
                    "strategy_id": "test",
                    "row_id": f"TICKER{i}:market",
                    "ticker": f"T{i}",
                    "field_id": "market.last_price",
                    "selected_value": str(i * 10.0),
                    "selected_provider": "tradier",
                    "confidence_level": "LOW",
                    "provenance_json": None,
                }
                for i in range(10)
            ]
            dp.write_provenance_batch_list(rows)
            page1 = dp.get_field_provenance_batch(run_id="run-2", limit=5, db_path=db_file)
            assert len(page1) <= 5
            if len(page1) == 5:
                last_id = page1[-1]["id"]
                page2 = dp.get_field_provenance_batch(run_id="run-2", limit=5, cursor=last_id, db_path=db_file)
                assert len(page2) <= 5
                # Ensure no overlap
                page1_ids = {r["id"] for r in page1}
                page2_ids = {r["id"] for r in page2}
                assert page1_ids.isdisjoint(page2_ids)
        finally:
            dp._db_path = orig

    def test_write_batch_list_empty(self):
        import app.db.data_provenance as dp
        result = dp.write_provenance_batch_list([])
        assert result == 0

    def test_get_batch_field_ids_filter(self, tmp_path):
        import app.db.data_provenance as dp
        db_file = str(tmp_path / "prov3.db")
        orig = dp._db_path
        dp._db_path = lambda: db_file
        try:
            rows = [
                {"run_id": "r", "strategy_id": "s", "row_id": "row1", "ticker": "A", "field_id": "market.last_price", "selected_value": "1", "selected_provider": "tradier", "confidence_level": "LOW", "provenance_json": None},
                {"run_id": "r", "strategy_id": "s", "row_id": "row2", "ticker": "A", "field_id": "market.bid", "selected_value": "0.9", "selected_provider": "tradier", "confidence_level": "LOW", "provenance_json": None},
            ]
            dp.write_provenance_batch_list(rows)
            result = dp.get_field_provenance_batch(run_id="r", field_ids=["market.bid"], db_path=db_file)
            assert len(result) == 1
            assert result[0]["field_id"] == "market.bid"
        finally:
            dp._db_path = orig


# ─── 6. Batch API endpoint ────────────────────────────────────────────────────

class TestBatchAPIEndpoint:
    def test_get_batch_returns_200(self, tmp_path):
        import app.db.data_provenance as dp
        db_file = str(tmp_path / "batch_prov.db")
        orig = dp._db_path
        dp._db_path = lambda: db_file

        rows = [
            {"run_id": "rA", "strategy_id": "s", "row_id": "AAPL:market", "ticker": "AAPL", "field_id": "market.last_price", "selected_value": "100", "selected_provider": "tradier", "confidence_level": "LOW", "provenance_json": None}
        ]
        dp.write_provenance_batch_list(rows)

        try:
            from app.api.data_confidence_api import get_batch_field_provenance_response
            result, status = get_batch_field_provenance_response("rA", None, None, 50, None)
            assert status == 200
            assert "items" in result
            assert result["count"] >= 1
            assert result["provider_calls_triggered"] is False
            assert result["read_only"] is True
        finally:
            dp._db_path = orig

    def test_get_batch_limit_clamped(self):
        from app.api.data_confidence_api import get_batch_field_provenance_response
        result, status = get_batch_field_provenance_response(None, None, None, 999, None)
        assert status == 200
        assert result["limit"] == 100  # clamped to max

    def test_get_batch_empty_returns_200(self):
        from app.api.data_confidence_api import get_batch_field_provenance_response
        result, status = get_batch_field_provenance_response("nonexistent-run", None, None, 10, None)
        assert status == 200
        assert result["count"] == 0
        assert result["has_next_page"] is False

    def test_get_batch_pagination(self, tmp_path):
        import app.db.data_provenance as dp
        db_file = str(tmp_path / "batch_prov2.db")
        orig = dp._db_path
        dp._db_path = lambda: db_file

        rows = [
            {"run_id": "rB", "strategy_id": "s", "row_id": f"r{i}", "ticker": f"T{i}", "field_id": "market.last_price", "selected_value": str(i), "selected_provider": "tradier", "confidence_level": "LOW", "provenance_json": None}
            for i in range(12)
        ]
        dp.write_provenance_batch_list(rows)

        try:
            from app.api.data_confidence_api import get_batch_field_provenance_response
            page1, _ = get_batch_field_provenance_response("rB", None, None, 5, None)
            assert page1["count"] == 5
            assert page1["has_next_page"] is True
            cursor = page1["next_cursor"]
            page2, _ = get_batch_field_provenance_response("rB", None, None, 5, cursor)
            page1_field_ids = {i["id"] for i in page1["items"]}
            page2_field_ids = {i["id"] for i in page2["items"]}
            assert page1_field_ids.isdisjoint(page2_field_ids)
        finally:
            dp._db_path = orig


# ─── 7. report_service: DataConfidencePopover ─────────────────────────────────

class TestDataConfidencePopover:
    def test_popover_high_confidence(self):
        from app.services.report_service import data_confidence_popover
        html = data_confidence_popover("2025-01-15", "HIGH", "earnings.date", "finnhub")
        assert "2025-01-15" in html
        assert "HIGH" in html
        assert "#83b88f" in html  # green color
        assert "earnings.date" in html

    def test_popover_conflict(self):
        from app.services.report_service import data_confidence_popover
        html = data_confidence_popover("2025-01-15", "CONFLICT", has_conflict=True)
        assert "CONFLICT" in html
        assert "conflict" in html.lower()

    def test_popover_unknown_fallback(self):
        from app.services.report_service import data_confidence_popover
        html = data_confidence_popover(None, "UNKNOWN", fallback="No date")
        assert "No date" in html

    def test_popover_missing_value(self):
        from app.services.report_service import data_confidence_popover
        html = data_confidence_popover("", "LOW", fallback="N/A")
        assert "N/A" in html

    def test_popover_xss_safe(self):
        from app.services.report_service import data_confidence_popover
        html = data_confidence_popover("<script>alert(1)</script>", "HIGH")
        assert "<script>" not in html
        assert "&lt;script&gt;" in html or "alert" not in html

    def test_popover_observed_at_shown(self):
        from app.services.report_service import data_confidence_popover
        html = data_confidence_popover("value", "LOW", observed_at="2025-01-15T10:00:00Z")
        assert "2025-01-15" in html

    def test_popover_returns_string(self):
        from app.services.report_service import data_confidence_popover
        result = data_confidence_popover("test", "MEDIUM")
        assert isinstance(result, str)
        assert len(result) > 0


# ─── 8. Calendar audit panel HTML ─────────────────────────────────────────────

class TestCalendarAuditPanelHtml:
    def test_empty_returns_empty_string(self):
        from app.services.report_service import _calendar_audit_panel_html
        assert _calendar_audit_panel_html({}) == ""
        assert _calendar_audit_panel_html(None) == ""

    def test_renders_funnel(self):
        from app.services.report_service import _calendar_audit_panel_html
        audit = {
            "funnel": {
                "raw_events": 5,
                "constituent_checked": 5,
                "quality_passed": 2,
                "scanner_candidates": 1,
                "strategy_evaluated": 1,
                "ranked": 1,
            },
            "ticker_audit": {
                "AAPL": {"stages": {"raw_events": True, "quality_passed": True, "scanner": True, "ranked": True}, "exit_stage": "ranked", "final_verdict": "WATCH"},
            },
            "run_mode": "dev",
        }
        html = _calendar_audit_panel_html(audit)
        assert "5" in html  # raw events count
        assert "AAPL" in html
        assert "ranked" in html

    def test_xss_safe(self):
        from app.services.report_service import _calendar_audit_panel_html
        audit = {
            "funnel": {"raw_events": 1, "quality_passed": 0, "scanner_candidates": 0, "strategy_evaluated": 0, "ranked": 0, "constituent_checked": 1},
            "ticker_audit": {"<script>": {"stages": {}, "exit_stage": None}},
            "run_mode": "prod",
        }
        html = _calendar_audit_panel_html(audit)
        assert "<script>" not in html


# ─── 9. Demo fixtures ─────────────────────────────────────────────────────────

class TestDemoFixtures:
    """14 deterministic demo fixture types for UI development."""

    def _make_field_prov(self, confidence: str, provider: str = "finnhub", value: Any = "2025-01-15") -> dict:
        from app.models.patch32a_provenance import FieldProvenanceRecord, ProviderValueRecord
        pv = ProviderValueRecord.available(provider, value, is_selected=True)
        return FieldProvenanceRecord(
            field_id="earnings.date",
            selected_value=value,
            selected_provider=provider,
            confidence_level=confidence,
            provider_values=[pv],
        )

    def test_fixture_high_agreement(self):
        from app.models.patch32a_provenance import CONFIDENCE_HIGH
        rec = self._make_field_prov(CONFIDENCE_HIGH)
        assert rec.confidence_level == "HIGH"
        assert rec.confidence_color == "green"

    def test_fixture_medium_date_agrees_session_differs(self):
        from app.models.patch32a_provenance import CONFIDENCE_MEDIUM, FieldProvenanceRecord, ProviderValueRecord
        rec = FieldProvenanceRecord(
            field_id="earnings.date",
            selected_value="2025-01-15",
            selected_provider="finnhub",
            confidence_level=CONFIDENCE_MEDIUM,
            confidence_reason="session_differs",
        )
        assert rec.confidence_color == "yellow-green"

    def test_fixture_low_single_source(self):
        from app.models.patch32a_provenance import CONFIDENCE_LOW
        rec = self._make_field_prov(CONFIDENCE_LOW, "alphavantage")
        assert rec.confidence_color == "orange"

    def test_fixture_conflict(self):
        from app.models.patch32a_provenance import CONFIDENCE_CONFLICT, FieldProvenanceRecord, ProviderValueRecord
        pv1 = ProviderValueRecord.available("finnhub", "2025-01-15", is_selected=True)
        pv2 = ProviderValueRecord.available("alphavantage", "2025-01-17", is_selected=False)
        rec = FieldProvenanceRecord(
            field_id="earnings.date",
            selected_value="2025-01-15",
            selected_provider="finnhub",
            confidence_level=CONFIDENCE_CONFLICT,
            provider_values=[pv1, pv2],
            conflicts=[{"providers": ["finnhub", "alphavantage"], "values": ["2025-01-15", "2025-01-17"]}],
        )
        assert rec.has_conflict is True
        assert rec.confidence_color == "red"

    def test_fixture_unknown_no_data(self):
        from app.models.patch32a_provenance import CONFIDENCE_UNKNOWN, FieldProvenanceRecord
        rec = FieldProvenanceRecord(field_id="earnings.date", confidence_level=CONFIDENCE_UNKNOWN)
        assert rec.confidence_color == "gray"
        assert rec.provider_count == 0

    def test_fixture_calculated_iv(self):
        from app.models.patch32a_provenance import CONFIDENCE_LOW, SOURCE_TYPE_CALCULATED, FieldProvenanceRecord
        rec = FieldProvenanceRecord(
            field_id="options.iv",
            selected_value=0.45,
            selected_provider="tradier",
            selected_source_type=SOURCE_TYPE_CALCULATED,
            confidence_level=CONFIDENCE_LOW,
            is_calculated=True,
            calculation_method="EXACT_MODEL",
        )
        assert rec.is_calculated is True
        assert rec.calculation_method == "EXACT_MODEL"

    def test_fixture_approximated_greek(self):
        from app.models.patch32a_provenance import CONFIDENCE_LOW, SOURCE_TYPE_APPROXIMATED, FieldProvenanceRecord
        rec = FieldProvenanceRecord(
            field_id="options.delta",
            selected_value=-0.45,
            selected_source_type=SOURCE_TYPE_APPROXIMATED,
            confidence_level=CONFIDENCE_LOW,
            is_calculated=True,
            is_approximation=True,
            calculation_method="APPROXIMATION",
        )
        compact = rec.compact()
        assert compact.get("is_approximation") is True

    def test_fixture_stale_data(self):
        from app.models.patch32a_provenance import STATUS_STALE, ProviderValueRecord
        pv = ProviderValueRecord(provider="finnhub", value="2025-01-10", status=STATUS_STALE)
        assert pv.status == "STALE"

    def test_fixture_missing_provider(self):
        from app.models.patch32a_provenance import STATUS_MISSING, ProviderValueRecord
        pv = ProviderValueRecord.missing("alphavantage")
        assert pv.status == "MISSING"
        assert pv.value is None

    def test_fixture_error_provider(self):
        from app.models.patch32a_provenance import STATUS_ERROR, ProviderValueRecord
        pv = ProviderValueRecord.error("finnhub", "RATE_LIMIT", "HTTP 429")
        assert pv.status == "ERROR"
        assert pv.error_code == "RATE_LIMIT"

    def test_fixture_not_requested(self):
        from app.models.patch32a_provenance import STATUS_NOT_REQUESTED, ProviderValueRecord
        pv = ProviderValueRecord.not_requested("robinhood")
        assert pv.status == "NOT_REQUESTED"

    def test_fixture_unsupported_field(self):
        from app.models.patch32a_provenance import STATUS_UNSUPPORTED, ProviderValueRecord
        pv = ProviderValueRecord.unsupported("alphavantage")
        assert pv.status == "UNSUPPORTED"

    def test_fixture_robinhood_position_preferred(self):
        from app.services.pipeline_provenance_service import build_position_provenance
        pos = {"current_price": 200.0, "quantity": 5}
        result = build_position_provenance("HOOD", pos)
        assert result["position.current_price"].selected_provider == "robinhood"

    def test_fixture_compact_representation(self):
        from app.models.patch32a_provenance import CONFIDENCE_HIGH, FieldProvenanceRecord, ProviderValueRecord
        pv = ProviderValueRecord.available("finnhub", "2025-01-15", is_selected=True)
        rec = FieldProvenanceRecord(
            field_id="earnings.date",
            selected_value="2025-01-15",
            selected_provider="finnhub",
            confidence_level=CONFIDENCE_HIGH,
            provider_values=[pv],
        )
        compact = rec.compact()
        assert compact["field_id"] == "earnings.date"
        assert compact["confidence_level"] == "HIGH"
        assert compact["confidence_color"] == "green"
        assert compact["provider_count"] == 1
        assert "schema_version" in compact


# ─── 10. Integration: pipeline wiring survives missing data ───────────────────

class TestPipelineWiringRobustness:
    def test_wire_pipeline_skips_private_keys(self):
        from app.services.pipeline_provenance_service import wire_pipeline_provenance
        snap = {
            "_pipeline_status": {"run_id": "x"},
            "_earnings_events": {},
            "AAPL": {"quote": {"last": 100.0}},
        }
        result = wire_pipeline_provenance("r", "s", snap, {}, [], ["finnhub"], db_enabled=False)
        assert result["errors"] == 0
        assert result["records_built"] > 0

    def test_reconciliation_survives_malformed_events(self):
        from app.services.earnings_reconciliation_service import build_earnings_reconciliation
        disc = {"items": [None, {}, {"ticker": ""}, {"ticker": "AAPL"}]}
        result = build_earnings_reconciliation(disc, ["finnhub"])
        assert isinstance(result, dict)

    def test_calendar_audit_survives_none_inputs(self):
        from app.services.calendar_audit_service import run_calendar_audit
        result = run_calendar_audit(None, None, None, None, log_print=lambda _: None)
        assert isinstance(result, dict)
