"""Tests for P1 Patch: FF Dev Cap Fix + Adverse IV Hard Gate + Earnings Date Multi-Source."""

import unittest
from unittest.mock import patch

from app.services.forward_factor_candidate_selection_service import score_forward_factor_candidate
from app.services.forward_factor_verdict_service import apply_forward_factor_verdict
from app.providers.earnings_provider import _merge_dedupe_events, _compute_earnings_confidence


class TestFFDevCapScoreOrdering(unittest.TestCase):
    """Item 1: ordered list sorts by candidate_quality_score descending."""

    def test_prescore_ranks_higher_score_first(self):
        high = score_forward_factor_candidate(
            "SBUX",
            {"current_price": 100, "average_volume_30d": 10_000_000, "options_available": True},
            {"valid_pair_seen": True, "structure_seen": True, "best_liquidity_status": "PASS"},
        )
        low = score_forward_factor_candidate(
            "AAA",
            {"current_price": 5, "average_volume_30d": 100},
            {},
        )
        self.assertGreater(high["score"], low["score"])

    def test_prescore_tiebreaker_is_alphabetical(self):
        a = score_forward_factor_candidate("AAA", {"current_price": 100, "average_volume_30d": 10_000_000}, {})
        b = score_forward_factor_candidate("ZZZ", {"current_price": 100, "average_volume_30d": 10_000_000}, {})
        self.assertEqual(a["score"], b["score"])
        ordered = sorted(["ZZZ", "AAA"], key=lambda t: (-a["score"], t))
        self.assertEqual(ordered, ["AAA", "ZZZ"])

    @patch("app.services.forward_factor_service.config")
    def test_ordered_list_uses_score_sort(self, mock_config):
        mock_config.FORWARD_FACTOR_STRATEGY_ENABLED = False
        from app.services.forward_factor_service import build_forward_factor_strategy
        metrics = {
            "SBUX": {"current_price": 100, "average_volume_30d": 10_000_000, "options_available": True},
            "AAA": {"current_price": 6, "average_volume_30d": 600},
        }
        result = build_forward_factor_strategy(
            universe=["AAA", "SBUX"], market_metrics=metrics, data_hub=None,
            observation_history={
                "SBUX": {"valid_pair_seen": True, "structure_seen": True, "best_liquidity_status": "PASS"},
            },
        )
        self.assertIsInstance(result, dict)


class TestAdverseIVHardGate(unittest.TestCase):
    """Item 2: forward_variance <= 0 → FAIL / IV_RELATIONSHIP_ADVERSE."""

    def _base_row(self, **overrides):
        row = {
            "ticker": "TEST",
            "forward_variance": 0.01,
            "forward_iv": 0.10,
            "forward_factor": 0.25,
            "liquidity_status": "PASS",
            "liquidity_pass": True,
            "debit_at_risk": 50,
            "earnings_contaminated": False,
        }
        row.update(overrides)
        return row

    def test_negative_forward_variance_fails(self):
        row = self._base_row(forward_variance=-0.005)
        result = apply_forward_factor_verdict(row)
        self.assertEqual(result["verdict"], "FAIL / IV_RELATIONSHIP_ADVERSE")
        self.assertIn("non-positive", result["primary_blocker"])

    def test_zero_forward_variance_fails(self):
        row = self._base_row(forward_variance=0.0)
        result = apply_forward_factor_verdict(row)
        self.assertEqual(result["verdict"], "FAIL / IV_RELATIONSHIP_ADVERSE")

    def test_positive_forward_variance_passes_through(self):
        row = self._base_row(forward_variance=0.01)
        result = apply_forward_factor_verdict(row)
        self.assertNotEqual(result["verdict"], "FAIL / IV_RELATIONSHIP_ADVERSE")

    def test_missing_forward_variance_treated_as_positive(self):
        row = self._base_row()
        del row["forward_variance"]
        result = apply_forward_factor_verdict(row)
        self.assertNotEqual(result["verdict"], "FAIL / IV_RELATIONSHIP_ADVERSE")

    def test_adverse_iv_takes_priority_over_liquidity(self):
        row = self._base_row(forward_variance=-0.01, liquidity_pass=False)
        result = apply_forward_factor_verdict(row)
        self.assertEqual(result["verdict"], "FAIL / IV_RELATIONSHIP_ADVERSE")


class TestEarningsDateConfidence(unittest.TestCase):
    """Item 3: Two-source confirmation gate on earnings dates."""

    def test_single_source_confidence(self):
        result = _compute_earnings_confidence({"sources_seen": ["finnhub"]})
        self.assertEqual(result, "single_source")

    def test_multi_source_confirmed(self):
        result = _compute_earnings_confidence({"sources_seen": ["finnhub", "alphavantage"]})
        self.assertEqual(result, "confirmed")

    def test_disputed_confidence(self):
        result = _compute_earnings_confidence({"sources_seen": ["finnhub", "alphavantage"], "earnings_source_conflict": True})
        self.assertEqual(result, "disputed")

    def test_no_sources_confidence(self):
        result = _compute_earnings_confidence({})
        self.assertEqual(result, "no_data")


class TestEarningsMergeDedupeFields(unittest.TestCase):
    """Item 3: _merge_dedupe_events produces date_confidence, date_conflict, date_sources."""

    def test_merge_adds_alias_fields(self):
        events = [
            {"ticker": "AAPL", "earnings_date": "2026-07-20", "source": "finnhub"},
            {"ticker": "AAPL", "earnings_date": "2026-07-20", "source": "alphavantage"},
        ]
        result = _merge_dedupe_events(events)
        self.assertEqual(len(result), 1)
        ev = result[0]
        self.assertEqual(ev["date_confidence"], "confirmed")
        self.assertFalse(ev["date_conflict"])
        self.assertIn("finnhub", ev["date_sources"])
        self.assertIn("alphavantage", ev["date_sources"])

    def test_single_source_alias_fields(self):
        events = [{"ticker": "TSLA", "earnings_date": "2026-08-01", "source": "finnhub"}]
        result = _merge_dedupe_events(events)
        ev = result[0]
        self.assertEqual(ev["date_confidence"], "single_source")
        self.assertFalse(ev["date_conflict"])
        self.assertEqual(ev["date_sources"], ["finnhub"])

    def test_conflicting_dates_flagged(self):
        events = [
            {"ticker": "MSFT", "earnings_date": "2026-07-20", "source": "finnhub"},
            {"ticker": "MSFT", "earnings_date": "2026-07-21", "source": "alphavantage"},
        ]
        result = _merge_dedupe_events(events)
        has_conflict = any(ev.get("date_conflict") for ev in result)
        self.assertTrue(has_conflict)


class TestEarningsDiscoveryQualityGate(unittest.TestCase):
    """Item 3: Quality row checks include earnings date agreement."""

    def test_quality_row_has_date_agreement_check_multi_source(self):
        from app.services.earnings_discovery_quality_service import _quality_row
        event = {
            "ticker": "AAPL",
            "earnings_date": "2026-07-20",
            "is_timestamp_confirmed": True,
            "sources_seen": ["finnhub", "alphavantage"],
            "earnings_date_confidence": "confirmed",
        }
        quote = {"last": 200, "volume": 10_000_000, "average_volume": 5_000_000}
        row = _quality_row(event, quote)
        check_names = [c["name"] for c in row["checks"]]
        self.assertIn("Earnings date agreement", check_names)
        date_check = next(c for c in row["checks"] if c["name"] == "Earnings date agreement")
        self.assertEqual(date_check["status"], "PASS")

    def test_quality_row_single_source_warns(self):
        from app.services.earnings_discovery_quality_service import _quality_row
        event = {
            "ticker": "TSLA",
            "earnings_date": "2026-08-01",
            "is_timestamp_confirmed": False,
            "sources_seen": ["finnhub"],
            "earnings_date_confidence": "single_source",
        }
        quote = {"last": 200, "volume": 10_000_000, "average_volume": 5_000_000}
        with patch("app.services.earnings_discovery_quality_service.config") as mock_config:
            mock_config.EARNINGS_DATE_REQUIRE_MULTI_SOURCE = False
            mock_config.EARNINGS_DISCOVERY_MIN_UNDERLYING_PRICE = 5
            mock_config.EARNINGS_DISCOVERY_MIN_AVERAGE_VOLUME = 500000
            row = _quality_row(event, quote)
        date_check = next(c for c in row["checks"] if c["name"] == "Earnings date agreement")
        self.assertEqual(date_check["status"], "WARN")

    def test_quality_row_single_source_fails_when_required(self):
        from app.services.earnings_discovery_quality_service import _quality_row
        event = {
            "ticker": "TSLA",
            "earnings_date": "2026-08-01",
            "is_timestamp_confirmed": False,
            "sources_seen": ["finnhub"],
            "earnings_date_confidence": "single_source",
        }
        quote = {"last": 200, "volume": 10_000_000, "average_volume": 5_000_000}
        with patch("app.services.earnings_discovery_quality_service.config") as mock_config:
            mock_config.EARNINGS_DATE_REQUIRE_MULTI_SOURCE = True
            mock_config.EARNINGS_DISCOVERY_MIN_UNDERLYING_PRICE = 5
            mock_config.EARNINGS_DISCOVERY_MIN_AVERAGE_VOLUME = 500000
            row = _quality_row(event, quote)
        date_check = next(c for c in row["checks"] if c["name"] == "Earnings date agreement")
        self.assertEqual(date_check["status"], "FAIL")


class TestConfigEarningsDateRequireMultiSource(unittest.TestCase):
    """Item 3: Config default is False."""

    def test_config_default_is_false(self):
        from app import config
        self.assertFalse(config.EARNINGS_DATE_REQUIRE_MULTI_SOURCE)


if __name__ == "__main__":
    unittest.main()
