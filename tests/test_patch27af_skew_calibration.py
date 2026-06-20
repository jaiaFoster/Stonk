"""Smoke tests for Patch 27AF — Skew calibration research (TKT-013)."""

from __future__ import annotations

import importlib
import sys
from unittest.mock import patch

import pytest


def _reload_config():
    if "app.config" in sys.modules:
        importlib.reload(sys.modules["app.config"])


# ---------------------------------------------------------------------------
# Helpers to build fake option rows
# ---------------------------------------------------------------------------

def _call(delta, mid, volume=0, iv=0.30, strike=100.0):
    bid = max(0.01, mid - 0.01)
    ask = mid + 0.01
    return {
        "option_type": "call",
        "strike": strike,
        "delta": delta,
        "mid": mid,
        "bid": bid,
        "ask": ask,
        "iv": iv,
        "open_interest": 100,
        "volume": volume,
    }


# ---------------------------------------------------------------------------
# Phase 1 — Lottery-call filter
# ---------------------------------------------------------------------------

class TestLotteryCallFilter:
    def test_strips_lottery_calls_when_enabled(self):
        from app.services.skew_momentum_vertical_service import _apply_lottery_filter

        options = [
            _call(delta=0.10, mid=0.05, volume=50),   # lottery: delta<0.15, mid<0.10, volume>0
            _call(delta=0.35, mid=0.80, volume=200),  # keeper
            _call(delta=0.50, mid=1.50, volume=100),  # keeper
        ]
        with patch("app.config.SKEW_LOTTERY_CALL_FILTER_ENABLED", True), \
             patch("app.config.SKEW_LOTTERY_CALL_DELTA_THRESHOLD", 0.15), \
             patch("app.config.SKEW_LOTTERY_CALL_PREMIUM_THRESHOLD", 0.10):
            kept, stripped = _apply_lottery_filter(options)

        assert stripped == 1
        assert len(kept) == 2

    def test_does_not_strip_when_filter_disabled(self):
        from app.services.skew_momentum_vertical_service import _apply_lottery_filter

        options = [
            _call(delta=0.10, mid=0.05, volume=50),
            _call(delta=0.35, mid=0.80, volume=200),
        ]
        with patch("app.config.SKEW_LOTTERY_CALL_FILTER_ENABLED", False):
            kept, stripped = _apply_lottery_filter(options)

        assert stripped == 0
        assert len(kept) == 2

    def test_does_not_strip_zero_volume_even_if_cheap(self):
        """volume=0 means no retail flow — not a lottery call by definition."""
        from app.services.skew_momentum_vertical_service import _apply_lottery_filter

        options = [
            _call(delta=0.08, mid=0.04, volume=0),   # cheap, low delta, but volume=0 → keep
        ]
        with patch("app.config.SKEW_LOTTERY_CALL_FILTER_ENABLED", True), \
             patch("app.config.SKEW_LOTTERY_CALL_DELTA_THRESHOLD", 0.15), \
             patch("app.config.SKEW_LOTTERY_CALL_PREMIUM_THRESHOLD", 0.10):
            kept, stripped = _apply_lottery_filter(options)

        assert stripped == 0
        assert len(kept) == 1

    def test_does_not_strip_high_premium_otm(self):
        """mid >= threshold → not stripped even if delta is small."""
        from app.services.skew_momentum_vertical_service import _apply_lottery_filter

        options = [
            _call(delta=0.10, mid=0.15, volume=50),  # mid >= 0.10 → keep
        ]
        with patch("app.config.SKEW_LOTTERY_CALL_FILTER_ENABLED", True), \
             patch("app.config.SKEW_LOTTERY_CALL_DELTA_THRESHOLD", 0.15), \
             patch("app.config.SKEW_LOTTERY_CALL_PREMIUM_THRESHOLD", 0.10):
            kept, stripped = _apply_lottery_filter(options)

        assert stripped == 0
        assert len(kept) == 1

    def test_strips_multiple_lottery_calls(self):
        from app.services.skew_momentum_vertical_service import _apply_lottery_filter

        options = [
            _call(delta=0.05, mid=0.02, volume=10),
            _call(delta=0.08, mid=0.03, volume=5),
            _call(delta=0.40, mid=1.00, volume=200),
        ]
        with patch("app.config.SKEW_LOTTERY_CALL_FILTER_ENABLED", True), \
             patch("app.config.SKEW_LOTTERY_CALL_DELTA_THRESHOLD", 0.15), \
             patch("app.config.SKEW_LOTTERY_CALL_PREMIUM_THRESHOLD", 0.10):
            kept, stripped = _apply_lottery_filter(options)

        assert stripped == 2
        assert len(kept) == 1


# ---------------------------------------------------------------------------
# Chain-level skew score
# ---------------------------------------------------------------------------

class TestComputeChainSkew:
    def test_returns_zero_with_no_atm_options(self):
        from app.services.skew_momentum_vertical_service import _compute_chain_skew

        options = [_call(delta=0.10, mid=0.05, iv=0.50)]
        score = _compute_chain_skew(options)
        assert score == 0.0

    def test_raw_higher_than_adjusted_when_lottery_calls_inflated_iv(self):
        """Lottery calls have inflated IV → raw_skew_score > adjusted_skew_score."""
        from app.services.skew_momentum_vertical_service import _compute_chain_skew

        lottery = _call(delta=0.08, mid=0.05, volume=10, iv=1.20, strike=115.0)  # extreme IV
        atm_call = _call(delta=0.48, mid=1.50, volume=100, iv=0.30, strike=100.0)
        otm_normal = _call(delta=0.22, mid=0.40, volume=30, iv=0.35, strike=107.0)

        all_options = [lottery, atm_call, otm_normal]
        filtered = [atm_call, otm_normal]

        raw = _compute_chain_skew(all_options)
        adjusted = _compute_chain_skew(filtered)

        assert raw > adjusted

    def test_adjusted_skew_score_zero_when_no_options_after_filter(self):
        from app.services.skew_momentum_vertical_service import _compute_chain_skew

        score = _compute_chain_skew([])
        assert score == 0.0


# ---------------------------------------------------------------------------
# Output fields on candidate
# ---------------------------------------------------------------------------

class TestCandidateSkewFields:
    def _make_candidate_with_patch(self, skew_filter_applied=False, lottery_count=0):
        """Build a minimal candidate dict directly from _candidate_row using patched config."""
        from app.services.skew_momentum_vertical_service import _candidate_row

        direction = {"direction": "bullish", "confirmed": True, "score": 70.0, "reason": "Bullish."}
        long_leg = {
            "strike": 100.0, "bid": 1.00, "ask": 1.20, "mid": 1.10,
            "iv": 0.30, "delta": 0.50, "open_interest": 200, "volume": 100,
        }
        short_leg = {
            "strike": 105.0, "bid": 0.40, "ask": 0.50, "mid": 0.45,
            "iv": 0.35, "delta": 0.30, "open_interest": 150, "volume": 80,
        }
        row = _candidate_row(
            ticker="AAPL",
            direction=direction,
            underlying=100.0,
            expiration="2024-03-15",
            dte=28,
            option_type="call",
            long_leg=long_leg,
            short_leg=short_leg,
            metrics={},
            earnings_event={},
            account_context={},
            raw_skew_score=18.5,
            adjusted_skew_score=12.0,
            lottery_calls_stripped_count=lottery_count,
            skew_filter_applied=skew_filter_applied,
        )
        return row

    def test_raw_and_adjusted_skew_score_present(self):
        row = self._make_candidate_with_patch()
        assert "raw_skew_score" in row
        assert "adjusted_skew_score" in row

    def test_lottery_calls_stripped_count_present(self):
        row = self._make_candidate_with_patch(lottery_count=3, skew_filter_applied=True)
        assert row["lottery_calls_stripped_count"] == 3

    def test_skew_filter_applied_flag(self):
        row = self._make_candidate_with_patch(skew_filter_applied=True, lottery_count=2)
        assert row["skew_filter_applied"] is True

    def test_skew_gap_to_pass_always_computed(self):
        with patch("app.config.SKEW_DIAGNOSTIC_MODE", False):
            row = self._make_candidate_with_patch()
        assert row["skew_gap_to_pass"] is not None
        assert isinstance(row["skew_gap_to_pass"], float)

    def test_skew_gap_to_pass_computed_when_diagnostic_mode_on(self):
        with patch("app.config.SKEW_DIAGNOSTIC_MODE", True), \
             patch("app.config.SKEW_RICHNESS_THRESHOLD", 12.5):
            row = self._make_candidate_with_patch()
        assert row["skew_gap_to_pass"] is not None
        assert isinstance(row["skew_gap_to_pass"], float)

    def test_skew_gap_to_pass_negative_when_adjusted_above_threshold(self):
        """adjusted_skew_score > threshold → negative gap (already passing)."""
        with patch("app.config.SKEW_DIAGNOSTIC_MODE", True), \
             patch("app.config.SKEW_RICHNESS_THRESHOLD", 10.0):
            from app.services.skew_momentum_vertical_service import _candidate_row
            direction = {"direction": "bullish", "confirmed": True, "score": 70.0, "reason": "x"}
            leg = {"strike": 100.0, "bid": 1.00, "ask": 1.20, "mid": 1.10, "iv": 0.30, "delta": 0.50, "open_interest": 200, "volume": 100}
            sleg = {"strike": 105.0, "bid": 0.40, "ask": 0.50, "mid": 0.45, "iv": 0.35, "delta": 0.30, "open_interest": 150, "volume": 80}
            row = _candidate_row("X", direction, 100.0, "2024-03-15", 28, "call", leg, sleg, {}, {}, {}, adjusted_skew_score=15.0)
        # 10.0 - 15.0 = -5.0
        assert row["skew_gap_to_pass"] == -5.0

    def test_would_pass_at_threshold_equals_adjusted_skew(self):
        """would_pass_at_threshold = adjusted_skew_score rounded to 2dp."""
        row = self._make_candidate_with_patch()
        assert "would_pass_at_threshold" in row
        assert row["would_pass_at_threshold"] == 12.0

    def test_would_pass_at_threshold_with_high_score(self):
        with patch("app.config.SKEW_RICHNESS_THRESHOLD", 10.0):
            from app.services.skew_momentum_vertical_service import _candidate_row
            direction = {"direction": "bullish", "confirmed": True, "score": 70.0, "reason": "x"}
            leg = {"strike": 100.0, "bid": 1.00, "ask": 1.20, "mid": 1.10, "iv": 0.30, "delta": 0.50, "open_interest": 200, "volume": 100}
            sleg = {"strike": 105.0, "bid": 0.40, "ask": 0.50, "mid": 0.45, "iv": 0.35, "delta": 0.30, "open_interest": 150, "volume": 80}
            row = _candidate_row("X", direction, 100.0, "2024-03-15", 28, "call", leg, sleg, {}, {}, {}, adjusted_skew_score=15.0)
        assert row["would_pass_at_threshold"] == 15.0
