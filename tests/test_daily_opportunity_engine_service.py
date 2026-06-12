import unittest

from app.services.daily_opportunity_engine_service import build_daily_opportunity_engine


class DailyOpportunityEngineTests(unittest.TestCase):
    def test_failed_calendar_candidate_is_not_shown_as_entry(self):
        engine = {
            "new_trade_rows": [
                {
                    "ticker": "ASO",
                    "score": 95,
                    "verdict": "FAIL / NO LIVE LIQUIDITY",
                    "entry_plan": "No entry.",
                    "requirements": [{"name": "Liquidity", "status": "FAIL", "detail": "No OI or volume."}],
                }
            ],
            "open_trade_rows": [],
        }

        result = build_daily_opportunity_engine(engine, {}, {}, [], log_print=lambda msg: None)

        self.assertEqual(result["actions"], [])

    def test_active_calendar_lifecycle_alert_survives_low_score_filter(self):
        engine = {
            "new_trade_rows": [],
            "open_trade_rows": [
                {
                    "ticker": "PDD",
                    "score": 20,
                    "verdict": "URGENT REVIEW / EXIT CHECK",
                    "next_action": "Review before close.",
                    "raw": {"lifecycle_priority_score": 20, "decision_summary": "Short leg near event."},
                }
            ],
        }

        result = build_daily_opportunity_engine(engine, {}, {}, [], log_print=lambda msg: None)

        self.assertEqual(len(result["actions"]), 1)
        self.assertEqual(result["actions"][0]["type"], "active_calendar")
        self.assertGreaterEqual(result["actions"][0]["priority_score"], 90)

    def test_active_calendar_sorts_above_higher_scored_stock_add(self):
        engine = {
            "new_trade_rows": [],
            "open_trade_rows": [
                {
                    "ticker": "AVGO",
                    "score": 78,
                    "verdict": "RECHECK BEFORE CLOSE",
                    "next_action": "Review before close.",
                    "raw": {"lifecycle_priority_score": 78},
                }
            ],
        }
        stock = {"items": [{"ticker": "NVDA", "score": 100, "action": "CONSIDER ADDING", "reasons": ["Strong trend."], "market_metrics": {
            "has_data": True, "data_state": "COMPLETE", "current_price": 100, "bar_count": 240,
            "return_3m_pct": 10, "above_sma_200": True, "avg_volume_30d": 1000000, "fresh": True,
        }}]}

        result = build_daily_opportunity_engine(engine, stock, {}, [], log_print=lambda msg: None)

        self.assertGreaterEqual(len(result["actions"]), 2)
        self.assertEqual(result["actions"][0]["ticker"], "AVGO")
        self.assertEqual(result["actions"][0]["type"], "active_calendar")

    def test_avoid_gap_suggestion_stays_risk_not_stock_add(self):
        gap = {
            "suggestions": [
                {
                    "ticker": "SOFI",
                    "score": 88,
                    "category": "AVOID ADDING / REDUCE RISK",
                    "reason": "Already too risky for add sizing.",
                },
                {
                    "ticker": "NVDA",
                    "score": 86,
                    "category": "CONSIDER ADDING / RESEARCH",
                    "reason": "Constructive growth bucket candidate.",
                },
            ]
        }

        result = build_daily_opportunity_engine({}, {}, gap, [], log_print=lambda msg: None)
        by_ticker = {row["ticker"]: row for row in result["actions"]}

        self.assertEqual(by_ticker["SOFI"]["type"], "risk")
        self.assertIn("AVOID", by_ticker["SOFI"]["action"])
        self.assertEqual(by_ticker["NVDA"]["type"], "stock_add")
        self.assertLess(result["actions"].index(by_ticker["NVDA"]), result["actions"].index(by_ticker["SOFI"]))

    def test_zero_value_recommendation_does_not_create_daily_risk(self):
        recommendations = [
            {
                "ticker": "BTC",
                "quantity": 0,
                "position_value": 0,
                "allocation_pct": 0,
                "action": "AVOID ADDING / REDUCE RISK",
                "score": 20,
            }
        ]

        result = build_daily_opportunity_engine({}, {}, {}, recommendations, log_print=lambda msg: None)

        self.assertEqual(result["actions"], [])

    def test_strategy_two_watch_excluded_and_summary_logged(self):
        logs = []
        strategy = {
            "pass_items": [],
            "watch_items": [{"ticker": "NVDA", "score": 90, "verdict": "WATCH / SKEW NOT RICH ENOUGH"}],
            "blocked_items": [],
            "summary": {"pass_count": 0, "watch_count": 1, "blocked_count": 0},
        }
        result = build_daily_opportunity_engine({}, {}, {}, [], log_print=logs.append, skew_momentum_vertical_strategy=strategy)
        self.assertEqual(result["actions"], [])
        self.assertTrue(any("0 skew_vertical" in line for line in logs))
        self.assertTrue(any("Strategy 2 summary: 0 pass, 1 watch, 0 fail; 0 included" in line for line in logs))

    def test_incomplete_market_data_cannot_be_actionable_add(self):
        stock = {"items": [{"ticker": "MU", "score": 95, "action": "CONSIDER ADDING", "market_metrics": {"has_data": False, "data_state": "SKIPPED_DEV_CAP"}}]}
        result = build_daily_opportunity_engine({}, stock, {}, [], log_print=lambda msg: None)
        self.assertEqual(result["actions"], [])


if __name__ == "__main__":
    unittest.main()
