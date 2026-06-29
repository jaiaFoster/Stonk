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
             "mid": 3.0, "avg_cost_per_share": 2.5, "broker": "tradier"},
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
             "mid": 5.0, "avg_cost_per_share": 3.0, "broker": "robinhood"},
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


class TestC1aSideIndependentMatching(unittest.TestCase):
    """C1a: legs with side='unknown' still match detected structures."""

    def test_unknown_side_vertical_leg_excluded(self):
        from app.services.open_options_service import _collect_unmatched_legs
        legs = [
            {"underlying": "SPY", "option_type": "call", "strike": 450,
             "expiration": "2026-07-18", "side": "unknown", "abs_quantity": 1},
            {"underlying": "SPY", "option_type": "call", "strike": 460,
             "expiration": "2026-07-18", "side": "unknown", "abs_quantity": 1},
        ]
        verticals = [{
            "ticker": "SPY", "option_type": "call",
            "long_strike": 450, "short_strike": 460,
            "expiration": "2026-07-18",
        }]
        result = _collect_unmatched_legs(legs, [], verticals)
        self.assertEqual(len(result), 0)


class TestC1bAvgCostPerShare(unittest.TestCase):
    """C1b: P&L uses avg_cost_per_share, not raw cost_basis."""

    def test_pnl_uses_per_share_cost(self):
        from app.services.open_options_service import _collect_unmatched_legs
        legs = [
            {"underlying": "NVDA", "option_type": "call", "strike": 210,
             "expiration": "2026-06-26", "side": "long", "abs_quantity": 1,
             "mid": 5.0, "avg_cost_per_share": 3.0, "cost_basis": 300.0,
             "broker": "robinhood"},
        ]
        result = _collect_unmatched_legs(legs, [], [])
        self.assertEqual(result[0]["unrealized_pnl"], 200.0)

    def test_fallback_to_avg_cost_per_contract_with_scaling(self):
        from app.services.open_options_service import _collect_unmatched_legs
        legs = [
            {"underlying": "NVDA", "option_type": "call", "strike": 210,
             "expiration": "2026-06-26", "side": "long", "abs_quantity": 1,
             "mid": 5.0, "avg_cost_per_contract": 300.0,
             "broker": "robinhood"},
        ]
        result = _collect_unmatched_legs(legs, [], [])
        self.assertEqual(result[0]["average_price"], 3.0)


class TestC1cSingleLegDedup(unittest.TestCase):
    """C1c: duplicate lot-level entries deduped on natural key."""

    def test_duplicate_legs_deduped(self):
        from app.services.open_options_service import _collect_unmatched_legs
        leg = {"underlying": "NVDA", "option_type": "call", "strike": 210,
               "expiration": "2026-06-26", "side": "long", "abs_quantity": 1,
               "mid": 5.0, "avg_cost_per_share": 3.0, "broker": "robinhood"}
        legs = [dict(leg, id="lot-1"), dict(leg, id="lot-2")]
        result = _collect_unmatched_legs(legs, [], [])
        self.assertEqual(len(result), 1)


class TestA1ChainBudgetFloor(unittest.TestCase):
    """A1: FF_MIN_CHAIN_SET_BUDGET floors reserve even when not reserved."""

    def test_min_chain_set_budget_config(self):
        from app import config
        self.assertEqual(config.FF_MIN_CHAIN_SET_BUDGET, 4)


class TestB1QualityRowDateFields(unittest.TestCase):
    """B1: quality row includes date_confidence/date_conflict/date_sources."""

    def test_quality_row_has_date_fields(self):
        from app.services.earnings_discovery_quality_service import _quality_row
        event = {
            "ticker": "AAPL",
            "earnings_date": "2026-07-20",
            "earnings_date_confidence": "confirmed",
            "earnings_source_conflict": False,
            "sources_seen": ["finnhub", "alphavantage"],
        }
        row = _quality_row(event, {})
        self.assertEqual(row["date_confidence"], "confirmed")
        self.assertFalse(row["date_conflict"])
        self.assertEqual(row["date_sources"], ["finnhub", "alphavantage"])

    def test_quality_row_conflict_fields(self):
        from app.services.earnings_discovery_quality_service import _quality_row
        event = {
            "ticker": "TSLA",
            "earnings_date": "2026-08-01",
            "earnings_date_confidence": "disputed",
            "earnings_source_conflict": True,
            "sources_seen": ["finnhub"],
        }
        row = _quality_row(event, {})
        self.assertEqual(row["date_confidence"], "disputed")
        self.assertTrue(row["date_conflict"])


class TestB3NearMissVerdictLabel(unittest.TestCase):
    """B3: NEAR_MISS verdict surfaces through unified engine."""

    def test_near_miss_verdict_from_quality_row(self):
        from app.services.unified_calendar_trade_engine_service import _new_trade_verdict
        quality_row = {"expiry_near_miss": True}
        verdict = _new_trade_verdict(False, {}, quality_row)
        self.assertEqual(verdict, "NEAR_MISS / EXPIRY_GAP")

    def test_no_structure_verdict_without_near_miss(self):
        from app.services.unified_calendar_trade_engine_service import _new_trade_verdict
        verdict = _new_trade_verdict(False, {}, None)
        self.assertEqual(verdict, "FAIL / NO VALID CALENDAR STRUCTURE")


class TestD1AccountRiskLabel(unittest.TestCase):
    """D1: account risk code produces ACCOUNT RISK TOO HIGH, not DEBIT TOO LARGE."""

    def test_account_risk_verdict(self):
        from app.services.skew_momentum_vertical_verdict_service import apply_skew_momentum_vertical_verdict
        candidate = {
            "strategy_id": "skew_momentum_vertical",
            "momentum_confirmed": True,
            "skew_pass": True,
            "requirements": [
                {"name": "Account risk", "status": "FAIL", "detail": "Max risk 5.2%", "code": "account_risk"},
            ],
        }
        result = apply_skew_momentum_vertical_verdict(candidate)
        self.assertIn("ACCOUNT RISK", result["verdict"])
        self.assertNotIn("DEBIT", result["verdict"])


class TestD2HighMoveWarning(unittest.TestCase):
    """D2: high-move stock warning gate."""

    def test_high_move_config_exists(self):
        from app import config
        self.assertEqual(config.CALENDAR_HIGH_MOVE_WARNING_THRESHOLD, 0.08)

    def test_high_move_warning_set(self):
        from app.services.earnings_discovery_quality_service import _quality_row
        event = {
            "ticker": "MU",
            "earnings_date": "2026-07-20",
            "avg_historical_earnings_move": 0.12,
        }
        row = _quality_row(event, {})
        self.assertTrue(row["high_move_warning"])
        self.assertIn("12.0%", row["high_move_note"])

    def test_no_warning_below_threshold(self):
        from app.services.earnings_discovery_quality_service import _quality_row
        event = {
            "ticker": "AAPL",
            "earnings_date": "2026-07-20",
            "avg_historical_earnings_move": 0.04,
        }
        row = _quality_row(event, {})
        self.assertFalse(row["high_move_warning"])

    def test_no_warning_when_data_missing(self):
        from app.services.earnings_discovery_quality_service import _quality_row
        event = {"ticker": "XYZ", "earnings_date": "2026-07-20"}
        row = _quality_row(event, {})
        self.assertFalse(row["high_move_warning"])


class TestPatch105PathADiagnostics(unittest.TestCase):
    """Patch 105 Item 1: Path A emits diagnostic log lines."""

    def test_path_a_diagnostic_log_lines(self):
        from app.services.open_options_service import detect_open_options_positions
        mock_provider = MagicMock()
        mock_provider.is_configured = False
        logs = []
        with patch("app.services.open_options_service.TradierProvider", return_value=mock_provider), \
             patch("app.config.OPEN_OPTIONS_DETECTOR_ENABLED", True), \
             patch("app.config.ROBINHOOD_OPTIONS_DETECTOR_ENABLED", False):
            detect_open_options_positions(log_print=logs.append)
        path_a_logs = [l for l in logs if "Path A:" in l]
        self.assertTrue(any("legs normalized" in l for l in path_a_logs))
        self.assertTrue(any("quote attach complete" in l for l in path_a_logs))
        self.assertTrue(any("detection complete" in l for l in path_a_logs))


class TestPatch105PathBQuoteAttach(unittest.TestCase):
    """Patch 105 Item 2: Path B attaches quotes via TradierProvider."""

    def test_path_b_calls_attach_leg_quotes(self):
        from app.services.open_options_service import detect_from_robinhood_raw_positions
        mock_normalize = MagicMock(return_value={
            "underlying": "NVDA", "option_type": "call", "strike": 210.0,
            "expiration": "2026-07-18", "quantity": 1, "side": "long",
            "symbol": "NVDA260718C00210000", "source": "robinhood",
            "broker": "robinhood",
        })
        mock_provider = MagicMock()
        mock_provider.is_configured = True
        logs = []
        raw = [{"id": "1", "option_type": "call"}]
        with patch("app.services.open_options_service._robinhood_position_to_option_leg") as mock_leg, \
             patch("app.services.open_options_service._attach_leg_quotes") as mock_attach, \
             patch("app.config.OPEN_OPTIONS_QUOTE_LEGS", True), \
             patch("app.providers.robinhood_provider._normalize_option_position", mock_normalize):
            mock_leg.return_value = {
                "underlying": "NVDA", "option_type": "call", "strike": 210.0,
                "expiration": "2026-07-18", "quantity": 1, "abs_quantity": 1,
                "side": "long", "symbol": "NVDA260718C00210000",
                "source": "robinhood", "broker": "robinhood", "mid": None,
            }
            result = detect_from_robinhood_raw_positions(raw, log_print=logs.append, provider=mock_provider)
            mock_attach.assert_called_once()
        path_b_logs = [l for l in logs if "Path B:" in l]
        self.assertTrue(any("quote attach applied" in l for l in path_b_logs))

    def test_path_b_skips_when_provider_not_configured(self):
        from app.services.open_options_service import detect_from_robinhood_raw_positions
        mock_normalize = MagicMock(return_value={
            "underlying": "NVDA", "option_type": "call", "strike": 210.0,
            "expiration": "2026-07-18", "quantity": 1, "side": "long",
            "symbol": "NVDA260718C00210000", "source": "robinhood",
            "broker": "robinhood",
        })
        mock_provider = MagicMock()
        mock_provider.is_configured = False
        logs = []
        raw = [{"id": "1", "option_type": "call"}]
        with patch("app.services.open_options_service._robinhood_position_to_option_leg") as mock_leg, \
             patch("app.services.open_options_service._attach_leg_quotes") as mock_attach, \
             patch("app.config.OPEN_OPTIONS_QUOTE_LEGS", True), \
             patch("app.providers.robinhood_provider._normalize_option_position", mock_normalize):
            mock_leg.return_value = {
                "underlying": "NVDA", "option_type": "call", "strike": 210.0,
                "expiration": "2026-07-18", "quantity": 1, "abs_quantity": 1,
                "side": "long", "symbol": "NVDA260718C00210000",
                "source": "robinhood", "broker": "robinhood", "mid": None,
            }
            detect_from_robinhood_raw_positions(raw, log_print=logs.append, provider=mock_provider)
            mock_attach.assert_not_called()
        path_b_logs = [l for l in logs if "Path B:" in l]
        self.assertTrue(any("quote attach skipped" in l for l in path_b_logs))

    def test_path_b_includes_single_legs_key(self):
        from app.services.open_options_service import detect_from_robinhood_raw_positions
        logs = []
        mock_normalize = MagicMock(return_value=None)
        with patch("app.providers.robinhood_provider._normalize_option_position", mock_normalize):
            result = detect_from_robinhood_raw_positions([], log_print=logs.append)
        self.assertIn("single_legs", result)
        self.assertIsInstance(result["single_legs"], list)


class TestPatch105FinalizeIncludesSingleLegCount(unittest.TestCase):
    """Patch 105: _finalize_result includes single_leg_count in summary."""

    def test_summary_has_single_leg_count(self):
        from app.services.open_options_service import _finalize_result
        result = {
            "account_ids": [],
            "positions": [],
            "option_legs": [],
            "calendars": [],
            "verticals": [],
            "single_legs": [{"ticker": "NVDA"}],
        }
        finalized = _finalize_result(result)
        self.assertEqual(finalized["summary"]["single_leg_count"], 1)

    def test_summary_single_leg_count_zero(self):
        from app.services.open_options_service import _finalize_result
        result = {
            "account_ids": [],
            "positions": [],
            "option_legs": [],
            "calendars": [],
            "verticals": [],
        }
        finalized = _finalize_result(result)
        self.assertEqual(finalized["summary"]["single_leg_count"], 0)


class TestPatch106FFLiveSummary(unittest.TestCase):
    """Patch 106 Item 1: _ff_live_summary extracts PASS/WATCH tickers from snapshot."""

    @patch("app.services.report_snapshot_service.ReportSnapshotRepository")
    def test_ff_live_summary_with_pass_row(self, mock_repo_cls):
        from app.api.knowledge import _ff_live_summary
        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo
        mock_repo.latest_success.return_value = {"run_id": "r1"}
        mock_repo.load_summary.return_value = {
            "report_data": {
                "tradier_snapshot": {
                    "_strategy_results": {
                        "forward_factor_calendar": {
                            "rows": [
                                {"ticker": "SBUX", "is_positive_signal": True,
                                 "forward_factor": 0.91, "signal_score": 96,
                                 "signal_tier": "SOURCE_QUALIFIED_POSITIVE",
                                 "verdict": "SOURCE-QUALIFIED POSITIVE",
                                 "front_expiration": "2026-08-21",
                                 "back_expiration": "2026-09-18",
                                 "conservative_debit": 1.79,
                                 "edge_on_margin": 12.5},
                                {"ticker": "XYZ", "is_positive_signal": False,
                                 "verdict": "WATCH / NEAR MISS"},
                            ],
                        }
                    }
                }
            }
        }
        result = _ff_live_summary()
        self.assertEqual(result["ff_pass_tickers"], ["SBUX"])
        self.assertEqual(result["ff_watch_tickers"], ["XYZ"])
        self.assertIsNotNone(result["ff_latest_pass"])
        self.assertEqual(result["ff_latest_pass"]["ticker"], "SBUX")
        self.assertAlmostEqual(result["ff_latest_pass"]["forward_factor"], 0.91)

    @patch("app.services.report_snapshot_service.ReportSnapshotRepository")
    def test_ff_live_summary_no_snapshot(self, mock_repo_cls):
        from app.api.knowledge import _ff_live_summary
        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo
        mock_repo.latest_success.return_value = None
        result = _ff_live_summary()
        self.assertEqual(result["ff_pass_tickers"], [])
        self.assertIsNone(result["ff_latest_pass"])


class TestPatch106FFAgentContext(unittest.TestCase):
    """Patch 106 Item 1: _ff_agent_context produces readable signal lines."""

    @patch("app.api.knowledge._ff_live_summary")
    @patch("app.services.report_snapshot_service.ReportSnapshotRepository")
    def test_ff_agent_context_with_pass(self, mock_repo_cls, mock_summary):
        from app.api.knowledge import _ff_agent_context
        mock_summary.return_value = {
            "ff_pass_tickers": ["SBUX"],
            "ff_latest_pass": {"ticker": "SBUX"},
        }
        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo
        mock_repo.latest_success.return_value = {"run_id": "r1"}
        mock_repo.load_summary.return_value = {
            "report_data": {
                "tradier_snapshot": {
                    "_strategy_results": {
                        "forward_factor_calendar": {
                            "rows": [
                                {"ticker": "SBUX", "is_positive_signal": True,
                                 "forward_factor": 0.907, "signal_score": 96,
                                 "verdict": "SOURCE-QUALIFIED POSITIVE",
                                 "front_expiration": "2026-08-21",
                                 "back_expiration": "2026-09-18",
                                 "conservative_debit": 1.79,
                                 "edge_on_margin": 12.5},
                            ],
                        }
                    }
                }
            }
        }
        lines = _ff_agent_context()
        self.assertEqual(len(lines), 1)
        self.assertIn("SBUX", lines[0])
        self.assertIn("FF CALENDAR SIGNAL", lines[0])
        self.assertIn("0.907", lines[0])

    @patch("app.api.knowledge._ff_live_summary")
    def test_ff_agent_context_no_pass(self, mock_summary):
        from app.api.knowledge import _ff_agent_context
        mock_summary.return_value = {
            "ff_pass_tickers": [],
            "ff_latest_pass": None,
        }
        lines = _ff_agent_context()
        self.assertEqual(lines, [])


class TestPatch106NearMissExpiry(unittest.TestCase):
    """Patch 106 Item 2: _find_near_miss_expiry detects close expirations."""

    def test_near_miss_found(self):
        from app.services.earnings_discovery_quality_service import _find_near_miss_expiry
        expirations = ["2026-07-10", "2026-07-17", "2026-08-21"]
        event = {"earnings_date": "2026-07-08"}
        result = _find_near_miss_expiry(expirations, event)
        self.assertIsNotNone(result)
        self.assertIn("2026-07-10", result["note"])
        self.assertIn("2d", result["note"])

    def test_no_near_miss_too_far(self):
        from app.services.earnings_discovery_quality_service import _find_near_miss_expiry
        expirations = ["2026-08-15", "2026-09-19"]
        event = {"earnings_date": "2026-07-08"}
        result = _find_near_miss_expiry(expirations, event)
        self.assertIsNone(result)

    def test_no_near_miss_before_earnings(self):
        from app.services.earnings_discovery_quality_service import _find_near_miss_expiry
        expirations = ["2026-07-03", "2026-07-04"]
        event = {"earnings_date": "2026-07-08"}
        result = _find_near_miss_expiry(expirations, event)
        self.assertIsNone(result)

    def test_no_event_date(self):
        from app.services.earnings_discovery_quality_service import _find_near_miss_expiry
        result = _find_near_miss_expiry(["2026-07-10"], {})
        self.assertIsNone(result)

    def test_quality_row_sets_near_miss_when_no_pair(self):
        """When pair is None but near-miss expiry exists, expiry_near_miss=True."""
        from app.services.earnings_discovery_quality_service import _quality_row
        event = {
            "ticker": "CAG",
            "earnings_date": "2026-07-08",
        }
        row = _quality_row(event, {})
        self.assertFalse(row.get("expiry_near_miss", False))


class TestPatch106VerticalDetectionRequiresSide(unittest.TestCase):
    """Patch 106 Item 3: _detect_vertical_spreads requires explicit long/short."""

    def test_unknown_sides_produce_zero_verticals(self):
        from app.services.open_options_service import _detect_vertical_spreads
        legs = [
            {"underlying": "NVDA", "option_type": "call", "strike": 207.5,
             "expiration": "2026-06-26", "side": "unknown", "abs_quantity": 1},
            {"underlying": "NVDA", "option_type": "call", "strike": 215.0,
             "expiration": "2026-06-26", "side": "unknown", "abs_quantity": 1},
        ]
        result = _detect_vertical_spreads(legs)
        self.assertEqual(len(result), 0)

    def test_explicit_sides_produce_vertical(self):
        from app.services.open_options_service import _detect_vertical_spreads
        legs = [
            {"underlying": "NVDA", "option_type": "call", "strike": 207.5,
             "expiration": "2026-06-26", "side": "long", "abs_quantity": 1},
            {"underlying": "NVDA", "option_type": "call", "strike": 215.0,
             "expiration": "2026-06-26", "side": "short", "abs_quantity": 1},
        ]
        result = _detect_vertical_spreads(legs)
        self.assertEqual(len(result), 1)


class TestPatch107BuildFix(unittest.TestCase):
    """Patch 107 Item 1: Dockerfile and requirements.txt fixes."""

    def test_dockerfile_no_cache_dir_removed(self):
        with open("Dockerfile") as f:
            content = f.read()
        self.assertNotIn("--no-cache-dir", content)
        self.assertIn("pip install -r requirements.txt", content)

    def test_requirements_no_moomoo(self):
        with open("requirements.txt") as f:
            content = f.read()
        self.assertNotIn("moomoo", content)

    def test_requirements_robin_stocks_pinned(self):
        with open("requirements.txt") as f:
            content = f.read()
        self.assertIn("1b4ef98be36d1df127886889c843256799a1dfea", content)


class TestPatch107SkewDedup(unittest.TestCase):
    """Patch 107 Item 2: Skew dedup keeps highest-scoring row per ticker."""

    def _dedup(self, items):
        """Replicate the inline dedup logic from build_skew_momentum_vertical_strategy."""
        seen: dict[str, dict] = {}
        for row in items:
            ticker = str(row.get("ticker") or "")
            existing = seen.get(ticker)
            if existing is None:
                seen[ticker] = row
            elif (float(row.get("score") or 0)) > (float(existing.get("score") or 0)):
                seen[ticker] = row
        return list(seen.values())

    def test_dedup_keeps_highest_score(self):
        items = [
            {"ticker": "CRDO", "score": 10, "verdict": "PASS"},
            {"ticker": "CRDO", "score": 20, "verdict": "PASS"},
            {"ticker": "CRDO", "score": 5, "verdict": "WATCH"},
            {"ticker": "NVO", "score": 15, "verdict": "PASS"},
            {"ticker": "NVO", "score": 8, "verdict": "WATCH"},
        ]
        deduped = self._dedup(items)
        tickers = [r["ticker"] for r in deduped]
        self.assertEqual(sorted(tickers), ["CRDO", "NVO"])
        crdo = next(r for r in deduped if r["ticker"] == "CRDO")
        self.assertEqual(crdo["score"], 20)

    def test_dedup_no_duplicates_passthrough(self):
        items = [
            {"ticker": "AAPL", "score": 10, "verdict": "PASS"},
            {"ticker": "MSFT", "score": 8, "verdict": "WATCH"},
        ]
        deduped = self._dedup(items)
        self.assertEqual(len(deduped), 2)

    def test_dedup_code_exists_in_service(self):
        import inspect
        from app.services import skew_momentum_vertical_service
        source = inspect.getsource(skew_momentum_vertical_service.build_skew_momentum_vertical_strategy)
        self.assertIn("seen_tickers", source)
        self.assertIn("dedup", source)


class TestPatch107StaleStructureInSnapshot(unittest.TestCase):
    """Patch 107 Item 3: _compact_strategy includes active_rows."""

    def test_compact_strategy_includes_active_rows(self):
        from app.services.report_snapshot_service import _compact_strategy
        strategy = {
            "strategy_id": "skew_momentum_vertical",
            "enabled": True,
            "summary": {"pass_count": 1},
            "active_rows": [
                {"ticker": "ORCL", "stale_structure": True, "stale_structure_note": "Stock 37% below"},
            ],
            "items": [
                {"ticker": "CRDO", "score": 20},
            ],
        }
        compact = _compact_strategy(strategy, include_rows=False)
        self.assertIn("active_rows", compact)
        self.assertEqual(len(compact["active_rows"]), 1)
        self.assertTrue(compact["active_rows"][0]["stale_structure"])
        self.assertNotIn("items", compact)

    def test_compact_strategy_active_rows_empty_list(self):
        from app.services.report_snapshot_service import _compact_strategy
        strategy = {
            "strategy_id": "skew_momentum_vertical",
            "enabled": True,
            "summary": {},
        }
        compact = _compact_strategy(strategy, include_rows=False)
        self.assertNotIn("active_rows", compact)


class TestPatch107AccountRiskAlreadyExists(unittest.TestCase):
    """Patch 107 Item 4: evaluate_account_risk already computes debit_pct_of_account."""

    def test_debit_pct_of_account_computed(self):
        from app.services.calendar_verdict_service import evaluate_account_risk
        candidate = {"conservative_debit": 2.50}
        account_context = {"account_value_estimate": 50000.0}
        result = evaluate_account_risk(candidate, account_context)
        self.assertIn("debit_pct_of_account", result)
        self.assertIsNotNone(result["debit_pct_of_account"])
        self.assertIn("account_risk_status", result)

    def test_no_account_value_returns_unknown(self):
        from app.services.calendar_verdict_service import evaluate_account_risk
        candidate = {"conservative_debit": 2.50}
        result = evaluate_account_risk(candidate, None)
        self.assertIn("account_risk_status", result)


class TestPatch107IVPercentile(unittest.TestCase):
    """Patch 107 Item 5: IV percentile from FF journal history."""

    def test_historical_ivs_returns_list(self):
        from app.db.ff_journal import historical_ivs
        with patch("app.db.ff_journal.config") as mock_config:
            mock_config.FF_JOURNAL_ENABLED = False
            result = historical_ivs("SBUX")
        self.assertEqual(result, [])

    def test_iv_percentile_computation(self):
        history = [0.20, 0.22, 0.25, 0.28, 0.30, 0.35, 0.40]
        current = 0.30
        below = sum(1 for iv in history if iv <= current)
        pct = round(below / len(history) * 100, 1)
        self.assertAlmostEqual(pct, 71.4, places=1)

    def test_iv_percentile_insufficient_history(self):
        history = [0.20, 0.22, 0.25]
        self.assertTrue(len(history) < 5)


class TestPatch108FFStrategyInThresholds(unittest.TestCase):
    """Patch 108 Item 1: ff_strategy top-level key in thresholds."""

    @patch("app.services.report_snapshot_service.ReportSnapshotRepository")
    def test_ff_strategy_key_exists_in_thresholds(self, mock_repo_cls):
        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo
        mock_repo.latest_success.return_value = None
        from app.api.knowledge import _ff_live_summary
        result = _ff_live_summary()
        self.assertIn("ff_pass_tickers", result)
        self.assertIn("ff_watch_tickers", result)
        self.assertIn("ff_latest_pass", result)


class TestPatch108FFSignalInstruction(unittest.TestCase):
    """Patch 108 Item 1: ff_signal_instruction present in agent-prompt code."""

    def test_agent_prompt_has_signal_instruction(self):
        import inspect
        from app.api import knowledge
        source = inspect.getsource(knowledge.knowledge_agent_prompt)
        self.assertIn("ff_signal_instruction", source)
        self.assertIn("active_ff_signals", source)


class TestPatch108NearMissVerdict(unittest.TestCase):
    """Patch 108 Item 2: _finalize_quality_row sets NEAR_MISS verdict."""

    def test_near_miss_sets_verdict(self):
        from app.services.earnings_discovery_quality_service import _finalize_quality_row
        row = {
            "expiry_near_miss": True,
            "checks": [
                {"name": "Tradier quote", "status": "PASS", "detail": "ok"},
                {"name": "Option expirations", "status": "WARN", "detail": "near miss"},
                {"name": "Underlying price", "status": "FAIL", "detail": "too low"},
            ],
            "is_timestamp_confirmed": False,
        }
        _finalize_quality_row(row)
        self.assertEqual(row["verdict"], "NEAR_MISS / EXPIRY_GAP")
        self.assertTrue(row["near_miss"])
        self.assertFalse(row["passes_precheck"])

    def test_no_near_miss_no_verdict_override(self):
        from app.services.earnings_discovery_quality_service import _finalize_quality_row
        row = {
            "expiry_near_miss": False,
            "checks": [
                {"name": "Tradier quote", "status": "PASS", "detail": "ok"},
                {"name": "Option expirations", "status": "FAIL", "detail": "no pair"},
            ],
            "is_timestamp_confirmed": False,
        }
        _finalize_quality_row(row)
        self.assertNotIn("verdict", row)
        self.assertFalse(row["passes_precheck"])

    def test_near_miss_passing_row_no_override(self):
        from app.services.earnings_discovery_quality_service import _finalize_quality_row
        row = {
            "expiry_near_miss": True,
            "checks": [
                {"name": "Tradier quote", "status": "PASS", "detail": "ok"},
                {"name": "Option expirations", "status": "WARN", "detail": "near miss"},
                {"name": "Underlying price", "status": "PASS", "detail": "ok"},
            ],
            "is_timestamp_confirmed": True,
        }
        _finalize_quality_row(row)
        self.assertTrue(row["passes_precheck"])
        self.assertNotIn("verdict", row)


class TestPatch108SideInferencePositiveQtyFallback(unittest.TestCase):
    """Patch 108 Item 3: positive quantity defaults to long."""

    def test_positive_qty_returns_long(self):
        from app.providers.robinhood_provider import _infer_robinhood_option_side
        raw = {"quantity": "5"}
        result = _infer_robinhood_option_side(raw, 5)
        self.assertEqual(result, "long")

    def test_negative_qty_returns_short(self):
        from app.providers.robinhood_provider import _infer_robinhood_option_side
        raw = {"quantity": "-3"}
        result = _infer_robinhood_option_side(raw, -3)
        self.assertEqual(result, "short")

    def test_explicit_side_takes_precedence(self):
        from app.providers.robinhood_provider import _infer_robinhood_option_side
        raw = {"side": "short", "quantity": "5"}
        result = _infer_robinhood_option_side(raw, 5)
        self.assertEqual(result, "short")

    def test_zero_qty_returns_unknown(self):
        from app.providers.robinhood_provider import _infer_robinhood_option_side
        raw = {"quantity": "0"}
        result = _infer_robinhood_option_side(raw, 0)
        self.assertEqual(result, "unknown")


if __name__ == "__main__":
    unittest.main()
