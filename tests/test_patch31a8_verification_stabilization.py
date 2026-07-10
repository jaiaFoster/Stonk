"""
Tests for ASA Patch 31A.8 — Verification-Driven Stabilization.

Covers four tracked defects:
  TKT-SEMANTIC-REJECTED-ELIGIBLE    — rejected rows must never be DO-eligible
  TKT-OPEN-OPTIONS-ACCOUNT-ALIAS-DEDUP — cross-account alias position dedup
  TKT-OPEN-POSITIONS-LIFECYCLE-CARDINALITY — active_calendar_count at top level
  TKT-FF-FORWARD-VARIANCE-DIAGNOSTICS — per-pair diagnostic fields on FF failure
"""

import sys
import types
import unittest
from unittest.mock import patch

# Stub robin_stocks before importing robinhood_provider (pre-existing env issue:
# _cffi_backend missing causes pyo3 panic if the real package is loaded).
if "robin_stocks.robinhood" not in sys.modules:
    _rs_stub = types.ModuleType("robin_stocks")
    _rh_stub = types.ModuleType("robin_stocks.robinhood")
    for _attr in ("login", "logout"):
        setattr(_rh_stub, _attr, lambda *a, **k: None)
    for _ns in ("account", "crypto", "options"):
        setattr(_rh_stub, _ns, types.SimpleNamespace())
    sys.modules["robin_stocks"] = _rs_stub
    sys.modules["robin_stocks.robinhood"] = _rh_stub

from app.services.strategy_row_normalization_service import _daily_opportunity_eligible
from app.strategies.earnings_calendar_universal import build_earnings_calendar_universal_row
from app.services.forward_factor_service import calculate_forward_factor

# open_options_service and robinhood_provider both import robin_stocks; guard
# against pyo3 panic from prior test files that corrupted the import machinery.
try:
    from app.services.open_options_service import _finalize_result
    from app.providers.robinhood_provider import _dedupe_key_for_option_position
    _RH_PROVIDER_AVAILABLE = True
except BaseException:
    _finalize_result = None  # type: ignore[assignment]
    _dedupe_key_for_option_position = None  # type: ignore[assignment]
    _RH_PROVIDER_AVAILABLE = False


# ── Ticket 1: TKT-SEMANTIC-REJECTED-ELIGIBLE ────────────────────────────────

class SemanticRejectedEligibleTests(unittest.TestCase):
    """rejected_candidate rows must never have daily_opportunity.eligible=True."""

    def _make_rejected_row(self, calendar_entry_allowed=True, verdict="FAIL / NOT AN EARNINGS SETUP"):
        return {
            "ticker": "AAPL",
            "score": 80,
            "verdict": verdict,
            "action": verdict,
            "calendar_entry_allowed": calendar_entry_allowed,
            "daily_opportunity_eligible": calendar_entry_allowed,
            "row_type": "rejected_candidate",
        }

    def test_rejected_candidate_universal_row_is_never_eligible(self):
        row = self._make_rejected_row(calendar_entry_allowed=True)
        result = build_earnings_calendar_universal_row(row)
        self.assertFalse(result["daily_opportunity"]["eligible"])
        self.assertFalse(result.get("daily_opportunity_eligible"))

    def test_rejected_candidate_eligible_flag_overwritten_to_false(self):
        """Even if incoming row has daily_opportunity_eligible=True, universal builder clears it."""
        row = self._make_rejected_row(calendar_entry_allowed=True)
        row["daily_opportunity_eligible"] = True
        result = build_earnings_calendar_universal_row(row)
        self.assertFalse(result["daily_opportunity"]["eligible"])

    def test_observation_row_eligible_if_calendar_entry_allowed(self):
        row = {
            "ticker": "MSFT",
            "score": 75,
            "verdict": "WATCH / REVIEW",
            "calendar_entry_allowed": True,
            "daily_opportunity_eligible": True,
            "row_type": "observation",
        }
        result = build_earnings_calendar_universal_row(row)
        self.assertTrue(result["daily_opportunity"]["eligible"])

    def test_normalization_service_blocks_fail_verdict(self):
        row = {"verdict": "FAIL / NOT AN EARNINGS SETUP", "calendar_entry_allowed": True}
        self.assertFalse(_daily_opportunity_eligible(row, "earnings_calendar", {}))

    def test_normalization_service_blocks_avoid_verdict(self):
        row = {"verdict": "AVOID THIS SETUP", "calendar_entry_allowed": True}
        self.assertFalse(_daily_opportunity_eligible(row, "earnings_calendar", {}))

    def test_normalization_service_blocks_not_an_earnings_setup(self):
        row = {"action": "NOT AN EARNINGS SETUP", "calendar_entry_allowed": True}
        self.assertFalse(_daily_opportunity_eligible(row, "earnings_calendar", {}))

    def test_normalization_service_allows_clean_watch_row(self):
        row = {"verdict": "WATCH / REVIEW", "calendar_entry_allowed": True}
        self.assertTrue(_daily_opportunity_eligible(row, "earnings_calendar", {}))

    def test_normalization_service_blocks_clean_row_if_not_calendar_entry_allowed(self):
        row = {"verdict": "WATCH / REVIEW", "calendar_entry_allowed": False}
        self.assertFalse(_daily_opportunity_eligible(row, "earnings_calendar", {}))


# ── Ticket 2: TKT-OPEN-OPTIONS-ACCOUNT-ALIAS-DEDUP ──────────────────────────

@unittest.skipUnless(_RH_PROVIDER_AVAILABLE, "robin_stocks unavailable in this test environment")
class AccountAliasDedupTests(unittest.TestCase):
    """Same broker position seen under Investing and Individual aliases must dedup."""

    def _position_raw(self, position_id="pos-123", option_url="https://broker/options/opt-1"):
        return {"id": position_id, "url": option_url, "chain_symbol": "SBUX"}

    def test_same_position_id_deduplicates_across_accounts(self):
        raw = self._position_raw()
        key_investing = _dedupe_key_for_option_position(raw, None, "Investing")
        key_individual = _dedupe_key_for_option_position(raw, "ABC123456", "Individual")
        self.assertEqual(key_investing, key_individual)

    def test_dedup_key_uses_broker_position_id_namespace(self):
        raw = self._position_raw()
        key = _dedupe_key_for_option_position(raw, None, "Investing")
        self.assertEqual(key[0], "broker_position_id")

    def test_different_position_ids_not_deduplicated(self):
        raw_a = self._position_raw(position_id="pos-001")
        raw_b = self._position_raw(position_id="pos-002")
        key_a = _dedupe_key_for_option_position(raw_a, None, "Investing")
        key_b = _dedupe_key_for_option_position(raw_b, None, "Investing")
        self.assertNotEqual(key_a, key_b)

    def test_fallback_to_option_url_includes_account(self):
        """Without id field, falls back to (account_number, option_url) — account-scoped."""
        raw = {"option": "https://broker/options/opt-1", "chain_symbol": "SBUX"}
        key_investing = _dedupe_key_for_option_position(raw, None, "Investing")
        key_individual = _dedupe_key_for_option_position(raw, "ABC123", "Individual")
        # account-scoped fallback: these SHOULD differ
        self.assertNotEqual(key_investing, key_individual)

    def test_url_field_also_global_dedup(self):
        """url field (without id) should also produce a cross-account stable key."""
        raw = {"url": "https://broker/positions/pos-999", "chain_symbol": "SBUX"}
        key_a = _dedupe_key_for_option_position(raw, None, "Investing")
        key_b = _dedupe_key_for_option_position(raw, "XYZ999", "Individual")
        self.assertEqual(key_a, key_b)
        self.assertEqual(key_a[0], "broker_position_id")


# ── Ticket 3: TKT-OPEN-POSITIONS-LIFECYCLE-CARDINALITY ─────────────────────

@unittest.skipUnless(_RH_PROVIDER_AVAILABLE, "robin_stocks unavailable in this test environment")
class ActiveCalendarCountTests(unittest.TestCase):
    """active_calendar_count must be present at top level of _finalize_result output."""

    def _make_result(self, num_calendars=2):
        calendars = [
            {"underlying": "SBUX", "option_type": "call", "strike": 110.0}
            for _ in range(num_calendars)
        ]
        return {
            "option_legs": [],
            "calendars": calendars,
            "verticals": [],
            "single_legs": [],
            "positions": [],
            "account_ids": ["acc-1"],
        }

    def test_active_calendar_count_at_top_level(self):
        result = _finalize_result(self._make_result(num_calendars=2))
        self.assertIn("active_calendar_count", result)
        self.assertEqual(result["active_calendar_count"], 2)

    def test_active_calendar_count_zero_when_no_calendars(self):
        result = _finalize_result(self._make_result(num_calendars=0))
        self.assertEqual(result["active_calendar_count"], 0)

    def test_active_calendar_count_matches_summary_calendar_count(self):
        result = _finalize_result(self._make_result(num_calendars=3))
        self.assertEqual(result["active_calendar_count"], result["summary"]["calendar_count"])

    def _api_response_with_open_opts(self, open_opts_dict):
        """Call build_open_positions_response with a mocked snapshot repo."""
        import tempfile
        from pathlib import Path
        from unittest.mock import MagicMock, patch as upatch
        from app.api.open_positions_api import build_open_positions_response
        from app.services.report_snapshot_service import ReportSnapshotRepository

        fake_snapshot = {"run_id": "run-1", "completed_at": "2026-07-10T10:00:00Z"}
        fake_summary = {
            "report_data": {
                "tradier_snapshot": {
                    "_open_options_positions": open_opts_dict,
                    "_calendar_lifecycle_checks": {},
                }
            }
        }
        mock_repo = MagicMock(spec=ReportSnapshotRepository)
        mock_repo.latest_success.return_value = fake_snapshot
        mock_repo.load_summary.return_value = fake_summary

        with upatch("app.services.report_snapshot_service.ReportSnapshotRepository", return_value=mock_repo):
            return build_open_positions_response()

    def test_api_reads_active_calendar_count_from_top_level(self):
        """The API helper returns active_calendar_count from the top-level key."""
        open_opts = {
            "active_calendar_count": 2,
            "has_open_calendars": True,
            "has_open_verticals": False,
        }
        response = self._api_response_with_open_opts(open_opts)
        self.assertEqual(response["active_calendar_count"], 2)

    def test_api_fallback_to_summary_calendar_count(self):
        """Legacy snapshots without top-level key fall back to summary.calendar_count."""
        open_opts = {
            "has_open_calendars": True,
            "has_open_verticals": False,
            "summary": {"calendar_count": 2},
        }
        response = self._api_response_with_open_opts(open_opts)
        self.assertEqual(response["active_calendar_count"], 2)


# ── Ticket 4: TKT-FF-FORWARD-VARIANCE-DIAGNOSTICS ───────────────────────────

class FFForwardVarianceDiagnosticsTests(unittest.TestCase):
    """calculate_forward_factor raises ValueError with diagnostic numerator/denominator."""

    def test_inverted_iv_term_structure_raises_with_diagnostic_values(self):
        """When back IV < front IV causing negative variance, error includes term values."""
        with self.assertRaises(ValueError) as ctx:
            # front_iv=0.60, back_iv=0.30 with short DTEs → inverted structure
            calculate_forward_factor(front_iv=0.60, back_iv=0.30, front_dte=30, back_dte=60)
        error_msg = str(ctx.exception)
        self.assertIn("INVALID_FORWARD_VARIANCE", error_msg)
        self.assertIn("back_term=", error_msg)
        self.assertIn("front_term=", error_msg)
        self.assertIn("numerator=", error_msg)
        self.assertIn("denominator=", error_msg)

    def test_valid_forward_variance_succeeds(self):
        result = calculate_forward_factor(front_iv=0.30, back_iv=0.35, front_dte=30, back_dte=60)
        self.assertGreater(result["forward_variance"], 0)
        self.assertIn("forward_factor", result)

    def test_invalid_expiration_order_raises(self):
        with self.assertRaises(ValueError) as ctx:
            calculate_forward_factor(front_iv=0.30, back_iv=0.35, front_dte=60, back_dte=30)
        self.assertIn("INVALID_EXPIRATION_ORDER", str(ctx.exception))

    def test_ff_rejection_code_present_in_blocked_row(self):
        """When calculate_forward_factor raises, the blocked row has ff_rejection_code."""
        from app.services.forward_factor_service import _blocked
        row = _blocked(
            "AAPL", "FAIL / INVALID FORWARD VARIANCE",
            "INVALID_FORWARD_VARIANCE: test",
            ff_candidate_stage="incomplete",
            ff_rejection_code="INVALID_FORWARD_VARIANCE",
            ff_front_iv_used=0.60,
            ff_back_iv_used=0.30,
            ff_front_dte_used=30,
            ff_back_dte_used=60,
            ff_calculation_status="failed",
        )
        self.assertEqual(row.get("ff_rejection_code"), "INVALID_FORWARD_VARIANCE")
        self.assertEqual(row.get("ff_front_iv_used"), 0.60)
        self.assertEqual(row.get("ff_back_iv_used"), 0.30)
        self.assertEqual(row.get("ff_front_dte_used"), 30)
        self.assertEqual(row.get("ff_back_dte_used"), 60)
        self.assertEqual(row.get("ff_calculation_status"), "failed")


if __name__ == "__main__":
    unittest.main()
