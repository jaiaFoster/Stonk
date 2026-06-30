"""Tests for the ASA patch — Skew universe Phase 2, earnings pre-screen,
and the UI overhaul (open options positions + personalize link).
"""

from __future__ import annotations

import importlib
import sys
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
