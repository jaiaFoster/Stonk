"""TKT-BROKER-RAW-LOGS — account_id must be masked before logging or appending to errors.

Before the fix, open_options_service.py emitted raw account IDs in logger calls and
result["errors"] entries.  After the fix every occurrence uses _mask_account_id_for_log
so only the last-4 digits (prefixed with ***) can appear.

Tests cover:
- _mask_account_id_for_log behaviour for short / normal / None inputs
- Success-path logger message contains masked ID, not raw ID
- Error-path logger message contains masked ID, not raw ID
- Error-path result["errors"] entry contains masked ID, not raw ID
"""
from __future__ import annotations

import sys
import types

# ── pyo3 panic guard ──────────────────────────────────────────────────────────
_rh_stub = types.ModuleType("robin_stocks")
_rh_stub.robinhood = types.ModuleType("robin_stocks.robinhood")
sys.modules.setdefault("robin_stocks", _rh_stub)
sys.modules.setdefault("robin_stocks.robinhood", _rh_stub.robinhood)

import pytest


ACCOUNT_ID = "ABC1234567"   # 10-char real-looking account ID
MASKED_ID  = "***4567"      # what _mask_account_id_for_log should produce


class TestMaskAccountIdForLog:
    """Unit tests for the masking helper itself."""

    @staticmethod
    def _fn():
        from app.providers.tradier_provider import _mask_account_id_for_log
        return _mask_account_id_for_log

    def test_normal_account_id_last_four_shown(self):
        mask = self._fn()
        assert mask("ABC1234567") == "***4567"

    def test_short_account_id_fully_masked(self):
        mask = self._fn()
        assert mask("AB") == "***"

    def test_exactly_four_chars_fully_masked(self):
        mask = self._fn()
        assert mask("1234") == "***"

    def test_five_chars_last_four_shown(self):
        mask = self._fn()
        assert mask("12345") == "***2345"

    def test_none_returns_stars(self):
        mask = self._fn()
        assert mask(None) == "***"

    def test_empty_string_returns_stars(self):
        mask = self._fn()
        assert mask("") == "***"


class TestOpenOptionsServiceLogRedaction:
    """Logger output and error messages must not contain raw account IDs."""

    def _run_success_path(self, account_id: str) -> list[str]:
        """Run the open-options fetch with a stub provider that returns 1 position."""
        from unittest import mock

        log_lines: list[str] = []

        stub_position = {
            "symbol": "SBUX210917C00095000",
            "instrument_type": "option",
            "quantity": 1,
            "average_buy_price": "1.50",
            "account_number": account_id,
        }

        stub_provider = mock.MagicMock()
        stub_provider.is_configured = True
        stub_provider.get_account_positions.return_value = [stub_position]

        with (
            mock.patch("app.services.open_options_service.TradierProvider", return_value=stub_provider),
            mock.patch("app.services.open_options_service._resolve_account_ids", return_value=[account_id]),
            mock.patch("app.services.open_options_service._configured_robinhood_option_accounts", return_value=[]),
            mock.patch("app.config.OPEN_OPTIONS_MAX_ACCOUNTS", 5),
        ):
            from app.services.open_options_service import detect_open_options_positions
            detect_open_options_positions(log_print=log_lines.append)

        return log_lines

    def _run_error_path(self, account_id: str) -> tuple[list[str], list[str]]:
        """Run the open-options fetch with a stub provider that raises an exception."""
        from unittest import mock

        log_lines: list[str] = []
        error_lines: list[str] = []

        stub_provider = mock.MagicMock()
        stub_provider.is_configured = True
        stub_provider.get_account_positions.side_effect = RuntimeError("Connection timeout")

        with (
            mock.patch("app.services.open_options_service.TradierProvider", return_value=stub_provider),
            mock.patch("app.services.open_options_service._resolve_account_ids", return_value=[account_id]),
            mock.patch("app.services.open_options_service._configured_robinhood_option_accounts", return_value=[]),
            mock.patch("app.config.OPEN_OPTIONS_MAX_ACCOUNTS", 5),
        ):
            from app.services.open_options_service import detect_open_options_positions
            result = detect_open_options_positions(log_print=log_lines.append)
            error_lines = result.get("errors", [])

        return log_lines, error_lines

    def test_success_log_does_not_contain_raw_account_id(self):
        log_lines = self._run_success_path(ACCOUNT_ID)
        for line in log_lines:
            assert ACCOUNT_ID not in line, (
                f"Raw account_id {ACCOUNT_ID!r} found in log line: {line!r}"
            )

    def test_success_log_contains_masked_account_id(self):
        log_lines = self._run_success_path(ACCOUNT_ID)
        matching = [l for l in log_lines if MASKED_ID in l]
        assert matching, (
            f"Expected masked ID {MASKED_ID!r} in at least one log line. Lines: {log_lines}"
        )

    def test_error_log_does_not_contain_raw_account_id(self):
        log_lines, _ = self._run_error_path(ACCOUNT_ID)
        for line in log_lines:
            assert ACCOUNT_ID not in line, (
                f"Raw account_id {ACCOUNT_ID!r} found in error log line: {line!r}"
            )

    def test_error_log_contains_masked_account_id(self):
        log_lines, _ = self._run_error_path(ACCOUNT_ID)
        matching = [l for l in log_lines if MASKED_ID in l]
        assert matching, (
            f"Expected masked ID {MASKED_ID!r} in at least one error log line. Lines: {log_lines}"
        )

    def test_error_result_errors_does_not_contain_raw_account_id(self):
        _, error_lines = self._run_error_path(ACCOUNT_ID)
        for entry in error_lines:
            assert ACCOUNT_ID not in entry, (
                f"Raw account_id {ACCOUNT_ID!r} found in result['errors']: {entry!r}"
            )

    def test_error_result_errors_contains_masked_account_id(self):
        _, error_lines = self._run_error_path(ACCOUNT_ID)
        matching = [e for e in error_lines if MASKED_ID in e]
        assert matching, (
            f"Expected masked ID {MASKED_ID!r} in result['errors']. Entries: {error_lines}"
        )
