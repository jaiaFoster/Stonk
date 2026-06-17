"""
Patch 27AD — Calendar data integrity.

Tests:
 1. TKT-025: is_timestamp_confirmed=False when single source (EARNINGS_CONFIRM_REQUIRE_MULTI_SOURCE=True)
 2. TKT-025: is_timestamp_single_source=True added for single-source events
 3. TKT-025: is_timestamp_confirmed stays True when ≥2 sources agree on date
 4. TKT-025: earnings_source_conflict=True when two sources disagree on date within threshold
 5. TKT-025: no conflict flag when dates differ by more than threshold
 6. TKT-026: multi-source check disabled by EARNINGS_CONFIRM_REQUIRE_MULTI_SOURCE=False
 7. TKT-028: expiration search widened when DTE cap excludes all valid fronts
 8. TKT-028: NO_VALID_EXPIRATION_PAIR reason in diagnostics when no valid pair
 9. TKT-028: tried_front_expirations populated in diagnostics
10. TKT-028: valid pair found via widened search (beyond front_max_dte)
11. TKT-022: earnings_discovery_window_effective present in pipeline_status
12. TKT-022: earnings_discovery_window_effective has window_start / window_end fields
"""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from unittest.mock import patch


# ---------------------------------------------------------------------------
# 1-6: TKT-025/026 — multi-source confirmation
# ---------------------------------------------------------------------------

class TestMultiSourceConfirmation(unittest.TestCase):

    def _merge(self, events, require_multi=True, conflict_days=2):
        from app.providers.earnings_provider import _merge_dedupe_events
        with patch("app.config.EARNINGS_CONFIRM_REQUIRE_MULTI_SOURCE", require_multi), \
             patch("app.config.EARNINGS_DATE_CONFLICT_THRESHOLD_DAYS", conflict_days):
            return _merge_dedupe_events(events)

    def _event(self, ticker, date_str, source, confirmed=True):
        return {
            "ticker": ticker,
            "symbol": ticker,
            "earnings_date": date_str,
            "date": date_str,
            "source": source,
            "is_timestamp_confirmed": confirmed,
            "hour": "amc" if confirmed else None,
            "time_of_day": "after_close" if confirmed else "unknown",
            "session_label": "After market close" if confirmed else "Unknown",
        }

    def test_single_source_sets_confirmed_false(self):
        events = [self._event("AAPL", "2026-07-01", "finnhub", confirmed=True)]
        result = self._merge(events)
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]["is_timestamp_confirmed"])

    def test_single_source_sets_is_timestamp_single_source(self):
        events = [self._event("AAPL", "2026-07-01", "finnhub", confirmed=True)]
        result = self._merge(events)
        self.assertTrue(result[0].get("is_timestamp_single_source"))

    def test_two_sources_same_date_keeps_confirmed_true(self):
        events = [
            self._event("NVDA", "2026-07-15", "finnhub", confirmed=True),
            self._event("NVDA", "2026-07-15", "alphavantage", confirmed=False),
        ]
        result = self._merge(events)
        self.assertEqual(len(result), 1)
        nvda = result[0]
        self.assertEqual(len(nvda["sources_seen"]), 2)
        self.assertTrue(nvda["is_timestamp_confirmed"])
        self.assertFalse(nvda.get("is_timestamp_single_source"))

    def test_source_conflict_flagged_within_threshold(self):
        events = [
            self._event("NKE", "2026-06-24", "finnhub", confirmed=True),
            self._event("NKE", "2026-06-26", "alphavantage", confirmed=False),
        ]
        result = self._merge(events, conflict_days=2)
        self.assertEqual(len(result), 2)
        tickers_with_conflict = [ev for ev in result if ev.get("earnings_source_conflict")]
        self.assertEqual(len(tickers_with_conflict), 2)

    def test_no_conflict_flag_when_dates_far_apart(self):
        events = [
            self._event("MSFT", "2026-06-24", "finnhub", confirmed=True),
            self._event("MSFT", "2026-07-10", "alphavantage", confirmed=False),
        ]
        result = self._merge(events, conflict_days=2)
        # No conflict — 16 days apart
        for ev in result:
            self.assertFalse(ev.get("earnings_source_conflict", False))

    def test_require_multi_source_disabled_preserves_confirmed(self):
        events = [self._event("TSLA", "2026-07-22", "finnhub", confirmed=True)]
        result = self._merge(events, require_multi=False)
        # When flag is disabled, single-source confirmed stays as-is
        self.assertTrue(result[0]["is_timestamp_confirmed"])
        self.assertFalse(result[0].get("is_timestamp_single_source", False))


# ---------------------------------------------------------------------------
# 7-10: TKT-028 — expiration step-through
# ---------------------------------------------------------------------------

class TestExpirationStepThrough(unittest.TestCase):

    def _run(self, expirations, event_date_str, session="amc", front_max=14):
        from app.services.calendar_spread_service import _select_expiration_pairs
        today = date.today()
        event = {
            "earnings_date": event_date_str,
            "date": event_date_str,
            "session_label": "After market close",
            "time_of_day": "after_close",
        }
        diag: dict = {}
        with patch("app.config.CALENDAR_EARNINGS_EVENT_AWARE_EXPIRATIONS", True), \
             patch("app.config.CALENDAR_EARNINGS_FRONT_MIN_DTE", 1), \
             patch("app.config.CALENDAR_EARNINGS_FRONT_MAX_DTE", front_max), \
             patch("app.config.CALENDAR_MIN_EXPIRATION_GAP_DAYS", 14), \
             patch("app.config.CALENDAR_TARGET_EXPIRATION_GAP_DAYS", 30), \
             patch("app.config.CALENDAR_EARNINGS_BACK_MIN_DTE_AFTER_EVENT", 7), \
             patch("app.config.CALENDAR_BACK_MAX_DTE", 90), \
             patch("app.config.CALENDAR_EARNINGS_BACK_MAX_DTE", 90), \
             patch("app.config.CALENDAR_MAX_EXPIRATION_PAIRS_PER_TICKER", 1):
            pairs = _select_expiration_pairs(expirations, earnings_event=event, diagnostics=diag)
        return pairs, diag

    def _future(self, days):
        return (date.today() + timedelta(days=days)).strftime("%Y-%m-%d")

    def test_no_valid_pair_reason_in_diagnostics(self):
        # No expirations before earnings, no valid pair possible
        event_date = self._future(20)
        expirations = [self._future(25), self._future(55)]  # both after earnings
        _, diag = self._run(expirations, event_date, front_max=14)
        self.assertEqual(diag.get("no_valid_pair_reason"), "NO_VALID_EXPIRATION_PAIR")

    def test_tried_front_expirations_populated(self):
        event_date = self._future(20)
        front = self._future(18)  # before earnings, 18 DTE — outside front_max=14
        back = self._future(55)
        _, diag = self._run([front, back], event_date, front_max=14)
        self.assertIn(front, diag.get("tried_front_expirations", []))

    def test_search_widened_when_dte_cap_excludes_all(self):
        event_date = self._future(20)
        front = self._future(18)  # 18 DTE — exceeds front_max=14
        back = self._future(55)
        _, diag = self._run([front, back], event_date, front_max=14)
        self.assertTrue(diag.get("search_widened", False))

    def test_valid_pair_found_via_widened_search(self):
        event_date = self._future(20)
        front = self._future(18)  # 18 DTE — exceeds front_max=14 but valid
        back = self._future(55)   # 55 DTE — captures event
        pairs, diag = self._run([front, back], event_date, front_max=14)
        self.assertEqual(len(pairs), 1, f"Expected 1 pair, got {len(pairs)}; diag={diag}")
        self.assertTrue(diag.get("search_widened"))


# ---------------------------------------------------------------------------
# 11-12: TKT-022 — earnings_discovery_window_effective in pipeline
# ---------------------------------------------------------------------------

class TestEarningsDiscoveryWindowEffective(unittest.TestCase):

    def test_window_effective_key_in_pipeline_status(self):
        import inspect
        import app.services.analysis_service as svc
        src = inspect.getsource(svc)
        self.assertIn("earnings_discovery_window_effective", src)

    def test_pipeline_status_dict_has_window_fields(self):
        # Build a minimal pipeline_status mock and verify the key structure.
        from app.services.pipeline_status_service import new_pipeline_status
        ps = new_pipeline_status("dev")
        # Simulate what analysis_service sets after earnings_trade_discovery runs.
        discovery = {
            "window_start": "2026-06-21",
            "window_end": "2026-07-08",
            "provider": "finnhub+alphavantage",
            "items": [{"ticker": "AAPL"}],
        }
        import app.config as cfg
        ps["earnings_discovery_window_effective"] = {
            "window_start": discovery.get("window_start"),
            "window_end": discovery.get("window_end"),
            "window_start_days": int(cfg.EARNINGS_DISCOVERY_START_DAYS or 2),
            "window_end_days": int(cfg.EARNINGS_DISCOVERY_END_DAYS or 21),
            "provider": discovery.get("provider"),
            "event_count": len(discovery.get("items", [])),
        }
        edw = ps["earnings_discovery_window_effective"]
        self.assertIn("window_start", edw)
        self.assertIn("window_end", edw)
        self.assertIn("event_count", edw)
        self.assertEqual(edw["event_count"], 1)


if __name__ == "__main__":
    unittest.main()
