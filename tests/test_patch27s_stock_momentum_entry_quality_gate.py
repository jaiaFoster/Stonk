import unittest

from app.services.daily_opportunity_engine_service import build_daily_opportunity_engine
from app.services.stock_momentum_strategy_service import build_stock_momentum_strategy


def _metrics(**overrides):
    row = {
        "has_data": True,
        "required_market_data_complete": True,
        "data_state": "COMPLETE",
        "fresh": True,
        "bar_count": 252,
        "current_price": 100,
        "return_3m_pct": 12,
        "return_6m_pct": 25,
        "return_12m_pct": 40,
        "relative_strength_6m_pct": 8,
        "above_sma_50": True,
        "above_sma_200": True,
        "price_vs_sma_50_pct": 8,
        "price_vs_sma_200_pct": 25,
        "distance_from_52w_high_pct": -5,
        "average_volume_30d": 2_000_000,
        "avg_volume_30d": 2_000_000,
        "realized_volatility_30d": 45,
    }
    row.update(overrides)
    return row


def _strategy(ticker, metrics, *, positions=None, gap=True):
    gaps = {"suggestions": [{"ticker": ticker, "score": 85, "category": "CONSIDER ADDING / RESEARCH"}]} if gap else {}
    result = build_stock_momentum_strategy(
        positions=positions or [],
        watchlist_candidates={"items": [{"ticker": ticker}]},
        recommendations=[],
        market_metrics={ticker: metrics},
        portfolio_gap_analysis=gaps,
        news_map={},
        log_print=lambda message: None,
    )
    result["items"] = [row for row in result["items"] if row["ticker"] == ticker]
    return result


class Patch27SStockMomentumEntryQualityGateTests(unittest.TestCase):
    def test_clean_candidate_gets_complete_entry_plan(self):
        row = _strategy("ALGN", _metrics())["items"][0]
        self.assertEqual(row["entry_quality"], "BUYABLE_NOW")
        self.assertTrue(row["add_allowed_boolean"])
        self.assertEqual(row["action"], "CONSIDER ADDING")
        self.assertIsNotNone(row["initial_stop"])
        self.assertIn("take-profit", row["take_profit_or_trailing_exit"])

    def test_extended_mu_is_not_clean_buy(self):
        row = _strategy("MU", _metrics(price_vs_sma_50_pct=36))["items"][0]
        self.assertEqual(row["entry_quality"], "EXTENDED_WAIT")
        self.assertFalse(row["add_allowed_boolean"])
        self.assertEqual(row["action"], "ADD ON PULLBACK")
        self.assertTrue(any("do not chase" in blocker.lower() for blocker in row["add_blockers"]))

    def test_leveraged_etf_is_tactical_only(self):
        row = _strategy("SOXL", _metrics())["items"][0]
        self.assertEqual(row["entry_quality"], "TACTICAL_ONLY")
        self.assertFalse(row["add_allowed_boolean"])
        self.assertEqual(row["action"], "TACTICAL ONLY / DO NOT CHASE")
        self.assertIn("Leveraged ETF", " ".join(row["add_blockers"]))

    def test_high_volatility_name_is_starter_only(self):
        row = _strategy("CRDO", _metrics(realized_volatility_30d=88))["items"][0]
        self.assertEqual(row["entry_quality"], "HIGH_BETA_STARTER_ONLY")
        self.assertFalse(row["add_allowed_boolean"])
        self.assertEqual(row["action"], "STARTER ONLY / WAIT FOR PULLBACK")

    def test_overweight_holding_cannot_add(self):
        positions = [{"ticker": "NVDA", "market_value": 900}, {"ticker": "CASH", "market_value": 100}]
        row = _strategy("NVDA", _metrics(), positions=positions)["items"][0]
        self.assertFalse(row["add_allowed_boolean"])
        self.assertEqual(row["action"], "HOLD / DO NOT ADD")
        self.assertTrue(any("allocation" in blocker.lower() for blocker in row["add_blockers"]))

    def test_daily_opportunity_downgrades_blocked_add(self):
        strategy = {"items": [{
            "ticker": "MU",
            "score": 95,
            "action": "CONSIDER ADDING",
            "entry_quality": "EXTENDED_WAIT",
            "add_allowed_boolean": False,
            "add_blockers": ["Do not chase."],
            "market_metrics": _metrics(),
        }]}
        gap = {"suggestions": [{"ticker": "MU", "score": 95, "category": "HIGH-PRIORITY CONSIDER ADDING"}]}
        result = build_daily_opportunity_engine({}, strategy, gap, [], log_print=lambda message: None)
        row = next(item for item in result["actions"] if item["ticker"] == "MU")
        self.assertIn("WATCH", row["action"])
        self.assertFalse(row["add_allowed_boolean"])


if __name__ == "__main__":
    unittest.main()
