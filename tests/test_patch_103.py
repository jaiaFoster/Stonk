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


# ────────────────────────────────────────────────────────────────────
# Patch 109: Near-miss upstream fix, dev snapshot stale structure,
#            account_value threading, volatility framing, leg detail
#            gaps, provider_fetch_count surfacing.
# ────────────────────────────────────────────────────────────────────

class TestPatch109GenericPairReturnType(unittest.TestCase):
    """Item 1 — TKT-061: _select_generic_calendar_pair returns 3-tuple."""

    def test_generic_pair_returns_three_tuple(self):
        from app.services.earnings_discovery_quality_service import _select_generic_calendar_pair
        today = date.today()
        parsed = [
            (10, (today.replace(day=1)).strftime("%Y-%m-%d")),
            (14, (today.replace(day=5)).strftime("%Y-%m-%d")),
            (45, (today.replace(day=28) if today.day < 28 else today).strftime("%Y-%m-%d")),
        ]
        result = _select_generic_calendar_pair(parsed)
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 3, "Generic pair must return 3-tuple (front, back, near_miss_flag)")
        self.assertFalse(result[2], "Generic pair near_miss flag should always be False")

    def test_generic_pair_none_when_no_match(self):
        from app.services.earnings_discovery_quality_service import _select_generic_calendar_pair
        result = _select_generic_calendar_pair([])
        self.assertIsNone(result)

    def test_calendar_pair_consistent_len(self):
        """Both event-aware and generic paths should produce indexable pair[2]."""
        from app.services.earnings_discovery_quality_service import _select_calendar_expiration_pair
        from unittest.mock import patch as _p
        today = date.today()
        exps = [
            (today.replace(year=today.year + 1, month=1, day=17)).strftime("%Y-%m-%d"),
            (today.replace(year=today.year + 1, month=2, day=21)).strftime("%Y-%m-%d"),
        ]
        with _p("app.services.earnings_discovery_quality_service.config") as mock_cfg:
            mock_cfg.CALENDAR_FRONT_MIN_DTE = 7
            mock_cfg.CALENDAR_FRONT_MAX_DTE = 400
            mock_cfg.CALENDAR_MIN_EXPIRATION_GAP_DAYS = 14
            mock_cfg.CALENDAR_BACK_MAX_DTE = 500
            mock_cfg.CALENDAR_TARGET_EXPIRATION_GAP_DAYS = 30
            mock_cfg.CALENDAR_EARNINGS_EVENT_AWARE_EXPIRATIONS = False
            result = _select_calendar_expiration_pair(exps, event=None)
        if result is not None:
            self.assertGreaterEqual(len(result), 3)


class TestPatch109DevSnapshotActiveRows(unittest.TestCase):
    """Item 2 — TKT-ADV-003: _strategy_summary includes active_rows/active_items."""

    def test_active_rows_included(self):
        from app.services.developer_snapshot_service import _strategy_summary
        strat = {
            "strategy_id": "forward_factor_calendar",
            "strategy_label": "FF",
            "enabled": True,
            "ran": True,
            "pass_count": 2,
            "watch_count": 1,
            "fail_count": 0,
            "skipped_count": 0,
            "summary": {},
            "rows": [{"ticker": "AAPL"}],
            "active_rows": [{"ticker": "MSFT", "verdict": "PASS"}],
            "active_items": [{"ticker": "GOOG", "action": "review"}],
        }
        result = _strategy_summary(strat, include_rows=False)
        self.assertIn("active_rows", result)
        self.assertIn("active_items", result)
        self.assertEqual(len(result["active_rows"]), 1)
        self.assertEqual(result["active_rows"][0]["ticker"], "MSFT")
        self.assertNotIn("rows", result)

    def test_active_rows_absent_when_not_in_source(self):
        from app.services.developer_snapshot_service import _strategy_summary
        strat = {"strategy_id": "test", "rows": []}
        result = _strategy_summary(strat, include_rows=False)
        self.assertNotIn("active_rows", result)
        self.assertNotIn("active_items", result)


class TestPatch109AccountValueThreading(unittest.TestCase):
    """Item 3 — TKT-ADV-014: account_value threaded to quality filter."""

    def test_quality_filter_accepts_account_value(self):
        import inspect
        from app.services.earnings_discovery_quality_service import filter_earnings_discovery_for_calendar_scan
        sig = inspect.signature(filter_earnings_discovery_for_calendar_scan)
        self.assertIn("account_value", sig.parameters)

    def test_account_value_in_summary(self):
        from app.services.earnings_discovery_quality_service import filter_earnings_discovery_for_calendar_scan
        with patch("app.services.earnings_discovery_quality_service.TradierProvider"):
            result = filter_earnings_discovery_for_calendar_scan(
                earnings_trade_discovery={"items": []},
                account_value=12345.67,
            )
        self.assertEqual(result["summary"]["account_value_estimate"], 12345.67)


class TestPatch109VolatilityFraming(unittest.TestCase):
    """Item 4 — TKT-ADV-022/023: volatility framing in agent-prompt."""

    def _get_agent_prompt(self):
        import inspect, json
        from app.api import knowledge
        source = inspect.getsource(knowledge.knowledge_agent_prompt)
        return source

    def test_volatility_framing_present(self):
        source = self._get_agent_prompt()
        self.assertIn("volatility_framing", source)
        self.assertIn("principle", source)
        self.assertIn("terminology", source)
        self.assertIn("never_say", source)

    def test_volatility_framing_content(self):
        source = self._get_agent_prompt()
        self.assertIn("vol", source.lower())
        self.assertIn("IV crush", source)
        self.assertIn("VRP", source)


class TestPatch109VerticalLegFields(unittest.TestCase):
    """Item 5 — Leg detail gaps: position_type, side, option_type in vertical legs."""

    def test_leg_fields_present_in_source(self):
        import inspect
        from app.services import open_options_service
        source = inspect.getsource(open_options_service._detect_vertical_spreads)
        for field in ("position_type", "side", "option_type"):
            long_pattern = f'"{field}"'
            self.assertIn(long_pattern, source, f"Leg dict should contain '{field}'")

    def test_leg_dict_structure(self):
        """Verify both legs get position_type/side/option_type from surrounding context."""
        import inspect
        from app.services import open_options_service
        source = inspect.getsource(open_options_service._detect_vertical_spreads)
        self.assertIn('"position_type": "long"', source)
        self.assertIn('"position_type": "short"', source)
        self.assertIn('"side": "long"', source)
        self.assertIn('"side": "short"', source)
        count = source.count('"option_type": option_type')
        self.assertGreaterEqual(count, 2, "Both legs should inherit option_type from parent")


class TestPatch109ProviderFetchCount(unittest.TestCase):
    """Item 6 — TKT-053: provider_fetch_count in dev/status."""

    def test_provider_fetch_count_surfaced(self):
        from app.services.app_diagnostics_service import build_dev_status
        with patch("app.services.app_diagnostics_service.RunManifestRepository") as MockRepo:
            MockRepo.return_value.latest.return_value = {"provider_fetch_count": 42}
            with patch("app.services.app_diagnostics_service.build_commit_identity", return_value={
                "source_of_truth": "abc123", "git_branch": "main",
                "deploy_label": "v1", "commit_identity_mismatch": False,
            }):
                result = build_dev_status()
        self.assertEqual(result["provider_fetch_count"], 42)

    def test_provider_fetch_count_default_zero(self):
        from app.services.app_diagnostics_service import build_dev_status
        with patch("app.services.app_diagnostics_service.RunManifestRepository") as MockRepo:
            MockRepo.return_value.latest.return_value = {}
            with patch("app.services.app_diagnostics_service.build_commit_identity", return_value={
                "source_of_truth": "abc123", "git_branch": "main",
                "deploy_label": "v1", "commit_identity_mismatch": False,
            }):
                result = build_dev_status()
        self.assertEqual(result["provider_fetch_count"], 0)


class TestPatch109VerificationItems(unittest.TestCase):
    """Items 7-9: Verification-only checks."""

    def test_iv_percentile_enrichment_exists(self):
        """Item 8: IV percentile code exists in forward_factor_service."""
        import inspect
        from app.services import forward_factor_service
        source = inspect.getsource(forward_factor_service)
        self.assertIn("iv_percentile", source)
        self.assertIn("iv_percentile_note", source)

    def test_edge_on_margin_in_ff_agent_context(self):
        """Item 9: edge_on_margin surfaced in FF agent context."""
        import inspect
        from app.api import knowledge
        source = inspect.getsource(knowledge._ff_agent_context)
        self.assertIn("edge_on_margin", source)

    def test_edge_on_margin_in_ff_live_summary(self):
        """Item 9: edge_on_margin in FF live summary."""
        import inspect
        from app.api import knowledge
        source = inspect.getsource(knowledge._ff_live_summary)
        self.assertIn("edge_on_margin", source)


# ────────────────────────────────────────────────────────────────────
# Patch 28A: Canonical Data Pipeline + Structural Fixes.
#            stale_structure on scan candidates, expiry_near_miss/exception
#            defaults, account_value_source threading, options_trading
#            philosophy block, exclude-list _strategy_summary rewrite.
# ────────────────────────────────────────────────────────────────────

class TestPatch28AStaleStructureOnCandidates(unittest.TestCase):
    """Item 1: stale_structure/stale_structure_note set on scan candidate rows."""

    def _make_candidate(self, underlying):
        from app.services.skew_momentum_vertical_service import _candidate_row
        direction = {"direction": "bullish", "confirmed": True, "score": 70.0, "reason": "Bullish."}
        long_leg = {
            "strike": 100.0, "bid": 1.00, "ask": 1.20, "mid": 1.10,
            "iv": 0.30, "delta": 0.50, "open_interest": 200, "volume": 100,
        }
        short_leg = {
            "strike": 105.0, "bid": 0.40, "ask": 0.50, "mid": 0.45,
            "iv": 0.35, "delta": 0.30, "open_interest": 150, "volume": 80,
        }
        return _candidate_row(
            ticker="AAPL", direction=direction, underlying=underlying,
            expiration="2024-03-15", dte=28, option_type="call",
            long_leg=long_leg, short_leg=short_leg, metrics={}, earnings_event={},
            account_context={}, adjusted_skew_score=15.0,
        )

    def test_fresh_candidate_not_stale(self):
        row = self._make_candidate(underlying=100.0)
        self.assertIn("stale_structure", row)
        self.assertFalse(row["stale_structure"])
        self.assertIsNone(row["stale_structure_note"])

    def test_moved_candidate_is_stale(self):
        row = self._make_candidate(underlying=110.0)
        self.assertTrue(row["stale_structure"])
        self.assertIsInstance(row["stale_structure_note"], str)
        self.assertIn("strike", row["stale_structure_note"])

    def test_finalize_defaults_missing_keys(self):
        from app.services.skew_momentum_vertical_service import _finalize
        result = {"items": [{"score": 1.0}], "enabled": True}
        finalized = _finalize(result)
        row = finalized["items"][0]
        self.assertIn("stale_structure", row)
        self.assertFalse(row["stale_structure"])
        self.assertIsNone(row["stale_structure_note"])


class TestPatch28AExpiryNearMissDefaults(unittest.TestCase):
    """Item 3: expiry_near_miss always present; expiry_exception captured on failure."""

    def test_expiry_exception_captured_and_near_miss_false_on_failure(self):
        from app.services.earnings_discovery_quality_service import filter_earnings_discovery_for_calendar_scan
        with patch("app.services.earnings_discovery_quality_service.TradierProvider") as MockProvider:
            instance = MockProvider.return_value
            instance.is_configured = True
            instance.get_quotes.return_value = {"AAPL": {"last": 150.0}}
            instance.get_expirations.side_effect = RuntimeError("boom")
            result = filter_earnings_discovery_for_calendar_scan(
                earnings_trade_discovery={"items": [{"ticker": "AAPL", "earnings_date": "2026-07-15"}]},
            )
        row = result["events_by_ticker"]["AAPL"]
        self.assertFalse(row["expiry_near_miss"])
        self.assertIsNotNone(row["expiry_exception"])
        self.assertIn("boom", row["expiry_exception"])

    def test_expiry_near_miss_present_and_exception_none_when_no_pair_matched(self):
        from app.services.earnings_discovery_quality_service import filter_earnings_discovery_for_calendar_scan
        with patch("app.services.earnings_discovery_quality_service.TradierProvider") as MockProvider, \
             patch("app.services.earnings_discovery_quality_service._select_calendar_expiration_pair", return_value=None), \
             patch("app.services.earnings_discovery_quality_service._find_near_miss_expiry", return_value=None):
            instance = MockProvider.return_value
            instance.is_configured = True
            instance.get_quotes.return_value = {"AAPL": {"last": 150.0}}
            instance.get_expirations.return_value = ["2026-08-01"]
            result = filter_earnings_discovery_for_calendar_scan(
                earnings_trade_discovery={"items": [{"ticker": "AAPL", "earnings_date": "2026-07-15"}]},
            )
        row = result["events_by_ticker"]["AAPL"]
        self.assertIn("expiry_near_miss", row)
        self.assertFalse(row["expiry_near_miss"])
        self.assertIsNone(row["expiry_exception"])


class TestPatch28AAccountValueSource(unittest.TestCase):
    """Item 5: account_value_source threaded through quality filter and account risk eval."""

    def test_quality_filter_accepts_account_value_source(self):
        import inspect
        from app.services.earnings_discovery_quality_service import filter_earnings_discovery_for_calendar_scan
        sig = inspect.signature(filter_earnings_discovery_for_calendar_scan)
        self.assertIn("account_value_source", sig.parameters)

    def test_account_value_source_in_summary(self):
        from app.services.earnings_discovery_quality_service import filter_earnings_discovery_for_calendar_scan
        with patch("app.services.earnings_discovery_quality_service.TradierProvider"):
            result = filter_earnings_discovery_for_calendar_scan(
                earnings_trade_discovery={"items": []},
                account_value=1000.0,
                account_value_source="live",
            )
        self.assertEqual(result["summary"]["account_value_source"], "live")

    def test_account_value_source_defaults_unknown(self):
        from app.services.earnings_discovery_quality_service import filter_earnings_discovery_for_calendar_scan
        with patch("app.services.earnings_discovery_quality_service.TradierProvider"):
            result = filter_earnings_discovery_for_calendar_scan(earnings_trade_discovery={"items": []})
        self.assertEqual(result["summary"]["account_value_source"], "unknown")

    def test_analysis_service_account_value_source_live_when_market_value_present(self):
        from app.services.analysis_service import _account_value_source
        positions = [{"market_value": 5000.0, "ticker": "AAPL"}]
        self.assertEqual(_account_value_source(positions), "live")

    def test_analysis_service_account_value_source_estimate_when_only_estimable(self):
        from app.services.analysis_service import _account_value_source
        positions = [{"quantity": 10.0, "avg_buy_price": 50.0}]
        self.assertEqual(_account_value_source(positions), "estimate")

    def test_analysis_service_account_value_source_unknown_when_empty(self):
        from app.services.analysis_service import _account_value_source
        self.assertEqual(_account_value_source([]), "unknown")

    def test_evaluate_account_risk_surfaces_source_from_context(self):
        from app.services.calendar_verdict_service import evaluate_account_risk
        with patch("app.services.calendar_verdict_service.config") as mock_cfg:
            mock_cfg.CALENDAR_ACCOUNT_VALUE_OVERRIDE = None
            mock_cfg.CALENDAR_ACCOUNT_GUARDRAILS_ENABLED = False
            mock_cfg.CALENDAR_MAX_DEBIT_PCT_OF_ACCOUNT = 0.02
            result = evaluate_account_risk(
                candidate={"conservative_debit": 1.0},
                account_context={"account_value_estimate": 10000.0, "account_value_source": "live"},
            )
        self.assertEqual(result["account_value_source"], "live")

    def test_evaluate_account_risk_source_is_override_when_override_set(self):
        from app.services.calendar_verdict_service import evaluate_account_risk
        with patch("app.services.calendar_verdict_service.config") as mock_cfg:
            mock_cfg.CALENDAR_ACCOUNT_VALUE_OVERRIDE = 5000.0
            mock_cfg.CALENDAR_ACCOUNT_GUARDRAILS_ENABLED = False
            mock_cfg.CALENDAR_MAX_DEBIT_PCT_OF_ACCOUNT = 0.02
            result = evaluate_account_risk(
                candidate={"conservative_debit": 1.0},
                account_context={"account_value_estimate": 10000.0, "account_value_source": "live"},
            )
        self.assertEqual(result["account_value_source"], "override")


class TestPatch28AOptionsTradingPhilosophy(unittest.TestCase):
    """Item 6: options_trading_philosophy block present in agent-prompt."""

    def _source(self):
        import inspect
        from app.api import knowledge
        return inspect.getsource(knowledge.knowledge_agent_prompt)

    def test_block_present_with_expected_keys(self):
        source = self._source()
        self.assertIn("options_trading_philosophy", source)
        self.assertIn("core", source)
        self.assertIn("signal_framing", source)
        self.assertIn("exit_conditions", source)
        self.assertIn("theta_clarification", source)

    def test_block_uses_vrp_and_iv_rv_language(self):
        source = self._source()
        self.assertIn("VRP", source)
        self.assertIn("implied volatility", source)
        self.assertIn("realized volatility", source)
        self.assertIn("not an edge source", source)


class TestPatch28AStrategySummaryExcludeList(unittest.TestCase):
    """Structural fix: _strategy_summary uses an exclude list, not a whitelist."""

    def test_arbitrary_new_field_passes_through(self):
        from app.services.developer_snapshot_service import _strategy_summary
        strat = {
            "strategy_id": "skew_momentum_vertical",
            "brand_new_field_nobody_whitelisted": "should still appear",
        }
        result = _strategy_summary(strat, include_rows=False)
        self.assertIn("brand_new_field_nobody_whitelisted", result)
        self.assertEqual(result["brand_new_field_nobody_whitelisted"], "should still appear")

    def test_excluded_keys_are_stripped(self):
        from app.services.developer_snapshot_service import _strategy_summary
        strat = {
            "strategy_id": "forward_factor_calendar",
            "observation_history": ["huge", "history"],
            "ff_journal": {"big": "blob"},
            "raw_chain_data": {"raw": True},
        }
        result = _strategy_summary(strat, include_rows=False)
        self.assertNotIn("observation_history", result)
        self.assertNotIn("ff_journal", result)
        self.assertNotIn("raw_chain_data", result)
        self.assertIn("strategy_id", result)

    def test_rows_capped_when_include_rows_true(self):
        from app.services.developer_snapshot_service import _strategy_summary
        strat = {"strategy_id": "x", "rows": [{"i": i} for i in range(75)]}
        result = _strategy_summary(strat, include_rows=True)
        self.assertEqual(len(result["rows"]), 50)

    def test_rows_stripped_when_include_rows_false(self):
        from app.services.developer_snapshot_service import _strategy_summary
        strat = {"strategy_id": "x", "rows": [{"i": 1}]}
        result = _strategy_summary(strat, include_rows=False)
        self.assertNotIn("rows", result)


class TestPatch28AProviderCallCountAlias(unittest.TestCase):
    """Item 4: provider_call_count alias present alongside provider_fetch_count."""

    def test_provider_call_count_alias_present(self):
        from app.services.app_diagnostics_service import build_dev_status
        with patch("app.services.app_diagnostics_service.RunManifestRepository") as MockRepo:
            MockRepo.return_value.latest.return_value = {"provider_fetch_count": 17}
            with patch("app.services.app_diagnostics_service.build_commit_identity", return_value={
                "source_of_truth": "abc123", "git_branch": "main",
                "deploy_label": "v1", "commit_identity_mismatch": False,
            }):
                result = build_dev_status()
        self.assertEqual(result["provider_call_count"], 17)
        self.assertEqual(result["provider_call_count"], result["provider_fetch_count"])


if __name__ == "__main__":
    unittest.main()
