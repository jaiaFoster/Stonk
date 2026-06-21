"""
tests/test_skew_threshold_analysis.py — Skew threshold analysis service tests.

Verifies the read-only aggregation logic without touching any provider.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


class TestWhatIfAnalysis:
    def test_reductions(self):
        from app.services.skew_threshold_analysis_service import _what_if_analysis
        candidates = [
            {"ticker": "AAPL", "adjusted_skew_score": 10.0},
            {"ticker": "MSFT", "adjusted_skew_score": 11.5},
            {"ticker": "GOOG", "adjusted_skew_score": 8.0},
        ]
        result = _what_if_analysis(candidates, 12.5)
        assert len(result) > 0
        first = result[0]
        assert first["threshold"] == 11.5
        assert first["reduction"] == 1.0
        assert first["additional_passes"] == 1
        assert "MSFT" in first["tickers"]

    def test_empty_candidates(self):
        from app.services.skew_threshold_analysis_service import _what_if_analysis
        result = _what_if_analysis([], 12.5)
        assert all(r["additional_passes"] == 0 for r in result)


class TestTickerHistory:
    def test_groups_by_ticker(self):
        from app.services.skew_threshold_analysis_service import _build_ticker_history
        candidates = [
            {"ticker": "AAPL", "verdict": "WATCH: close", "adjusted_skew_score": 10.0, "skew_gap_to_pass": 2.5},
            {"ticker": "AAPL", "verdict": "WATCH: close", "adjusted_skew_score": 11.0, "skew_gap_to_pass": 1.5},
            {"ticker": "MSFT", "verdict": "FAIL: skew", "adjusted_skew_score": 5.0, "skew_gap_to_pass": 7.5},
        ]
        result = _build_ticker_history(candidates)
        by_ticker = {r["ticker"]: r for r in result}
        assert by_ticker["AAPL"]["appearances"] == 2
        assert by_ticker["AAPL"]["watch_count"] == 2
        assert by_ticker["AAPL"]["best_adjusted_skew_score"] == 11.0
        assert by_ticker["MSFT"]["fail_count"] == 1

    def test_sorted_by_closest(self):
        from app.services.skew_threshold_analysis_service import _build_ticker_history
        candidates = [
            {"ticker": "FAR", "verdict": "WATCH", "adjusted_skew_score": 5.0, "skew_gap_to_pass": 7.5},
            {"ticker": "CLOSE", "verdict": "WATCH", "adjusted_skew_score": 12.0, "skew_gap_to_pass": 0.5},
        ]
        result = _build_ticker_history(candidates)
        assert result[0]["ticker"] == "CLOSE"


class TestExtractSkewRows:
    def test_returns_none_for_empty(self):
        from app.services.skew_threshold_analysis_service import _extract_skew_rows
        assert _extract_skew_rows({}) is None

    def test_extracts_from_strategy_results(self):
        import json, zlib
        from app.services.skew_threshold_analysis_service import _extract_skew_rows
        tradier = {
            "_strategy_results": {
                "skew_momentum_vertical": {
                    "rows": [{"ticker": "AAPL", "verdict": "WATCH"}]
                }
            }
        }
        blob = zlib.compress(json.dumps(tradier).encode())
        snap = {"raw_provider_blob": blob}
        rows = _extract_skew_rows(snap)
        assert rows is not None
        assert len(rows) == 1
        assert rows[0]["ticker"] == "AAPL"
