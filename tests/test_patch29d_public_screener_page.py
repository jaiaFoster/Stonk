from __future__ import annotations

from unittest.mock import patch


def _client():
    from app.main import app
    app.config["TESTING"] = True
    return app.test_client()


def _row(
    ticker: str,
    verdict: str,
    score: float,
    **extra,
):
    row = {
        "ticker": ticker,
        "verdict": verdict,
        "verdict_tier": 100 if verdict.startswith("PASS") else 80 if verdict.startswith("WATCH") else 35,
        "score": score,
        "raw": {"ticker": ticker, "verdict": verdict, "score": score},
    }
    row.update(extra)
    return row


def _core_data():
    snapshot = {"run_id": "run-public-1", "completed_at": "2026-07-04T22:55:05.764810+00:00"}
    report = {
        "positions": [{"ticker": "SECRET", "account": "roth", "account_number": "1234"}],
        "tradier_snapshot": {
            "_pipeline_status": {"report_quality": "SUCCESS_COMPLETE"},
            "_strategy_results": {
                "stock_momentum": {
                    "pass_count": 1,
                    "watch_count": 1,
                    "fail_count": 1,
                    "canonical_opportunities": [
                        _row("ALGN", "CONSIDER ADDING", 82.4, why="Strong momentum, trend confirmation, acceptable risk filters."),
                        _row("MU", "WATCH / CONFIRM TREND", 71.0, notes="Needs confirmation before entry."),
                        _row("ELF", "FAIL / OVEREXTENDED", 88.0, blocking_reason="Poor risk/reward after extension."),
                    ],
                },
                "forward_factor_calendar": {
                    "pass_count": 0,
                    "watch_count": 1,
                    "fail_count": 1,
                    "canonical_opportunities": [
                        _row(
                            "SBUX",
                            "WATCH / EX-EARNINGS IV UNAVAILABLE",
                            89.0,
                            diagnostic_raw_iv_forward_factor=0.968,
                            front_dte=60,
                            back_dte=90,
                            raw={
                                "ticker": "SBUX",
                                "verdict": "WATCH / EX-EARNINGS IV UNAVAILABLE",
                                "diagnostic_raw_iv_forward_factor": 0.968,
                                "is_diagnostic_only": True,
                                "can_enter_daily_opportunity": False,
                            },
                            why="Forward volatility appears cheap relative to front volatility.",
                        ),
                        _row(
                            "ELF",
                            "FAIL / OPTIONS ILLIQUID",
                            91.0,
                            blocking_reasons=["Package slippage too wide."],
                            raw={"ticker": "ELF", "verdict": "FAIL / OPTIONS ILLIQUID"},
                        ),
                    ],
                },
                "earnings_calendar": {
                    "pass_count": 1,
                    "watch_count": 0,
                    "fail_count": 1,
                    "canonical_opportunities": [
                        _row("JPM", "PASS / POSSIBLE ENTRY SETUP", 74.0, display_reason="Event volatility and expiration spacing line up."),
                        _row("CAG", "FAIL / NO VALID CALENDAR STRUCTURE", 95.0, primary_reason="Interesting event setup, but expiration pair failed."),
                    ],
                },
                "skew_momentum_vertical": {
                    "pass_count": 0,
                    "watch_count": 1,
                    "fail_count": 1,
                    "canonical_opportunities": [
                        _row("AMZN", "WATCH / SKEW NOT RICH ENOUGH", 70.0, raw={"ticker": "AMZN", "verdict": "WATCH / SKEW NOT RICH ENOUGH", "direction": "Bullish"}),
                        _row("NVDA", "FAIL / DTE TOO SHORT", 87.0, gate_failures=["Front leg too short."]),
                    ],
                },
            },
        },
    }
    tradier = report["tradier_snapshot"]
    return snapshot, report, tradier


class TestPublicScreener:
    def setup_method(self):
        self.client = _client()

    def test_screener_returns_200_when_snapshot_exists(self):
        with patch("app.main._load_dashboard_core_report", return_value=_core_data()), \
             patch("app.config.PUBLIC_SCREENER_ENABLED", True):
            resp = self.client.get("/screener")
        assert resp.status_code == 200

    def test_demo_screener_redirects(self):
        with patch("app.config.PUBLIC_SCREENER_ENABLED", True):
            resp = self.client.get("/demo/screener", follow_redirects=False)
        assert resp.status_code in {301, 302}
        assert resp.headers["Location"].endswith("/screener")

    def test_page_includes_strategy_education_copy(self):
        with patch("app.main._load_dashboard_core_report", return_value=_core_data()), \
             patch("app.config.PUBLIC_SCREENER_ENABLED", True):
            html = self.client.get("/screener").get_data(as_text=True)
        assert "Today&apos;s Options &amp; Stock Screener" in html
        assert "<strong>PASS</strong><p>Setup meets strategy&apos;s current rules.</p>" in html
        assert "A failed setup is not wasted." in html
        assert "ASA is research and decision-support tool." in html

    def test_page_includes_all_four_strategy_sections(self):
        with patch("app.main._load_dashboard_core_report", return_value=_core_data()), \
             patch("app.config.PUBLIC_SCREENER_ENABLED", True):
            html = self.client.get("/screener").get_data(as_text=True)
        assert "Stock Momentum" in html
        assert "Forward Factor Calendar" in html
        assert "Earnings Calendar" in html
        assert "Skew Momentum Verticals" in html

    def test_page_includes_pass_watch_and_failed_rows(self):
        with patch("app.main._load_dashboard_core_report", return_value=_core_data()), \
             patch("app.config.PUBLIC_SCREENER_ENABLED", True):
            html = self.client.get("/screener").get_data(as_text=True)
        assert "ALGN" in html
        assert "CONSIDER ADDING" in html
        assert "MU" in html
        assert "WATCH / CONFIRM TREND" in html
        assert "Rejected by Risk Filters" in html
        assert "CAG" in html
        assert "Interesting event setup, but expiration pair failed." in html

    def test_page_includes_ff_dry_run_label(self):
        with patch("app.main._load_dashboard_core_report", return_value=_core_data()), \
             patch("app.config.PUBLIC_SCREENER_ENABLED", True), \
             patch("app.config.FORWARD_FACTOR_DRY_RUN", True):
            html = self.client.get("/screener").get_data(as_text=True)
        assert "Forward Factor: DRY RUN" in html
        assert "Research Only" in html

    def test_page_does_not_include_private_portfolio_data(self):
        with patch("app.main._load_dashboard_core_report", return_value=_core_data()), \
             patch("app.config.PUBLIC_SCREENER_ENABLED", True):
            html = self.client.get("/screener").get_data(as_text=True)
        assert "Open Positions" not in html
        assert "account value" not in html.lower()
        assert "1234" not in html
        assert "broker auth status" not in html.lower()
        assert "user_run_id" not in html
        assert "Robinhood" not in html

    def test_page_handles_missing_reasons_gracefully(self):
        snapshot, report, tradier = _core_data()
        report["tradier_snapshot"]["_strategy_results"]["stock_momentum"]["canonical_opportunities"] = [
            {
                "ticker": "UNKNOWN",
                "verdict": "UNKNOWN",
                "score": 90.0,
                "raw": {"ticker": "NVDA", "verdict": "PASS / STOCK MOMENTUM"},
            }
        ]
        with patch("app.main._load_dashboard_core_report", return_value=(snapshot, report, tradier)), \
             patch("app.config.PUBLIC_SCREENER_ENABLED", True):
            html = self.client.get("/screener").get_data(as_text=True)
        assert "NVDA" in html
        assert "PASS / STOCK MOMENTUM" in html
        assert "No detailed reason available." in html
        assert "Needs Review" not in html
