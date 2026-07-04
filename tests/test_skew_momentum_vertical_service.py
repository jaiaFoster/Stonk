import unittest
from datetime import date, timedelta

from app.services.skew_momentum_vertical_service import construct_vertical_candidates, momentum_direction


def option(strike, option_type, bid, ask, iv, delta=0.5, oi=100, volume=20):
    return {
        "strike": strike,
        "option_type": option_type,
        "bid": bid,
        "ask": ask,
        "mid": (bid + ask) / 2,
        "iv": iv,
        "delta": delta,
        "open_interest": oi,
        "volume": volume,
    }


class SkewMomentumVerticalServiceTests(unittest.TestCase):
    def setUp(self):
        self.expiration = (date.today() + timedelta(days=21)).isoformat()

    def test_bullish_vertical_construction_and_payoff(self):
        direction = {"direction": "bullish", "score": 82, "confirmed": True, "reason": "Bullish trend confirmed."}
        rows = construct_vertical_candidates(
            "CRDO", direction, 100, self.expiration,
            [option(100, "call", 1.9, 2.0, .30), option(105, "call", .70, .75, .40, .25)],
        )
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["possible_spread"]["long_strike"], 100)
        self.assertEqual(row["possible_spread"]["short_strike"], 105)
        self.assertEqual(row["breakeven"], 101.3)
        self.assertGreater(row["reward_risk"], 2.0)
        self.assertTrue(row["verdict"].startswith("PASS"))

    def test_bearish_vertical_construction(self):
        direction = {"direction": "bearish", "score": 80, "confirmed": True, "reason": "Bearish trend confirmed."}
        rows = construct_vertical_candidates(
            "SOFI", direction, 100, self.expiration,
            [option(95, "put", .70, .75, .37, -.25), option(100, "put", 1.9, 2.0, .30, -.50)],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["possible_spread"]["long_strike"], 100)
        self.assertEqual(rows[0]["possible_spread"]["short_strike"], 95)
        self.assertEqual(rows[0]["breakeven"], 98.7)

    def test_no_skew_edge_is_watch_not_pass(self):
        direction = {"direction": "bullish", "score": 85, "confirmed": True, "reason": "Bullish."}
        row = construct_vertical_candidates(
            "NVDA", direction, 100, self.expiration,
            [option(100, "call", 1.9, 2.0, .30), option(105, "call", .30, .34, .30, .25)],
        )[0]
        self.assertEqual(row["verdict"], "WATCH / SKEW NOT RICH ENOUGH")

    def test_illiquid_legs_are_fatal_even_with_good_score_inputs(self):
        direction = {"direction": "bullish", "score": 90, "confirmed": True, "reason": "Bullish."}
        row = construct_vertical_candidates(
            "NVDA", direction, 100, self.expiration,
            [option(100, "call", 1.0, 2.0, .30, oi=0, volume=0), option(105, "call", .70, 1.0, .40, .25, oi=0, volume=0)],
        )[0]
        self.assertEqual(row["verdict"], "FAIL / OPTIONS ILLIQUID")

    def test_momentum_direction_is_explainable(self):
        result = momentum_direction({
            "has_data": True,
            "above_sma_50": True,
            "above_sma_200": True,
            "return_3m_pct": 10,
            "return_6m_pct": 20,
            "return_12m_pct": 30,
            "relative_strength_6m_pct": 5,
        })
        self.assertEqual(result["direction"], "bullish")
        self.assertIn("3M", result["reason"])
        self.assertTrue(result["components"])

    def test_short_dte_spread_hard_fails(self):
        direction = {"direction": "bullish", "score": 82, "confirmed": True, "reason": "Bullish trend confirmed."}
        short_expiration = (date.today() + timedelta(days=14)).isoformat()
        rows = construct_vertical_candidates(
            "CRDO", direction, 100, short_expiration,
            [option(100, "call", 1.9, 2.0, .30), option(105, "call", .70, .75, .36, .25)],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["verdict"], "FAIL / DTE TOO SHORT")
        self.assertIn("below hard minimum", rows[0]["primary_blocker"])


if __name__ == "__main__":
    unittest.main()
