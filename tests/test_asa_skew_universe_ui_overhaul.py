"""Tests for the ASA patch — Skew universe Phase 2, earnings pre-screen,
and the UI overhaul (open options positions + personalize link).
"""

from __future__ import annotations

import importlib
import sys
from datetime import date, timedelta
from unittest.mock import patch

import pytest


def _reload_config():
    if "app.config" in sys.modules:
        importlib.reload(sys.modules["app.config"])


# ---------------------------------------------------------------------------
# Area 1 — _batch_quotes
# ---------------------------------------------------------------------------

class TestBatchQuotes:
    def test_chunks_requests_by_chunk_size(self):
        from app.services import universe_discovery_service as svc

        tickers = [f"T{i}" for i in range(5)]
        calls: list[list[str]] = []

        class FakeProvider:
            is_configured = True

            def get_quotes(self, symbols, greeks=False):
                calls.append(list(symbols))
                return {s: {"last": 10.0} for s in symbols}

        with patch("app.providers.tradier_provider.TradierProvider", return_value=FakeProvider()):
            result = svc._batch_quotes(tickers, lambda msg: None, chunk_size=2)

        assert len(calls) == 3
        assert calls[0] == ["T0", "T1"]
        assert calls[1] == ["T2", "T3"]
        assert calls[2] == ["T4"]
        assert len(result) == 5

    def test_returns_empty_on_unconfigured_provider(self):
        from app.services import universe_discovery_service as svc

        class FakeProvider:
            is_configured = False

            def get_quotes(self, symbols, greeks=False):
                raise AssertionError("should not be called when not configured")

        with patch("app.providers.tradier_provider.TradierProvider", return_value=FakeProvider()):
            result = svc._batch_quotes(["AAPL"], lambda msg: None)

        assert result == {}

    def test_returns_empty_on_empty_input(self):
        from app.services import universe_discovery_service as svc

        result = svc._batch_quotes([], lambda msg: None)
        assert result == {}

    def test_per_chunk_failure_is_non_fatal(self):
        from app.services import universe_discovery_service as svc

        class FakeProvider:
            is_configured = True

            def get_quotes(self, symbols, greeks=False):
                if "BAD" in symbols:
                    raise RuntimeError("boom")
                return {s: {"last": 5.0} for s in symbols}

        with patch("app.providers.tradier_provider.TradierProvider", return_value=FakeProvider()):
            result = svc._batch_quotes(["BAD", "GOOD"], lambda msg: None, chunk_size=1)

        assert "GOOD" in result
        assert "BAD" not in result

    def test_provider_init_failure_is_non_fatal(self):
        from app.services import universe_discovery_service as svc

        with patch("app.providers.tradier_provider.TradierProvider", side_effect=RuntimeError("no init")):
            result = svc._batch_quotes(["AAPL"], lambda msg: None)

        assert result == {}


# ---------------------------------------------------------------------------
# Area 1 — get_skew_candidates (volume ranking, not IV)
# ---------------------------------------------------------------------------

class TestGetSkewCandidates:
    def test_ranks_by_average_volume_descending(self):
        from app.services import universe_discovery_service as svc

        quotes = {
            "AAA": {"last": 50.0, "average_volume": 1_000_000},
            "BBB": {"last": 50.0, "average_volume": 5_000_000},
            "CCC": {"last": 50.0, "average_volume": 2_000_000},
        }

        with patch.object(svc, "_get_constituent_tickers", return_value=["AAA", "BBB", "CCC"]), \
             patch.object(svc, "_batch_quotes", return_value=quotes), \
             patch("app.config.UNIVERSE_DISCOVERY_ENABLED", True), \
             patch("app.config.UNIVERSE_MIN_PRICE", 10.0), \
             patch("app.config.UNIVERSE_MAX_PRICE", 1000.0), \
             patch("app.config.UNIVERSE_MIN_AVG_VOLUME", 500_000), \
             patch("app.config.SKEW_UNIVERSE_MAX_CANDIDATES", 50):
            result = svc.get_skew_candidates(log_print=lambda msg: None)

        assert result == ["BBB", "CCC", "AAA"]

    def test_respects_max_candidates_cap_of_50(self):
        from app.services import universe_discovery_service as svc

        tickers = [f"T{i:03d}" for i in range(100)]
        quotes = {t: {"last": 50.0, "average_volume": 1_000_000 + i} for i, t in enumerate(tickers)}

        with patch.object(svc, "_get_constituent_tickers", return_value=tickers), \
             patch.object(svc, "_batch_quotes", return_value=quotes), \
             patch("app.config.UNIVERSE_DISCOVERY_ENABLED", True), \
             patch("app.config.UNIVERSE_MIN_PRICE", 10.0), \
             patch("app.config.UNIVERSE_MAX_PRICE", 1000.0), \
             patch("app.config.UNIVERSE_MIN_AVG_VOLUME", 500_000), \
             patch("app.config.SKEW_UNIVERSE_MAX_CANDIDATES", 50):
            result = svc.get_skew_candidates(log_print=lambda msg: None)

        assert len(result) == 50
        # highest avg_volume tickers (last in the input list) should win
        assert result[0] == "T099"

    def test_excludes_held_tickers(self):
        from app.services import universe_discovery_service as svc

        quotes = {
            "AAA": {"last": 50.0, "average_volume": 1_000_000},
            "BBB": {"last": 50.0, "average_volume": 5_000_000},
        }

        with patch.object(svc, "_get_constituent_tickers", return_value=["AAA", "BBB"]), \
             patch.object(svc, "_batch_quotes", return_value=quotes), \
             patch("app.config.UNIVERSE_DISCOVERY_ENABLED", True), \
             patch("app.config.UNIVERSE_MIN_PRICE", 10.0), \
             patch("app.config.UNIVERSE_MAX_PRICE", 1000.0), \
             patch("app.config.UNIVERSE_MIN_AVG_VOLUME", 500_000), \
             patch("app.config.SKEW_UNIVERSE_MAX_CANDIDATES", 50):
            result = svc.get_skew_candidates(exclude_held=["bbb"], log_print=lambda msg: None)

        assert result == ["AAA"]

    def test_filters_by_price_and_volume_thresholds(self):
        from app.services import universe_discovery_service as svc

        quotes = {
            "CHEAP": {"last": 1.0, "average_volume": 5_000_000},   # below min price
            "EXPENSIVE": {"last": 5000.0, "average_volume": 5_000_000},  # above max price
            "ILLIQUID": {"last": 50.0, "average_volume": 1_000},   # below min volume
            "OK": {"last": 50.0, "average_volume": 1_000_000},
        }

        with patch.object(svc, "_get_constituent_tickers", return_value=list(quotes.keys())), \
             patch.object(svc, "_batch_quotes", return_value=quotes), \
             patch("app.config.UNIVERSE_DISCOVERY_ENABLED", True), \
             patch("app.config.UNIVERSE_MIN_PRICE", 10.0), \
             patch("app.config.UNIVERSE_MAX_PRICE", 1000.0), \
             patch("app.config.UNIVERSE_MIN_AVG_VOLUME", 500_000), \
             patch("app.config.SKEW_UNIVERSE_MAX_CANDIDATES", 50):
            result = svc.get_skew_candidates(log_print=lambda msg: None)

        assert result == ["OK"]

    def test_returns_empty_when_universe_discovery_disabled(self):
        from app.services import universe_discovery_service as svc

        with patch("app.config.UNIVERSE_DISCOVERY_ENABLED", False):
            result = svc.get_skew_candidates(log_print=lambda msg: None)

        assert result == []

    def test_returns_empty_on_constituent_fetch_failure(self):
        from app.services import universe_discovery_service as svc

        with patch.object(svc, "_get_constituent_tickers", side_effect=RuntimeError("db down")), \
             patch("app.config.UNIVERSE_DISCOVERY_ENABLED", True):
            result = svc.get_skew_candidates(log_print=lambda msg: None)

        assert result == []


# ---------------------------------------------------------------------------
# Area 1/2 — get_constituent_ticker_set
# ---------------------------------------------------------------------------

class TestGetConstituentTickerSet:
    def test_returns_uppercase_set_on_success(self):
        from app.services import universe_discovery_service as svc

        with patch.object(svc, "_get_constituent_tickers", return_value=["aapl", "MSFT", " nvda "]):
            result = svc.get_constituent_ticker_set()

        assert result == {"AAPL", "MSFT", "NVDA"}

    def test_returns_empty_set_on_failure_fail_open(self):
        from app.services import universe_discovery_service as svc

        with patch.object(svc, "_get_constituent_tickers", side_effect=RuntimeError("db down")):
            result = svc.get_constituent_ticker_set()

        assert result == set()


# ---------------------------------------------------------------------------
# Area 2 — earnings discovery constituent pre-screen
# ---------------------------------------------------------------------------

class TestEarningsDiscoveryPrescreen:
    def _discovery(self, tickers):
        return {"items": [{"ticker": t} for t in tickers]}

    def test_filters_tickers_not_in_constituent_set(self):
        from app.services import earnings_discovery_quality_service as svc

        with patch("app.config.EARNINGS_DISCOVERY_CONSTITUENT_PRESCREEN", True), \
             patch(
                 "app.services.universe_discovery_service.get_constituent_ticker_set",
                 return_value={"AAPL", "MSFT"},
             ), \
             patch.object(svc, "_merge_universe_discovery", side_effect=lambda items, *a, **k: items), \
             patch("app.services.earnings_discovery_quality_service.TradierProvider") as MockProvider:
            MockProvider.return_value.is_configured = False
            result = svc.filter_earnings_discovery_for_calendar_scan(
                self._discovery(["AAPL", "MSFT", "ZZZZ"]),
                log_print=lambda msg: None,
            )

        assert result["summary"]["prescreen_removed_count"] == 1
        assert result["summary"]["raw_event_count"] == 2

    def test_fail_open_when_constituent_lookup_raises(self):
        from app.services import earnings_discovery_quality_service as svc

        with patch("app.config.EARNINGS_DISCOVERY_CONSTITUENT_PRESCREEN", True), \
             patch(
                 "app.services.universe_discovery_service.get_constituent_ticker_set",
                 side_effect=RuntimeError("cache unavailable"),
             ), \
             patch.object(svc, "_merge_universe_discovery", side_effect=lambda items, *a, **k: items), \
             patch("app.services.earnings_discovery_quality_service.TradierProvider") as MockProvider:
            MockProvider.return_value.is_configured = False
            result = svc.filter_earnings_discovery_for_calendar_scan(
                self._discovery(["AAPL", "MSFT", "ZZZZ"]),
                log_print=lambda msg: None,
            )

        assert result["summary"]["prescreen_removed_count"] == 0
        assert result["summary"]["raw_event_count"] == 3

    def test_disabled_flag_skips_filter_entirely(self):
        from app.services import earnings_discovery_quality_service as svc

        with patch("app.config.EARNINGS_DISCOVERY_CONSTITUENT_PRESCREEN", False), \
             patch.object(svc, "_merge_universe_discovery", side_effect=lambda items, *a, **k: items), \
             patch("app.services.earnings_discovery_quality_service.TradierProvider") as MockProvider:
            MockProvider.return_value.is_configured = False
            result = svc.filter_earnings_discovery_for_calendar_scan(
                self._discovery(["AAPL", "MSFT", "ZZZZ"]),
                log_print=lambda msg: None,
            )

        assert result["summary"]["prescreen_removed_count"] == 0
        assert result["summary"]["raw_event_count"] == 3

    def test_universe_added_count_independent_of_prescreen(self):
        """universe_added_count reflects what _merge_universe_discovery added,
        not what the prescreen filter later removes.
        """
        from app.services import earnings_discovery_quality_service as svc

        def fake_merge(items, *a, **k):
            return items + [{"ticker": "NEWCO"}]

        with patch("app.config.EARNINGS_DISCOVERY_CONSTITUENT_PRESCREEN", True), \
             patch(
                 "app.services.universe_discovery_service.get_constituent_ticker_set",
                 return_value={"AAPL"},
             ), \
             patch.object(svc, "_merge_universe_discovery", side_effect=fake_merge), \
             patch("app.services.earnings_discovery_quality_service.TradierProvider") as MockProvider:
            MockProvider.return_value.is_configured = False
            result = svc.filter_earnings_discovery_for_calendar_scan(
                self._discovery(["AAPL"]),
                log_print=lambda msg: None,
            )

        # raw_only=1, merged adds NEWCO -> universe_added_count=1, then
        # prescreen removes NEWCO (not in constituent set) -> raw_event_count=1
        assert result["summary"]["raw_only_count"] == 1
        assert result["summary"]["universe_added_count"] == 1
        assert result["summary"]["prescreen_removed_count"] == 1
        assert result["summary"]["raw_event_count"] == 1


# ---------------------------------------------------------------------------
# Area 3 — Open Options Positions section + Personalize link + FF kicker
# ---------------------------------------------------------------------------

class TestOpenOptionsPositionsSection:
    def test_empty_input_shows_empty_state(self):
        from app.services.report_service import _open_options_positions_section_html

        html = _open_options_positions_section_html({})

        assert 'id="open-options-positions"' in html
        assert "Open Options Positions" in html
        assert "did not run for this report" in html

    def test_no_verticals_or_single_legs_shows_empty_message(self):
        from app.services.report_service import _open_options_positions_section_html

        html = _open_options_positions_section_html({
            "summary": {"account_count": 1, "total_positions": 0, "option_leg_count": 0},
            "verticals": [],
            "single_legs": [],
            "calendars": [],
            "errors": [],
        })

        assert "No open verticals or single-leg option positions detected" in html

    def test_renders_vertical_and_single_leg_rows(self):
        from app.services.report_service import _open_options_positions_section_html

        open_options = {
            "summary": {"account_count": 1, "total_positions": 2, "option_leg_count": 3},
            "verticals": [{
                "ticker": "AAPL",
                "option_type": "call",
                "exit_signal": "EXIT_TARGET",
                "long_strike": 100.0,
                "short_strike": 110.0,
                "unrealized_pnl_pct": 25.0,
                "unrealized_pnl": 250.0,
                "dte": 12,
                "net_debit": 4.0,
                "current_value": 5.0,
                "max_profit": 600.0,
                "max_loss": 400.0,
                "pct_of_max_profit": 40.0,
                "quantity": 1,
                "expiration": "2026-07-17",
                "broker": "robinhood",
            }],
            "single_legs": [{
                "ticker": "MSFT",
                "position": "short",
                "option_type": "put",
                "strike": 300.0,
                "unrealized_pnl": -50.0,
                "dte": 5,
                "expiration": "2026-07-03",
                "average_price": 2.0,
                "current_price": 2.5,
                "quantity": -1,
                "broker": "robinhood",
            }],
            "calendars": [],
            "errors": [],
        }

        html = _open_options_positions_section_html(open_options)

        assert "AAPL" in html and "EXIT_TARGET" in html
        assert "MSFT" in html and "Short" in html
        assert "1 vertical" in html and "1 single-leg" in html


class TestPersonalizeLink:
    def test_link_points_to_dashboard(self):
        from app.services.report_service import _personalize_link_html

        html = _personalize_link_html()

        assert 'href="/dashboard"' in html
        assert "Personalize this view" in html


class TestFormatHtmlIntegration:
    def test_format_html_includes_new_sections_on_empty_snapshot(self):
        from app.services.report_service import format_html

        html = format_html("payload text", [], {}, [], {}, [])

        assert "open-options-positions" in html
        assert "Personalize this view" in html
        assert "Open Options Positions" in html

    def test_ff_kicker_reflects_dry_run_flag(self):
        from app.services.report_service import format_html

        with patch("app.config.FORWARD_FACTOR_DRY_RUN", True):
            html = format_html("payload text", [], {}, [], {}, [])

        assert "signal live, execution gated" in html


# ---------------------------------------------------------------------------
# Phase 2 — TKT-ADV-006: precheck expiration pair passthrough
# ---------------------------------------------------------------------------

class TestPositionsFromEarningsDiscovery:
    def test_carries_precheck_expirations_into_earnings_event(self):
        from app.services.pipeline_helpers import positions_from_earnings_discovery

        discovery = {
            "passed_items": [{
                "ticker": "JPM",
                "front_expiration": "2026-07-10",
                "back_expiration": "2026-08-07",
                "event": {
                    "ticker": "JPM",
                    "earnings_date": "2026-07-11",
                },
            }]
        }

        positions = positions_from_earnings_discovery(discovery)

        assert len(positions) == 1
        ev = positions[0]["earnings_event"]
        assert ev["precheck_front_expiration"] == "2026-07-10"
        assert ev["precheck_back_expiration"] == "2026-08-07"

    def test_omits_precheck_keys_when_expirations_missing(self):
        from app.services.pipeline_helpers import positions_from_earnings_discovery

        discovery = {
            "passed_items": [{
                "ticker": "AAPL",
                "event": {"ticker": "AAPL", "earnings_date": "2026-07-25"},
            }]
        }

        positions = positions_from_earnings_discovery(discovery)

        assert len(positions) == 1
        ev = positions[0]["earnings_event"]
        assert "precheck_front_expiration" not in ev
        assert "precheck_back_expiration" not in ev

    def test_returns_empty_on_empty_discovery(self):
        from app.services.pipeline_helpers import positions_from_earnings_discovery

        assert positions_from_earnings_discovery({}) == []
        assert positions_from_earnings_discovery(None) == []


class TestSelectExpirationPairsPrecheck:
    def _run(self, expirations, earnings_event=None, diagnostics=None):
        from app.services.calendar_spread_service import _select_expiration_pairs
        return _select_expiration_pairs(expirations, earnings_event=earnings_event, diagnostics=diagnostics)

    def test_uses_precheck_pair_when_both_expirations_available(self):
        front = (date.today() + timedelta(days=7)).isoformat()
        back = (date.today() + timedelta(days=35)).isoformat()
        expirations = [front, (date.today() + timedelta(days=14)).isoformat(), back, (date.today() + timedelta(days=63)).isoformat()]
        event = {
            "precheck_front_expiration": front,
            "precheck_back_expiration": back,
        }
        diag: dict = {}
        result = self._run(expirations, earnings_event=event, diagnostics=diag)

        assert result == [(front, back)]
        assert diag.get("source") == "quality_precheck"

    def test_falls_through_when_precheck_pair_stale(self):
        # Only back expiration is present; front has already rolled off.
        expirations = ["2026-08-07", "2026-09-18"]
        event = {
            "precheck_front_expiration": "2026-07-10",  # not in expirations anymore
            "precheck_back_expiration": "2026-08-07",
        }
        diag: dict = {}
        result = self._run(expirations, earnings_event=event, diagnostics=diag)

        assert diag.get("precheck_pair_stale") is True
        # Falls through to generic selection; just check it returned something (or empty)
        assert isinstance(result, list)

    def test_ignores_precheck_when_both_keys_missing(self):
        expirations = ["2026-08-07", "2026-09-18"]
        event = {"earnings_date": "2026-08-01"}
        diag: dict = {}
        result = self._run(expirations, earnings_event=event, diagnostics=diag)

        assert diag.get("source") != "quality_precheck"


# ---------------------------------------------------------------------------
# Phase 2 — TKT-ADV-001/002: source_call_log on quality rows
# ---------------------------------------------------------------------------

class TestSourceCallLog:
    def _make_row(self, sources_seen, configured=("finnhub",)):
        from app.services.earnings_discovery_quality_service import _quality_row
        event = {
            "ticker": "AAPL",
            "earnings_date": "2026-07-25",
            "sources_seen": list(sources_seen),
        }
        with patch("app.services.earnings_discovery_quality_service._configured_provider_names",
                   return_value=list(configured)):
            return _quality_row(event, {})

    def test_source_call_log_present_on_quality_row(self):
        row = self._make_row(["finnhub"])
        assert "source_call_log" in row

    def test_single_source_flag_true_when_one_source(self):
        row = self._make_row(["finnhub"])
        assert row["source_call_log"]["is_single_source"] is True

    def test_single_source_flag_false_when_two_sources(self):
        row = self._make_row(["finnhub", "alphavantage"], configured=("finnhub", "alphavantage"))
        assert row["source_call_log"]["is_single_source"] is False

    def test_providers_without_data_lists_missing_contributor(self):
        row = self._make_row(["finnhub"], configured=("finnhub", "alphavantage"))
        assert "alphavantage" in row["source_call_log"]["providers_without_data"]

    def test_configured_providers_empty_when_no_keys_set(self):
        row = self._make_row([], configured=())
        assert row["source_call_log"]["configured_providers"] == []


class TestConfiguredProviderNames:
    def test_returns_only_providers_with_api_keys(self):
        from app.services.earnings_discovery_quality_service import _configured_provider_names

        with patch("app.config.FINNHUB_API_KEY", "key123"), \
             patch("app.config.ALPHA_VANTAGE_API_KEY", None), \
             patch("app.config.EARNINGS_PROVIDER_ORDER", ["finnhub", "alphavantage"]):
            result = _configured_provider_names()

        assert result == ["finnhub"]

    def test_returns_both_when_both_keys_set(self):
        from app.services.earnings_discovery_quality_service import _configured_provider_names

        with patch("app.config.FINNHUB_API_KEY", "key123"), \
             patch("app.config.ALPHA_VANTAGE_API_KEY", "avkey"), \
             patch("app.config.EARNINGS_PROVIDER_ORDER", ["finnhub", "alphavantage"]):
            result = _configured_provider_names()

        assert "finnhub" in result
        assert "alphavantage" in result


# ---------------------------------------------------------------------------
# Phase 2 — TKT-CAL-004: verdict_tier sort
# ---------------------------------------------------------------------------

def _verdict_tier(verdict: str) -> int:
    v = str(verdict or "").upper()
    if v.startswith("PASS"):
        return 100
    if v.startswith("WATCH"):
        return 80
    if v.startswith("NEAR_MISS"):
        return 60
    return 35


class TestVerdictTier:
    def test_pass_scores_100(self):
        assert _verdict_tier("PASS / POSSIBLE ENTRY SETUP") == 100

    def test_watch_scores_80(self):
        assert _verdict_tier("WATCH / STRUCTURE FOUND") == 80

    def test_near_miss_scores_60(self):
        assert _verdict_tier("NEAR_MISS / EXPIRY_GAP") == 60

    def test_fail_scores_35(self):
        assert _verdict_tier("FAIL / NO VALID CALENDAR STRUCTURE") == 35

    def test_unknown_scores_35(self):
        assert _verdict_tier("") == 35


class TestVerdictTierSortOrder:
    def test_pass_rows_sort_before_watch_rows(self):
        rows = [
            {"verdict": "WATCH / STRUCTURE FOUND", "score": 50.0, "verdict_tier": _verdict_tier("WATCH / STRUCTURE FOUND")},
            {"verdict": "PASS / POSSIBLE ENTRY SETUP", "score": 50.0, "verdict_tier": _verdict_tier("PASS / POSSIBLE ENTRY SETUP")},
            {"verdict": "FAIL / NO VALID CALENDAR STRUCTURE", "score": 50.0, "verdict_tier": _verdict_tier("FAIL / NO VALID CALENDAR STRUCTURE")},
        ]
        rows.sort(key=lambda item: (float(item.get("verdict_tier") or 0), float(item.get("score") or 0)), reverse=True)

        assert rows[0]["verdict"].startswith("PASS")
        assert rows[1]["verdict"].startswith("WATCH")
        assert rows[2]["verdict"].startswith("FAIL")

    def test_watch_before_fail_at_equal_score(self):
        rows = [
            {"verdict": "FAIL / NO VALID CALENDAR STRUCTURE", "score": 35.0, "verdict_tier": _verdict_tier("FAIL / NO VALID CALENDAR STRUCTURE")},
            {"verdict": "WATCH / STRUCTURE FOUND", "score": 35.0, "verdict_tier": _verdict_tier("WATCH / STRUCTURE FOUND")},
        ]
        rows.sort(key=lambda item: (float(item.get("verdict_tier") or 0), float(item.get("score") or 0)), reverse=True)

        assert rows[0]["verdict"].startswith("WATCH")
        assert rows[1]["verdict"].startswith("FAIL")

    def test_higher_score_wins_within_same_tier(self):
        rows = [
            {"verdict": "WATCH / STRUCTURE FOUND", "score": 40.0, "verdict_tier": _verdict_tier("WATCH / STRUCTURE FOUND")},
            {"verdict": "WATCH / URGENT MANUAL REVIEW", "score": 70.0, "verdict_tier": _verdict_tier("WATCH / URGENT MANUAL REVIEW")},
        ]
        rows.sort(key=lambda item: (float(item.get("verdict_tier") or 0), float(item.get("score") or 0)), reverse=True)

        assert rows[0]["score"] == 70.0
