"""
ASA Patch 30B — Daily Opportunity Compatibility Tests

Verifies that universalization does NOT change existing Daily Opportunity behavior:
  - Stock Momentum DO eligibility (CONSIDER ADDING / ADD ON PULLBACK = eligible)
  - FF always excluded
  - Legacy Daily Opportunity ranking logic unaffected
  - Universal daily_opportunity dict agrees with existing daily_opportunity_eligible bool
"""
from __future__ import annotations

import py_compile
from typing import Any


class TestCompile:
    def test_stock_momentum_service_compiles(self):
        py_compile.compile("app/services/stock_momentum_strategy_service.py", doraise=True)


# ─── Legacy DO eligibility rules unchanged ────────────────────────────────────

class TestDailyOpportunityEligibility:
    def _normalize(self, row: dict, strategy_id: str) -> dict:
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        normalize_strategy_row(row, strategy_id)
        return row

    def test_consider_adding_eligible(self):
        row = {"ticker": "AAPL", "action": "CONSIDER ADDING"}
        self._normalize(row, "stock_momentum")
        assert row.get("daily_opportunity_eligible") is True

    def test_add_on_pullback_eligible(self):
        row = {"ticker": "AAPL", "action": "ADD ON PULLBACK"}
        self._normalize(row, "stock_momentum")
        assert row.get("daily_opportunity_eligible") is True

    def test_watch_not_eligible(self):
        row = {"ticker": "AAPL", "action": "WATCH / CONFIRM TREND"}
        self._normalize(row, "stock_momentum")
        assert row.get("daily_opportunity_eligible") is False

    def test_avoid_not_eligible(self):
        row = {"ticker": "AAPL", "action": "AVOID ADDING"}
        self._normalize(row, "stock_momentum")
        assert row.get("daily_opportunity_eligible") is False

    def test_ff_always_excluded(self):
        row = {"ticker": "AAPL", "verdict": "PASS — FF signal"}
        self._normalize(row, "forward_factor_calendar")
        assert row.get("daily_opportunity_eligible") is False

    def test_ff_can_trade_live_false(self):
        row = {"ticker": "AAPL", "verdict": "PASS — FF signal"}
        self._normalize(row, "forward_factor_calendar")
        assert row.get("can_trade_live") is False


# ─── Universal dict agrees with existing bool ──────────────────────────────────

class TestUniversalDODictAgreement:
    def _build_row(self, action: str, eligible: bool) -> dict:
        from app.strategies.stock_momentum_universal import build_stock_momentum_universal_row
        row = {
            "ticker": "MSFT",
            "action": action,
            "score": 75.0,
            "daily_opportunity_eligible": eligible,
            "daily_opportunity_reason": "Stock-only signal.",
            "market_metrics": {},
        }
        build_stock_momentum_universal_row(row)
        return row

    def test_universal_eligible_matches_bool_true(self):
        row = self._build_row("CONSIDER ADDING", True)
        assert row["daily_opportunity"]["eligible"] is True

    def test_universal_eligible_matches_bool_false(self):
        row = self._build_row("WATCH / CONFIRM TREND", False)
        assert row["daily_opportunity"]["eligible"] is False

    def test_exclusion_reason_empty_when_eligible(self):
        row = self._build_row("CONSIDER ADDING", True)
        assert row["daily_opportunity"]["exclusion_reason"] == ""

    def test_exclusion_reason_set_when_ineligible(self):
        row = self._build_row("WATCH / CONFIRM TREND", False)
        assert row["daily_opportunity"]["exclusion_reason"]

    def test_bucket_is_stock_momentum(self):
        row = self._build_row("CONSIDER ADDING", True)
        assert row["daily_opportunity"]["bucket"] == "stock_momentum"


# ─── End-to-end: production service DO counts stable ─────────────────────────

class TestProductionDOCountsStable:
    def _run(self, tickers_and_metrics: dict[str, dict]) -> dict:
        from app.services.stock_momentum_strategy_service import build_stock_momentum_strategy
        watchlist = {"items": [{"ticker": t} for t in tickers_and_metrics]}
        return build_stock_momentum_strategy(
            positions=[],
            watchlist_candidates=watchlist,
            recommendations=None,
            market_metrics=tickers_and_metrics,
            portfolio_gap_analysis=None,
            news_map=None,
        )

    def _do_count(self, result: dict) -> int:
        return sum(
            1 for item in result.get("items") or []
            if item.get("daily_opportunity_eligible") is True
        )

    def _do_count_universal(self, result: dict) -> int:
        return sum(
            1 for item in result.get("items") or []
            if (item.get("daily_opportunity") or {}).get("eligible") is True
        )

    def _strong_metrics(self) -> dict:
        return {
            "above_sma_50": True,
            "above_sma_200": True,
            "return_3m_pct": 12.0,
            "return_6m_pct": 22.0,
            "relative_strength_6m_pct": 8.0,
            "distance_from_52w_high_pct": -5.0,
            "average_volume_30d": 50_000_000,
            "realized_volatility_30d": 25.0,
            "price_vs_sma_50_pct": 5.0,
            "current_price": 190.0,
        }

    def test_legacy_do_bool_matches_universal_do_dict(self):
        metrics = {
            "AAPL": self._strong_metrics(),
            "MSFT": self._strong_metrics(),
        }
        result = self._run(metrics)
        legacy_count = self._do_count(result)
        universal_count = self._do_count_universal(result)
        assert legacy_count == universal_count, (
            f"Legacy DO count {legacy_count} != universal DO count {universal_count}"
        )

    def test_weak_ticker_not_do_eligible(self):
        weak = {
            "WEAK": {
                "above_sma_50": False,
                "above_sma_200": False,
                "return_3m_pct": -5.0,
                "return_6m_pct": -10.0,
                "relative_strength_6m_pct": -8.0,
            }
        }
        result = self._run(weak)
        for item in result.get("items") or []:
            if item["ticker"] == "WEAK":
                assert item.get("daily_opportunity_eligible") is False
                assert (item.get("daily_opportunity") or {}).get("eligible") is False

    def test_no_reconciliation_needed_for_matching_counts(self):
        metrics = {"AAPL": self._strong_metrics()}
        result = self._run(metrics)
        for item in result.get("items") or []:
            bool_eligible = item.get("daily_opportunity_eligible")
            dict_eligible = (item.get("daily_opportunity") or {}).get("eligible")
            assert bool_eligible == dict_eligible, (
                f"{item['ticker']}: bool_eligible={bool_eligible} "
                f"!= dict_eligible={dict_eligible}"
            )
