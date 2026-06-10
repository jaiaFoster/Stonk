import unittest
import tempfile
from unittest.mock import patch

from app import config
from app.services.config_check_service import build_config_check
from app.services.daily_opportunity_engine_service import build_daily_opportunity_engine
from app.services.report_service import format_html
from app.services.skew_momentum_vertical_verdict_service import apply_skew_momentum_vertical_verdict
from app.services.skew_momentum_vertical_cache_service import cache_skew_momentum_vertical_opportunities
from app.providers.robinhood_provider import _discover_watchlist_names


class SkewMomentumVerticalIntegrationTests(unittest.TestCase):
    def test_final_verdict_fatal_failure_overrides_high_score(self):
        row = apply_skew_momentum_vertical_verdict({
            "score": 99,
            "momentum_confirmed": True,
            "skew_pass": True,
            "requirements": [{"status": "FAIL", "code": "debit", "detail": "Debit $500 exceeds limit."}],
        })
        self.assertEqual(row["verdict"], "FAIL / DEBIT TOO LARGE")

    def test_daily_opportunity_accepts_pass_only(self):
        strategy = {
            "pass_items": [{"ticker": "CRDO", "score": 84, "verdict": "PASS / POSSIBLE ENTRY SETUP", "primary_reason": "Momentum plus skew."}],
            "watch_items": [{"ticker": "SOFI", "score": 90, "verdict": "WATCH / SKEW NOT RICH ENOUGH"}],
        }
        result = build_daily_opportunity_engine({}, {}, {}, [], skew_momentum_vertical_strategy=strategy)
        self.assertEqual([row["ticker"] for row in result["actions"]], ["CRDO"])
        self.assertEqual(result["actions"][0]["type"], "skew_vertical")

    def test_dashboard_has_strategy_two_section_and_blocker(self):
        snapshot = {
            "_skew_momentum_vertical_strategy": {
                "pass_items": [],
                "watch_items": [{
                    "ticker": "SOFI",
                    "direction": "bullish",
                    "verdict": "WATCH / SKEW NOT RICH ENOUGH",
                    "score": 68,
                    "primary_blocker": "Short wing does not provide meaningful financing.",
                    "next_action": "Wait for richer skew.",
                    "momentum_reason": "Bullish momentum confirmed.",
                    "skew_reason": "Short IV edge 0.000.",
                    "requirements": [],
                }],
                "blocked_items": [],
            },
            "_pipeline_status": {"run_mode": "dev", "config_snapshot": {}},
        }
        html = format_html("debug", [], {}, [], snapshot, [])
        self.assertIn("Skew Momentum Vertical Candidates", html)
        self.assertIn("Short wing does not provide meaningful financing.", html)
        self.assertLess(html.index("Active Calendar Lifecycle"), html.index("Skew Momentum Vertical Candidates"))
        self.assertLess(html.index("Skew Momentum Vertical Candidates"), html.index("Holdings / Portfolio Advisor"))

    def test_config_check_exposes_preflight_and_strategy_two(self):
        config_check = build_config_check()
        self.assertEqual(config_check["limits"]["earnings_discovery_window_days"], "+4..+21")
        self.assertIn("skew_vertical_dte_range", config_check["limits"])
        self.assertIn("skew_momentum_vertical", config_check["enabled_modules"])

    def test_watchlist_name_discovery_skips_missing_name_rows(self):
        names = _discover_watchlist_names(
            {"results": [{"display_name": "Growth"}, {"title": "Research"}, {"unexpected": "row"}]},
            None,
        )
        self.assertEqual(names, ["Growth", "Research"])

    def test_scanner_generated_cache_upserts_seen_count(self):
        row = {
            "ticker": "CRDO",
            "direction": "bullish",
            "verdict": "PASS / POSSIBLE ENTRY SETUP",
            "display_state": "PASSED_ENTRY_REVIEW",
            "score": 84,
            "possible_spread": {"expiration": "2026-07-17", "long_strike": 72.5, "short_strike": 80, "option_type": "call"},
        }
        with tempfile.TemporaryDirectory() as tmp, patch.object(config, "SKEW_VERTICAL_OPPORTUNITY_DB_PATH", f"{tmp}/cache.sqlite3"):
            cache_skew_momentum_vertical_opportunities([row])
            result = cache_skew_momentum_vertical_opportunities([row])
        self.assertEqual(result["recent"][0]["seen_count"], 2)


if __name__ == "__main__":
    unittest.main()
