import unittest
import tempfile
from unittest.mock import patch

from app import config
from app.services.config_check_service import build_config_check
from app.services.daily_opportunity_engine_service import build_daily_opportunity_engine
from app.services.report_service import format_html, format_payload
from app.services.pipeline_helpers import config_log_lines, config_snapshot
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

    def test_visibility_exports_daily_brief_and_debug_summary_include_watch_rows(self):
        watch = {
            "ticker": "NVDA",
            "direction": "bullish",
            "verdict": "WATCH / SKEW NOT RICH ENOUGH",
            "score": 81.7,
            "dte": 22,
            "possible_spread": {"option_type": "call", "long_strike": 210, "short_strike": 230, "expiration": "2026-07-02"},
            "conservative_debit": 4.2,
            "max_profit": 1580,
            "reward_risk": 3.76,
            "short_iv_edge": 0.01,
            "short_premium_financing_pct": 15,
            "momentum_reason": "Bullish momentum confirmed.",
            "skew_reason": "Short wing financing below threshold.",
            "primary_blocker": "Short wing is not rich enough.",
            "next_action": "Wait for the short call to become richer.",
            "requirements": [],
            "provider_notes": ["Tradier option-chain quotes."],
            "risk_notes": [],
        }
        strategy = {
            "enabled": True,
            "run_mode": "dev",
            "items": [watch],
            "pass_items": [],
            "watch_items": [watch],
            "blocked_items": [],
            "active_items": [],
            "summary": {
                "enabled": True,
                "candidate_count": 1,
                "pass_count": 0,
                "watch_count": 1,
                "blocked_count": 0,
                "active_count": 0,
                "run_mode": "dev",
                "scanned_ticker_count": 3,
                "scanned_tickers": ["NVDA", "ORCL", "GOOGL"],
                "configured_max_tickers": 8,
                "runtime_ticker_cap": 3,
            },
        }
        snapshot = {
            "_skew_momentum_vertical_strategy": strategy,
            "_skew_momentum_vertical_cache": {
                "summary": {"write_count": 1, "recent_count": 1},
                "recent": [{"ticker": "NVDA", "direction": "bullish", "expiration": "2026-07-02", "final_verdict": watch["verdict"], "score": 81.7, "main_blocker": watch["primary_blocker"], "seen_count": 2, "last_seen_at": "2026-06-10"}],
            },
            "_daily_opportunity_engine": {"actions": [], "summary": {"skew_vertical_count": 0}},
            "_pipeline_status": {"run_mode": "dev", "config_snapshot": {}},
        }
        html = format_html("debug", [], {}, [], snapshot, [])
        payload = format_payload([], {}, [], snapshot)
        skew_section = html[html.index('id="skew-verticals"'):html.index('id="holdings"')]

        self.assertIn("SKEW", html)
        self.assertIn("0P · 1W · 0F", html)
        self.assertIn("1 · PASS 0 · WATCH 1 · FAIL 0 · SCANNED 3", skew_section)
        self.assertNotIn("COUNT 0", skew_section)
        self.assertIn("Dev mode: Strategy 2 scan limited to 3 tickers", skew_section)
        self.assertIn("Recent Skew Vertical Opportunities", skew_section)
        self.assertIn("Copy Skew Verticals Report", html)
        self.assertIn("Copy Options Strategies Report", html)
        self.assertIn("Skew Momentum Vertical Report", html)
        self.assertIn("0 actionable, 1 watch, 0 fail.", html)
        self.assertIn("Strategy 2 Summary", html)
        self.assertIn("SKEW MOMENTUM VERTICAL STRATEGY V1", payload)
        self.assertIn("Watch 1", payload)

    def test_top_skew_kpi_handles_zero_rows(self):
        html = format_html(
            "debug", [], {}, [],
            {"_skew_momentum_vertical_strategy": {"enabled": True, "items": [], "summary": {"enabled": True, "candidate_count": 0}}, "_pipeline_status": {"run_mode": "prod", "config_snapshot": {}}},
            [],
        )
        self.assertIn("SKEW", html)
        self.assertIn(">0</span>", html)

    def test_runtime_config_log_uses_effective_21_day_window(self):
        snapshot = config_snapshot("prod")
        lines = config_log_lines(snapshot)
        self.assertEqual(snapshot["earnings_discovery_window"], "+4..+21 days")
        self.assertIn("EARNINGS_DISCOVERY_WINDOW: +4..+21 days", lines)

    def test_config_check_discloses_stale_railway_window_override(self):
        with patch.object(config, "EARNINGS_DISCOVERY_END_DAYS_REQUESTED", 14), patch.object(config, "EARNINGS_DISCOVERY_END_DAYS", 21):
            result = build_config_check()
        self.assertTrue(result["limits"]["earnings_discovery_end_override_adjusted"])
        self.assertTrue(any("Railway requested EARNINGS_DISCOVERY_END_DAYS=14" in warning for warning in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
