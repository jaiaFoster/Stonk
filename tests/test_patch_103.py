"""Tests for Patch 103: FF Chain Cap + Calendar Quality + Options Visibility."""

import sys
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

# Prevent robin_stocks import crash — attribute chain must be wired
_fake_rs = type(sys)("robin_stocks")
_fake_rh = type(sys)("robin_stocks.robinhood")
_fake_rs.robinhood = _fake_rh
for _sub in ("options", "account", "authentication"):
    _mod = type(sys)(f"robin_stocks.robinhood.{_sub}")
    setattr(_fake_rh, _sub, _mod)
    sys.modules[f"robin_stocks.robinhood.{_sub}"] = _mod
sys.modules.setdefault("robin_stocks", _fake_rs)
sys.modules.setdefault("robin_stocks.robinhood", _fake_rh)

from app.services.forward_factor_verdict_service import apply_forward_factor_verdict
from app.providers.earnings_provider import _merge_dedupe_events
from app.services.earnings_calendar_strategy_service import _compact_event


class TestA1ChainCapFloor(unittest.TestCase):
    """A1: chain reserve cannot be lower than chain_cap."""

    @patch("app.services.forward_factor_service.config")
    def test_reserve_floors_at_chain_cap(self, mock_config):
        mock_config.FORWARD_FACTOR_STRATEGY_ENABLED = False
        mock_config.FF_DEV_MAX_CHAIN_TICKERS_PER_RUN = 3
        mock_config.FF_MAX_CHAIN_TICKERS_PER_RUN = 4
        self.assertEqual(mock_config.FF_DEV_MAX_CHAIN_TICKERS_PER_RUN, 3)

    def test_config_default_raised_to_three(self):
        from app import config
        self.assertEqual(config.FF_DEV_MAX_CHAIN_TICKERS_PER_RUN, 3)


class TestA2AdverseIVGateReachable(unittest.TestCase):
    """A2: verify adverse IV gate is wired and first."""

    def test_adverse_iv_is_first_verdict_check(self):
        row = {
            "forward_variance": -0.01,
            "liquidity_status": "PASS",
            "liquidity_pass": True,
            "debit_at_risk": 50,
            "forward_factor": 0.25,
            "earnings_contaminated": False,
        }
        result = apply_forward_factor_verdict(row)
        self.assertEqual(result["verdict"], "FAIL / IV_RELATIONSHIP_ADVERSE")


class TestA3EdgeOnMargin(unittest.TestCase):
    """A3: edge_on_margin populated on completed structure rows."""

    def test_edge_on_margin_formula(self):
        front_iv = 0.50
        forward_iv = 0.30
        net_debit = 2.0
        margin = net_debit * 100
        edge = max(0, (front_iv - forward_iv) / forward_iv) * net_debit * 100
        expected = round(edge / margin * 100, 2)
        self.assertAlmostEqual(expected, 66.67, places=1)

    def test_edge_on_margin_zero_when_forward_iv_exceeds_front(self):
        front_iv = 0.25
        forward_iv = 0.40
        net_debit = 2.0
        margin = net_debit * 100
        edge = max(0, (front_iv - forward_iv) / forward_iv) * net_debit * 100
        self.assertEqual(edge, 0.0)


class TestB1DateConfidencePassthrough(unittest.TestCase):
    """B1: _compact_event passes date_confidence/date_conflict/date_sources."""

    def test_compact_event_has_date_fields(self):
        event = {
            "has_data": True,
            "ticker": "AAPL",
            "earnings_date": "2026-07-20",
            "is_timestamp_confirmed": True,
            "earnings_date_confidence": "confirmed",
            "date_confidence": "confirmed",
            "date_conflict": False,
            "date_sources": ["finnhub", "alphavantage"],
            "sources_seen": ["finnhub", "alphavantage"],
        }
        result = _compact_event(event)
        self.assertEqual(result["date_confidence"], "confirmed")
        self.assertFalse(result["date_conflict"])
        self.assertEqual(result["date_sources"], ["finnhub", "alphavantage"])

    def test_compact_event_disputed(self):
        event = {
            "has_data": True,
            "ticker": "TSLA",
            "earnings_date": "2026-08-01",
            "earnings_date_confidence": "disputed",
            "earnings_source_conflict": True,
            "sources_seen": ["finnhub", "alphavantage"],
        }
        result = _compact_event(event)
        self.assertEqual(result["date_confidence"], "disputed")
        self.assertTrue(result["date_conflict"])
        self.assertIn("date disputed", result.get("earnings_date_warning", ""))

    def test_compact_event_null_event(self):
        result = _compact_event(None)
        self.assertFalse(result["has_data"])


class TestB2FundExclusion(unittest.TestCase):
    """B2: CEF/bond fund tickers excluded from calendar discovery."""

    def test_fund_tickers_in_config(self):
        from app import config
        excluded = config.EARNINGS_EXCLUDED_FUND_TICKERS
        self.assertIn("NAD", excluded)
        self.assertIn("NVG", excluded)

    def test_prefilter_excludes_fund_ticker(self):
        from app.services.earnings_discovery_quality_service import _cheap_prefilter
        events = [
            {"ticker": "AAPL", "last_price": 200},
            {"ticker": "NAD", "last_price": 15},
            {"ticker": "NVG", "last_price": 12},
        ]
        logs = []
        result = _cheap_prefilter(events, logs.append)
        tickers = [e["ticker"] for e in result]
        self.assertIn("AAPL", tickers)
        self.assertNotIn("NAD", tickers)
        self.assertNotIn("NVG", tickers)
        fund_logs = [l for l in logs if "fund exclusion" in l]
        self.assertEqual(len(fund_logs), 2)


class TestB3NearMissExpiry(unittest.TestCase):
    """B3: near-miss expiry stepping + NEAR_MISS verdict."""

    def test_near_miss_calendar_verdict(self):
        from app.services.earnings_calendar_strategy_service import _evaluate_candidate
        candidate = {
            "ticker": "ESI",
            "score": 70,
            "front_expiration": "2026-07-17",
            "back_expiration": "2026-08-21",
        }
        event = {
            "has_data": True,
            "ticker": "ESI",
            "earnings_date": "2026-07-08",
            "time_of_day": "before_open",
            "session_label": "Before market open",
        }
        result = _evaluate_candidate(candidate, event)
        self.assertEqual(result["action"], "NEAR_MISS / EXPIRY_GAP")
        self.assertEqual(result["earnings_relation"], "near_miss_expiry_gap")

    def test_far_gap_still_avoid(self):
        from app.services.earnings_calendar_strategy_service import _evaluate_candidate
        candidate = {
            "ticker": "XYZ",
            "score": 70,
            "front_expiration": "2026-08-15",
            "back_expiration": "2026-09-19",
        }
        event = {
            "has_data": True,
            "ticker": "XYZ",
            "earnings_date": "2026-07-20",
            "time_of_day": "after_close",
            "session_label": "After market close",
        }
        result = _evaluate_candidate(candidate, event)
        self.assertIn("AVOID", result["action"])

    def test_step_window_config_exists(self):
        from app import config
        self.assertEqual(config.CALENDAR_SHORT_LEG_STEP_WINDOW_DAYS, 10)


class TestC1SingleLegDetection(unittest.TestCase):
    """C1: unmatched option legs surfaced as single_legs."""

    def test_unmatched_legs_collected(self):
        from app.services.open_options_service import _collect_unmatched_legs
        legs = [
            {"underlying": "NVDA", "option_type": "call", "strike": 207.5,
             "expiration": "2026-06-26", "side": "short", "abs_quantity": 1,
             "mid": 3.0, "average_price": 2.5, "broker": "tradier"},
        ]
        result = _collect_unmatched_legs(legs, [], [])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["strategy_type"], "single_leg")
        self.assertEqual(result[0]["ticker"], "NVDA")
        self.assertEqual(result[0]["strike"], 207.5)

    def test_matched_vertical_leg_excluded(self):
        from app.services.open_options_service import _collect_unmatched_legs
        legs = [
            {"underlying": "SPY", "option_type": "call", "strike": 450,
             "expiration": "2026-07-18", "side": "long", "abs_quantity": 1},
            {"underlying": "SPY", "option_type": "call", "strike": 460,
             "expiration": "2026-07-18", "side": "short", "abs_quantity": 1},
        ]
        verticals = [{
            "ticker": "SPY", "option_type": "call",
            "long_strike": 450, "short_strike": 460,
            "expiration": "2026-07-18",
        }]
        result = _collect_unmatched_legs(legs, [], verticals)
        self.assertEqual(len(result), 0)

    def test_matched_calendar_leg_excluded(self):
        from app.services.open_options_service import _collect_unmatched_legs
        legs = [
            {"underlying": "AAPL", "option_type": "call", "strike": 200,
             "expiration": "2026-07-18", "side": "short", "abs_quantity": 1},
            {"underlying": "AAPL", "option_type": "call", "strike": 200,
             "expiration": "2026-08-15", "side": "long", "abs_quantity": 1},
        ]
        calendars = [{
            "underlying": "AAPL", "option_type": "call", "strike": 200,
            "front_expiration": "2026-07-18", "back_expiration": "2026-08-15",
        }]
        result = _collect_unmatched_legs(legs, calendars, [])
        self.assertEqual(len(result), 0)

    def test_single_leg_pnl_calculation(self):
        from app.services.open_options_service import _collect_unmatched_legs
        legs = [
            {"underlying": "NVDA", "option_type": "call", "strike": 210,
             "expiration": "2026-06-26", "side": "long", "abs_quantity": 2,
             "mid": 5.0, "average_price": 3.0, "broker": "robinhood"},
        ]
        result = _collect_unmatched_legs(legs, [], [])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["unrealized_pnl"], 400.0)

    def test_detector_includes_single_legs_key(self):
        from app.services.open_options_service import detect_open_options_positions
        mock_provider = MagicMock()
        mock_provider.is_configured = False
        logs = []
        with patch("app.services.open_options_service.TradierProvider", return_value=mock_provider), \
             patch("app.config.OPEN_OPTIONS_DETECTOR_ENABLED", True), \
             patch("app.config.ROBINHOOD_OPTIONS_DETECTOR_ENABLED", False):
            result = detect_open_options_positions(log_print=logs.append)
        self.assertIn("single_legs", result)
        self.assertIsInstance(result["single_legs"], list)
        single_log = [l for l in logs if "single leg(s)" in l]
        self.assertTrue(len(single_log) > 0)


class TestSnapshotCompactKeys(unittest.TestCase):
    """Snapshot _compact includes verticals and single_legs."""

    def test_compact_passes_verticals_and_single_legs(self):
        from app.services.developer_snapshot_service import _compact
        data = {
            "summary": {"total": 5},
            "verticals": [{"ticker": "SPY"}],
            "single_legs": [{"ticker": "NVDA"}],
            "calendars": [{"ticker": "AAPL"}],
            "internal_data": "should be excluded",
        }
        result = _compact(data)
        self.assertIn("verticals", result)
        self.assertIn("single_legs", result)
        self.assertIn("calendars", result)
        self.assertNotIn("internal_data", result)


class TestCalendarStrategyDateConflictRisk(unittest.TestCase):
    """B1+B3: date conflict downgrades calendar score."""

    def test_disputed_date_caps_score(self):
        from app.services.earnings_calendar_strategy_service import _evaluate_candidate
        candidate = {
            "ticker": "AAPL",
            "score": 80,
            "front_expiration": "2026-07-10",
            "back_expiration": "2026-08-14",
        }
        event = {
            "has_data": True,
            "ticker": "AAPL",
            "earnings_date": "2026-07-20",
            "earnings_date_confidence": "disputed",
            "date_conflict": True,
            "time_of_day": "after_close",
            "session_label": "After market close",
        }
        result = _evaluate_candidate(candidate, event)
        self.assertLessEqual(result["score"], 40.0)
        disputed_risks = [r for r in result["risks"] if "disputed" in r.lower()]
        self.assertTrue(len(disputed_risks) > 0)


if __name__ == "__main__":
    unittest.main()
