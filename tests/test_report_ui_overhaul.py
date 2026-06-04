import unittest

from app.services.report_service import format_html


class ReportUiOverhaulTests(unittest.TestCase):
    def test_muted_black_terminal_report_hierarchy_and_data_contracts(self):
        html = format_html(
            payload="sample payload",
            positions=[],
            news_map={},
            recommendations=[
                {
                    "ticker": "MSFT",
                    "action": "HOLD, ADD ON PULLBACK",
                    "allocation_pct": 8.5,
                    "gain_loss_pct": 12.4,
                    "position_value": 1200,
                    "next_check": "Recheck tomorrow.",
                    "reasons": ["Above 200D"],
                    "risks": ["Concentration review"],
                    "market_metrics": {
                        "has_data": True,
                        "above_sma_200": True,
                        "return_6m_pct": 14.0,
                        "relative_strength_6m_pct": 3.2,
                        "distance_from_52w_high_pct": -4.0,
                    },
                }
            ],
            tradier_snapshot={
                "_unified_calendar_trade_engine": {
                    "open_trade_rows": [
                        {
                            "ticker": "AVGO",
                            "verdict": "URGENT REVIEW / EXIT CHECK",
                            "next_action": "Reprice immediately.",
                            "structure": "430 CALL, short 2026-06-05 / long 2026-06-12",
                            "estimated_pnl_pct": 12.9,
                            "pnl_total_estimate": 41,
                            "front_dte": 4,
                            "short_leg_moneyness_pct": 7.3,
                            "assignment_risk_level": "Elevated",
                            "current_mid_debit": 3.60,
                            "entry_debit_estimate": 3.19,
                            "target_debit": 4.79,
                            "stop_debit": 2.07,
                            "underlying_price": 461.42,
                            "hold_through_score": 50,
                            "hold_through_action": "CONSIDER CLOSING BEFORE EARNINGS",
                            "reasons": ["Broker-detected calendar"],
                        }
                    ],
                    "new_trade_rows": [
                        {
                            "ticker": "ASO",
                            "verdict": "FAIL / NO LIVE LIQUIDITY",
                            "trade_type_label": "Earnings calendar",
                            "main_blocker": "Options market untradeable",
                            "backtest_status": "diagnostic",
                            "account_risk_status": "ok",
                            "raw_scanner_verdict": "candidate",
                        }
                    ],
                },
                "_daily_opportunity": {
                    "actions": [
                        {
                            "type": "active_calendar",
                            "ticker": "AVGO",
                            "action": "URGENT REVIEW / EXIT CHECK",
                            "priority_score": 95,
                        },
                        {
                            "type": "stock_add",
                            "ticker": "MSFT",
                            "action": "CONSIDER ADDING",
                            "priority_score": 81,
                            "why": "Strong momentum",
                            "source": "momentum",
                        },
                    ]
                },
                "_portfolio_gap": {
                    "exposure_rows": [
                        {
                            "bucket": "AI / Semiconductors",
                            "actual_pct": 7.7,
                            "target_pct": 18.0,
                            "status": "UNDERWEIGHT",
                        }
                    ],
                    "risk_rows": [{"name": "Concentration", "detail": "Review mega-cap exposure"}],
                },
                "_pipeline_status": {"mode": "dev", "steps": []},
            },
            log_lines=[],
        )

        sections = [
            "Macro Context Strip",
            "Active Calendar Lifecycle",
            "Holdings / Portfolio Advisor",
            "Unified Potential Adds",
            "Calendar Candidates / Blocked Setups",
            "Portfolio + Macro Infographic",
            "Monitor / Debug",
        ]
        positions = [html.index(section) for section in sections]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("--bg: #000000", html)
        self.assertIn("Broker-detected open calendars", html)
        self.assertIn("AVGO", html)
        self.assertIn("Reprice immediately.", html)
        self.assertIn("ASO", html)
        self.assertIn("Options market untradeable", html)
        self.assertIn('details class="debug-details"', html)

    def test_cleanup_patch_filters_zero_assets_and_splits_adds_from_risk(self):
        html = format_html(
            payload="debug BTC SOL payload",
            positions=[
                {"ticker": "BTC", "quantity": 0.0, "market_value": 0.0, "account": "Crypto"},
                {"ticker": "SOL", "quantity": 0.0, "market_value": 0.0, "account": "Crypto"},
            ],
            news_map={},
            recommendations=[
                {"ticker": "BTC", "action": "AVOID ADDING", "position_value": 0, "risks": ["Zero row"]},
                {"ticker": "SOFI", "action": "AVOID ADDING / REDUCE RISK", "position_value": 400, "risks": ["High beta"]},
                {"ticker": "NVDA", "action": "CONSIDER ADDING", "position_value": 1000, "reasons": ["Constructive"]},
            ],
            tradier_snapshot={
                "_daily_opportunity_engine": {
                    "actions": [
                        {"type": "stock_add", "ticker": "BTC", "action": "AVOID ADDING", "priority_score": 70},
                        {"type": "stock_add", "ticker": "SOFI", "action": "AVOID ADDING / REDUCE RISK", "priority_score": 72},
                        {"type": "stock_add", "ticker": "NVDA", "action": "CONSIDER ADDING", "priority_score": 90, "why": "Momentum"},
                    ]
                },
                "_pipeline_status": {"run_mode": "dev", "config_snapshot": {}},
            },
            log_lines=[],
        )

        holdings_html = html[html.index('id="holdings"'):html.index('id="potential-adds"')]
        potential_html = html[html.index('id="potential-adds"'):html.index('id="risk-review"')]
        risk_html = html[html.index('id="risk-review"'):html.index('id="blocked-calendars"')]
        self.assertNotIn("BTC", holdings_html)
        self.assertNotIn("BTC", potential_html)
        self.assertNotIn("BTC", risk_html)
        self.assertIn("NVDA", potential_html)
        self.assertNotIn("SOFI", potential_html)
        self.assertIn("SOFI", risk_html)
        self.assertIn("Copy Daily Brief", html)
        self.assertIn("Download Full Debug Payload", html)
        self.assertIn("copyTextWithFallback", html)

    def test_provider_status_and_active_calendar_deep_itm_warning(self):
        html = format_html(
            payload="sample",
            positions=[],
            news_map={},
            recommendations=[],
            tradier_snapshot={
                "_calendar_lifecycle_checks": {
                    "checks": [
                        {
                            "ticker": "AVGO",
                            "action": "URGENT REVIEW / EXIT CHECK",
                            "structure": "430 CALL | short 2026-06-05 / long 2026-06-12",
                            "current_debit": 3.38,
                            "entry_debit_estimate": 3.19,
                            "estimated_pnl_pct": 5.8,
                            "estimated_pnl_dollars": 18,
                            "target_debit": 4.79,
                            "stop_debit": 2.07,
                            "underlying_price": 487.99,
                            "short_dte": 3,
                            "short_strike": 430,
                            "short_moneyness_pct": 13.5,
                            "assignment_risk_level": "High",
                            "hold_through_score": 46.0,
                            "hold_through_action": "CONSIDER CLOSING BEFORE EARNINGS",
                            "pricing_warnings": ["leg_side_inferred"],
                        }
                    ]
                },
                "_pipeline_status": {
                    "run_mode": "dev",
                    "config_snapshot": {
                        "has_finnhub_api_key": True,
                        "has_tradier_access_token": True,
                        "has_alpha_vantage_api_key": True,
                    },
                },
            },
            log_lines=["Finnhub stock/candle returned HTTP 403 Forbidden; Tradier fallback active."],
        )

        self.assertIn("FINNHUB", html)
        self.assertIn("CANDLES BLOCKED", html)
        self.assertIn("TRADIER", html)
        self.assertIn("FALLBACK ACTIVE", html)
        self.assertIn("SHORT LEG DEEP ITM - CLOSE / ROLL REVIEW REQUIRED", html)
        self.assertIn("leg_side_inferred", html)
        self.assertIn("3.38", html)
        self.assertIn("3.19", html)

    def test_blocked_candidate_guardrail_wording_is_not_actionable(self):
        html = format_html(
            payload="sample",
            positions=[],
            news_map={},
            recommendations=[],
            tradier_snapshot={
                "_unified_calendar_trade_engine": {
                    "new_trade_rows": [
                        {
                            "ticker": "ADBE",
                            "verdict": "FAIL / DEBIT TOO LARGE",
                            "trade_type_label": "PRE-EARNINGS FINANCING / LONG-VOL TRADE",
                            "main_blocker": "debit too large for account",
                            "debit": 15.0,
                            "backtest_status": "skipped_untradeable",
                        }
                    ]
                },
                "_pipeline_status": {"run_mode": "dev", "config_snapshot": {}},
            },
            log_lines=[],
        )

        blocked_html = html[html.index('id="blocked-calendars"'):html.index('id="portfolio-infographic"')]
        self.assertIn("FAIL / DEBIT TOO LARGE", blocked_html)
        self.assertIn("PRE-EARNINGS FINANCING / LONG-VOL TRADE", blocked_html)
        self.assertIn("Why not actionable", blocked_html)
        self.assertIn("Research-only", blocked_html)
        self.assertIn("Not eligible - options market untradeable", blocked_html)


if __name__ == "__main__":
    unittest.main()
