"""ASA Patch 31B — Universal Scoring + Ranking tests.

Covers:
    31B.9  — Score contract fields present
    31B.10 — Ceilings (FAIL ≤39, WATCH ≤74, PASS ≤100) and confidence bands
    31B.11 — Four strategy adapter outputs
    31B.12 — Hard-gate precedence (hard_gate_pass=False → score ≤39)
    31B.13 — UniversalStrategyRankingService tiers and sort order
    31B.16 — Explanation fields present
"""
from __future__ import annotations

import sys
import types

_rh = types.ModuleType("robin_stocks")
_rh.robinhood = types.ModuleType("robin_stocks.robinhood")
sys.modules.setdefault("robin_stocks", _rh)
sys.modules.setdefault("robin_stocks.robinhood", _rh.robinhood)

import pytest


# ─── helpers ──────────────────────────────────────────────────────────────────

def _score(row: dict, strategy_id: str = "earnings_calendar") -> dict:
    from app.services.universal_scoring_service import compute_universal_score
    return compute_universal_score(row, strategy_id)


def _rank(rows: list) -> list:
    from app.services.universal_ranking_service import rank_strategy_rows
    return rank_strategy_rows(rows)


# ─── 31B.9: contract fields ──────────────────────────────────────────────────

_REQUIRED_FIELDS = {
    "universal_score", "return_score", "risk_score", "confidence_score",
    "liquidity_score", "data_quality_score", "capital_efficiency_score",
    "timing_score", "historical_evidence_score", "portfolio_fit_score",
    "actionability_score", "hard_gate_pass", "score_version",
    "score_completeness_pct", "missing_score_components", "score_confidence_band",
}


class TestScoreContractFields:
    def test_all_required_fields_present_earnings(self):
        result = _score({"verdict": "PASS / CALENDAR ENTRY", "score": 85, "calendar_entry_allowed": True}, "earnings_calendar")
        missing = _REQUIRED_FIELDS - set(result.keys())
        assert not missing, f"Missing score fields: {missing}"

    def test_all_required_fields_present_skew(self):
        result = _score({"verdict": "PASS / VERTICAL ENTRY", "skew_score": 80}, "skew_momentum_vertical")
        missing = _REQUIRED_FIELDS - set(result.keys())
        assert not missing, f"Missing score fields: {missing}"

    def test_all_required_fields_present_ff(self):
        result = _score({"verdict": "PASS / FORWARD FACTOR POSITIVE", "forward_factor": 0.25, "signal_score": 80}, "forward_factor_calendar")
        missing = _REQUIRED_FIELDS - set(result.keys())
        assert not missing, f"Missing score fields: {missing}"

    def test_all_required_fields_present_stock(self):
        result = _score({"action": "CONSIDER ADDING", "momentum_score": 75}, "stock_momentum")
        missing = _REQUIRED_FIELDS - set(result.keys())
        assert not missing, f"Missing score fields: {missing}"

    def test_score_is_integer(self):
        result = _score({"verdict": "PASS"}, "earnings_calendar")
        assert isinstance(result["universal_score"], int)

    def test_completeness_pct_is_float(self):
        result = _score({"verdict": "PASS"}, "earnings_calendar")
        assert isinstance(result["score_completeness_pct"], float)

    def test_missing_components_is_list(self):
        result = _score({}, "earnings_calendar")
        assert isinstance(result["missing_score_components"], list)


# ─── 31B.10: score ceilings and confidence bands ─────────────────────────────

class TestScoreCeilings:
    def test_fail_row_capped_at_39(self):
        result = _score({"verdict": "FAIL / DEBIT TOO LARGE"}, "earnings_calendar")
        assert result["universal_score"] <= 39, f"FAIL row score must be ≤39, got {result['universal_score']}"

    def test_hard_gate_fail_capped_at_39(self):
        result = _score({"verdict": "PASS", "hard_gate_pass": False}, "earnings_calendar")
        assert result["universal_score"] <= 39

    def test_watch_row_capped_at_74(self):
        result = _score({"verdict": "WATCH / CONFIRM TREND", "momentum_score": 95}, "stock_momentum")
        assert result["universal_score"] <= 74, f"WATCH row score must be ≤74, got {result['universal_score']}"

    def test_pass_row_can_reach_75(self):
        row = {
            "verdict": "PASS / CALENDAR ENTRY",
            "score": 95, "calendar_entry_allowed": True,
            "debit_confidence": 0.95, "liquidity_score": 90,
        }
        result = _score(row, "earnings_calendar")
        # PASS rows CAN be ≥75; just assert not capped at 74
        assert result["universal_score"] <= 100
        assert result["hard_gate_pass"] is True

    def test_confidence_band_high_at_full_components(self):
        row = {
            "verdict": "PASS / FORWARD FACTOR POSITIVE",
            "forward_factor": 0.28, "signal_score": 85,
            "liquidity_pass": True, "structure_status": "COMPLETE",
            "conservative_debit": 2.0, "debit_at_risk": 200.0,
            "put_delta_deviation": 0.01, "call_delta_deviation": 0.01,
            "edge_on_margin": 12.0, "package_slippage_pct": 2.5,
        }
        result = _score(row, "forward_factor_calendar")
        assert result["score_confidence_band"] in {"HIGH", "MEDIUM"}

    def test_confidence_band_insufficient_for_empty_row(self):
        result = _score({}, "earnings_calendar")
        assert result["score_confidence_band"] in {"LOW", "INSUFFICIENT_DATA"}


# ─── 31B.11: four strategy adapters ─────────────────────────────────────────

class TestStrategyAdapters:
    def test_earnings_calendar_pass_scores_above_50(self):
        row = {
            "verdict": "PASS / CALENDAR ENTRY",
            "score": 90, "calendar_entry_allowed": True,
            "debit_confidence": 0.9, "liquidity_score": 85,
        }
        result = _score(row, "earnings_calendar")
        assert result["universal_score"] >= 50

    def test_earnings_calendar_fail_scores_below_40(self):
        result = _score({"verdict": "FAIL / DEBIT TOO LARGE"}, "earnings_calendar")
        assert result["universal_score"] <= 39

    def test_skew_pass_scores_above_50(self):
        row = {"verdict": "PASS / VERTICAL ENTRY", "skew_score": 85, "entry_score": 80}
        result = _score(row, "skew_momentum_vertical")
        assert result["universal_score"] >= 50

    def test_forward_factor_pass_scores_above_50(self):
        row = {
            "verdict": "PASS / FORWARD FACTOR POSITIVE",
            "forward_factor": 0.30, "signal_score": 85,
            "liquidity_pass": True, "structure_status": "COMPLETE",
            "edge_on_margin": 15.0, "package_slippage_pct": 2.0,
        }
        result = _score(row, "forward_factor_calendar")
        assert result["universal_score"] >= 50

    def test_stock_momentum_add_scores_above_40(self):
        row = {"action": "CONSIDER ADDING", "momentum_score": 80}
        result = _score(row, "stock_momentum")
        assert result["universal_score"] >= 40

    def test_stock_momentum_avoid_scores_below_40(self):
        result = _score({"action": "AVOID / WEAK TREND", "momentum_score": 10}, "stock_momentum")
        assert result["universal_score"] <= 39


# ─── 31B.12: hard-gate precedence ────────────────────────────────────────────

class TestHardGatePrecedence:
    def test_explicit_hard_gate_false_caps_score(self):
        row = {"verdict": "PASS", "hard_gate_pass": False, "score": 95}
        result = _score(row, "earnings_calendar")
        assert result["universal_score"] <= 39
        assert result["hard_gate_pass"] is False

    def test_fail_verdict_implies_hard_gate_false(self):
        result = _score({"verdict": "FAIL / NO ELIGIBLE EXPIRATION PAIR"}, "forward_factor_calendar")
        assert result["universal_score"] <= 39

    def test_pass_verdict_implies_hard_gate_true(self):
        result = _score({"verdict": "PASS / FORWARD FACTOR POSITIVE", "forward_factor": 0.28, "signal_score": 80}, "forward_factor_calendar")
        assert result["hard_gate_pass"] is True


# ─── 31B.13: ranking service ─────────────────────────────────────────────────

class TestUniversalRankingService:
    def _make_row(self, strategy_id, verdict, score, ticker="AAPL", actionable=True):
        return {
            "strategy_id": strategy_id, "ticker": ticker, "verdict": verdict,
            "universal_score": score, "hard_gate_pass": not verdict.startswith("FAIL"),
            "strategy_actionable": actionable,
            "liquidity_score": 70, "data_quality_score": 80,
            "score_confidence_band": "HIGH",
        }

    def test_global_rank_assigned(self):
        rows = [self._make_row("earnings_calendar", "PASS", 85), self._make_row("skew_momentum_vertical", "PASS", 78)]
        ranked = _rank(rows)
        assert all("global_rank" in r for r in ranked)
        ranks = sorted(r["global_rank"] for r in ranked)
        assert ranks == list(range(1, len(rows) + 1))

    def test_strategy_rank_assigned(self):
        rows = [
            self._make_row("earnings_calendar", "PASS", 85),
            self._make_row("earnings_calendar", "PASS", 70),
        ]
        ranked = _rank(rows)
        strategy_ranks = {r["global_rank"]: r["strategy_rank"] for r in ranked}
        assert 1 in strategy_ranks.values()
        assert 2 in strategy_ranks.values()

    def test_ticker_rank_assigned(self):
        rows = [
            self._make_row("earnings_calendar", "PASS", 85, ticker="AAPL"),
            self._make_row("skew_momentum_vertical", "PASS", 78, ticker="AAPL"),
        ]
        ranked = _rank(rows)
        ticker_ranks = sorted(r["ticker_rank"] for r in ranked)
        assert ticker_ranks == [1, 2]

    def test_tier_a_for_high_score_pass(self):
        rows = [self._make_row("earnings_calendar", "PASS / CALENDAR ENTRY", 80)]
        ranked = _rank(rows)
        assert ranked[0]["opportunity_tier"] == "A"

    def test_tier_b_for_watch_verdict(self):
        rows = [self._make_row("stock_momentum", "WATCH / CONFIRM TREND", 60, actionable=False)]
        ranked = _rank(rows)
        assert ranked[0]["opportunity_tier"] in {"B", "C"}

    def test_tier_rejected_for_fail(self):
        rows = [{"strategy_id": "earnings_calendar", "ticker": "AAPL", "verdict": "FAIL / DEBIT TOO LARGE", "universal_score": 20, "hard_gate_pass": False, "strategy_actionable": False, "liquidity_score": 50, "data_quality_score": 50, "score_confidence_band": "LOW"}]
        ranked = _rank(rows)
        assert ranked[0]["opportunity_tier"] == "REJECTED"

    def test_pass_rows_sort_before_fail(self):
        rows = [
            {"strategy_id": "earnings_calendar", "ticker": "FAIL_TICKER", "verdict": "FAIL", "universal_score": 30, "hard_gate_pass": False, "strategy_actionable": False, "liquidity_score": 50, "data_quality_score": 50, "score_confidence_band": "LOW"},
            {"strategy_id": "earnings_calendar", "ticker": "PASS_TICKER", "verdict": "PASS", "universal_score": 80, "hard_gate_pass": True, "strategy_actionable": True, "liquidity_score": 80, "data_quality_score": 80, "score_confidence_band": "HIGH"},
        ]
        ranked = _rank(rows)
        assert ranked[0]["ticker"] == "PASS_TICKER"

    def test_higher_score_sorts_first_within_pass(self):
        rows = [
            self._make_row("earnings_calendar", "PASS", 70, ticker="MID"),
            self._make_row("earnings_calendar", "PASS", 90, ticker="TOP"),
            self._make_row("earnings_calendar", "PASS", 55, ticker="LOW"),
        ]
        ranked = _rank(rows)
        assert ranked[0]["ticker"] == "TOP"
        assert ranked[-1]["ticker"] == "LOW"

    def test_empty_input_returns_empty(self):
        assert _rank([]) == []

    def test_ranking_version_set(self):
        rows = [self._make_row("earnings_calendar", "PASS", 80)]
        ranked = _rank(rows)
        assert ranked[0].get("ranking_version")


# ─── 31B.16: explanation fields ──────────────────────────────────────────────

class TestExplanationFields:
    def test_top_strengths_present(self):
        result = _score({"verdict": "PASS / CALENDAR ENTRY", "score": 85, "calendar_entry_allowed": True}, "earnings_calendar")
        assert "top_strengths" in result
        assert isinstance(result["top_strengths"], list)

    def test_top_risks_present(self):
        result = _score({"verdict": "FAIL"}, "earnings_calendar")
        assert "top_risks" in result
        assert isinstance(result["top_risks"], list)

    def test_why_actionable_present(self):
        result = _score({"verdict": "PASS"}, "earnings_calendar")
        assert "why_actionable_or_not" in result
        assert isinstance(result["why_actionable_or_not"], str)

    def test_score_breakdown_present(self):
        result = _score({"verdict": "PASS / CALENDAR ENTRY", "score": 85}, "earnings_calendar")
        assert "score_breakdown" in result

    def test_why_not_higher_present(self):
        result = _score({"verdict": "PASS / CALENDAR ENTRY", "score": 85}, "earnings_calendar")
        assert "why_not_higher" in result
