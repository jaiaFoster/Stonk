"""
tests/test_tkt041_skew_lifecycle_fields.py — TKT-041 skew lifecycle fields always present.

Verifies lifecycle_status, active_count, and active_rows are always
present in skew diagnostic output and survive snapshot compaction.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Skew strategy result always has lifecycle fields
# ---------------------------------------------------------------------------

class TestSkewLifecycleFieldsPresent:
    def test_deferred_when_no_open_options(self):
        from app.services.skew_momentum_vertical_service import _finalize

        result = {
            "enabled": False,
            "items": [],
            "active_items": [],
            "lifecycle_status": "deferred",
            "active_count": 0,
            "errors": [],
            "scanned_tickers": [],
            "configured_max_tickers": 10,
            "runtime_ticker_cap": 10,
            "run_mode": "prod",
        }
        finalized = _finalize(result)

        assert "lifecycle_status" in finalized
        assert finalized["lifecycle_status"] == "deferred"
        assert "active_count" in finalized
        assert finalized["active_count"] == 0
        assert "active_rows" in finalized
        assert finalized["active_rows"] == []
        assert finalized["summary"]["lifecycle_status"] == "deferred"
        assert finalized["summary"]["active_count"] == 0

    def test_active_when_open_options_present(self):
        from app.services.skew_momentum_vertical_service import build_skew_momentum_vertical_strategy

        open_options = {
            "verticals": [
                {
                    "ticker": "AAPL",
                    "strategy_type": "vertical",
                    "quantity": 1,
                    "pct_of_max_profit": 30.0,
                    "dte": 15,
                    "unrealized_pnl_pct": 10.0,
                },
            ]
        }
        with patch("app.config.SKEW_VERTICAL_STRATEGY_ENABLED", False):
            result = build_skew_momentum_vertical_strategy(
                positions=[], watchlist_candidates=None,
                portfolio_gap_analysis=None, market_metrics=None,
                open_options=open_options,
            )

        assert result["lifecycle_status"] == "active"
        assert result["active_count"] == 1
        assert len(result["active_rows"]) == 1
        assert result["active_rows"][0]["ticker"] == "AAPL"


# ---------------------------------------------------------------------------
# Snapshot compaction preserves lifecycle fields
# ---------------------------------------------------------------------------

class TestSnapshotCompactionPreservesLifecycle:
    def test_compact_strategy_keeps_lifecycle_fields(self):
        from app.services.report_snapshot_service import _compact_strategy

        strategy = {
            "strategy_id": "skew_momentum_vertical",
            "strategy_label": "Skew Momentum Vertical",
            "enabled": True,
            "ran": True,
            "lifecycle_status": "active",
            "active_count": 3,
            "pass_count": 2,
            "watch_count": 1,
            "summary": {"active_count": 3, "lifecycle_status": "active"},
        }
        compacted = _compact_strategy(strategy, include_rows=False)

        assert compacted["lifecycle_status"] == "active"
        assert compacted["active_count"] == 3

    def test_compact_strategy_lifecycle_deferred_when_absent(self):
        from app.services.report_snapshot_service import _compact_strategy

        strategy = {
            "strategy_id": "skew_momentum_vertical",
            "enabled": True,
        }
        compacted = _compact_strategy(strategy, include_rows=False)
        assert compacted.get("lifecycle_status") is None
        assert compacted.get("active_count") is None

    def test_compact_hot_detail_keeps_lifecycle_fields(self):
        from app.services.report_snapshot_service import _compact_hot_detail

        detail = {
            "enabled": True,
            "has_data": True,
            "lifecycle_status": "inactive",
            "active_count": 0,
            "active_rows": [],
            "summary": {"lifecycle_status": "inactive", "active_count": 0},
        }
        compacted = _compact_hot_detail(detail)

        assert compacted["lifecycle_status"] == "inactive"
        assert compacted["active_count"] == 0


# ---------------------------------------------------------------------------
# Advisor data service strategy summary includes lifecycle
# ---------------------------------------------------------------------------

class TestAdvisorStrategyLifecycle:
    def test_strategy_summary_includes_lifecycle(self):
        from app.services.advisor_data_service import _strategy_summary

        strategies = {
            "skew_momentum_vertical": {
                "pass_count": 2,
                "watch_count": 1,
                "fail_count": 0,
                "skipped_count": 0,
                "lifecycle_status": "active",
                "active_count": 3,
            },
            "earnings_calendar": {
                "pass_count": 1,
                "watch_count": 0,
                "fail_count": 0,
                "skipped_count": 0,
            },
        }
        result = _strategy_summary(strategies)

        assert result["skew_momentum_vertical"]["lifecycle_status"] == "active"
        assert result["skew_momentum_vertical"]["active_count"] == 3
        assert result["earnings_calendar"]["lifecycle_status"] is None
        assert result["earnings_calendar"]["active_count"] == 0
