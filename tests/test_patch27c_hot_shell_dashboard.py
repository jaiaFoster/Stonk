import unittest
from unittest.mock import patch

from app.main import app
from app.services.report_service import format_html


def _snapshot():
    return {
        "_daily_opportunity_engine": {
            "actions": [
                {
                    "type": "stock_add",
                    "ticker": ticker,
                    "action": "CONSIDER ADDING",
                    "priority_score": 90 - index,
                    "why": "Constructive momentum",
                    "next_step": "Review entry.",
                }
                for index, ticker in enumerate(("NVDA", "AMZN", "MSFT", "GOOGL", "META", "AVGO"))
            ]
        },
        "_stock_momentum_strategy": {
            "items": [
                {"ticker": "NVDA", "action": "CONSIDER ADDING", "score": 90, "reason": "Constructive"}
            ]
        },
        "_strategy_results": {
            "earnings_calendar": {"pass_count": 1, "watch_count": 2, "fail_count": 3},
            "forward_factor_calendar": {"enabled": True, "pass_count": 0, "watch_count": 1, "fail_count": 2},
        },
        "_skew_momentum_vertical_strategy": {
            "enabled": True,
            "summary": {"pass_count": 0, "watch_count": 2, "blocked_count": 1},
        },
        "_runtime_profile": {"total_ms": 57_000},
        "_payload_size_profile": {"sections_bytes": {"tradier_snapshot": 2_000_000}},
        "_pipeline_status": {"run_mode": "dev", "config_snapshot": {}},
    }


class Patch27CHotShellDashboardTests(unittest.TestCase):
    def test_shell_is_compact_and_full_report_remains_available(self):
        shell = format_html("payload", [], {}, [], _snapshot(), [], view="shell")
        full = format_html("payload", [], {}, [], _snapshot(), [], view="full")

        self.assertIn('data-dashboard-view="shell"', shell)
        self.assertIn("Portfolio Status", shell)
        self.assertIn("Daily Opportunity", shell)
        self.assertIn("Top Actionable Adds", shell)
        self.assertIn("Urgent Risk Review", shell)
        self.assertIn("Strategy Summary", shell)
        self.assertIn("Open Full Report", shell)
        self.assertIn("Heavy run:", shell)
        self.assertNotIn("Monitor / Debug", shell)
        self.assertNotIn("Forward Factor Calendar Candidates", shell)
        self.assertNotIn("Holdings / Portfolio Advisor", shell)

        self.assertIn('data-dashboard-view="full"', full)
        self.assertIn("Monitor / Debug", full)
        self.assertIn("Forward Factor Calendar Candidates", full)
        self.assertIn("Holdings / Portfolio Advisor", full)
        self.assertIn("Compact Dashboard", full)

    def test_shell_limits_daily_rows_but_keeps_total_count(self):
        shell = format_html("payload", [], {}, [], _snapshot(), [], view="shell")
        daily = shell[shell.index('id="daily-opportunity"'):shell.index('id="potential-adds"')]
        self.assertIn("NVDA", daily)
        self.assertIn("META", daily)
        self.assertNotIn("AVGO", daily)
        self.assertIn("COUNT", daily)
        self.assertIn("6", daily)

    def test_route_dashboard_view_defaults_to_shell_and_accepts_full(self):
        from app import config
        from app import main

        with app.test_request_context("/?token=x"):
            with patch.object(config, "DASHBOARD_DEFAULT_VIEW", "shell"):
                self.assertEqual(main._requested_dashboard_view(), "shell")
        with app.test_request_context("/?token=x&view=full"):
            self.assertEqual(main._requested_dashboard_view(), "full")
        with app.test_request_context("/?token=x&detail=full"):
            self.assertEqual(main._requested_dashboard_view(), "full")


if __name__ == "__main__":
    unittest.main()
