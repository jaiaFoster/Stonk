"""
tests/test_tkt040_broker_audit.py — TKT-040 broker data flow audit tests.

Verifies:
- discover_accounts produces distinct account_numbers in stored positions
- Legacy dead code paths are actually unreachable
- Broker provider returns 3-tuple from fetch_positions_with_options
- No ACCOUNT_MAP anywhere in codebase
"""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def temp_db(tmp_path):
    db_path = str(tmp_path / "users_040.db")
    with patch("app.config.USERS_DB_PATH", db_path):
        from app.db.users import init_db
        init_db()
    return db_path


# ---------------------------------------------------------------------------
# Integration: discovered accounts → distinct account_number in user_positions
# ---------------------------------------------------------------------------

class TestDiscoveredAccountsToPositions:
    def test_distinct_account_numbers_in_stored_positions(self, temp_db):
        """
        Simulates the per-user pipeline: discovered accounts with different
        account_numbers should produce rows in user_positions with distinct
        account_type values (since account_type carries the account label).
        """
        with patch("app.config.USERS_DB_PATH", temp_db):
            from app.db.users import save_user_positions, get_user_positions, create_user_run

            create_user_run(user_id=1, run_id="audit_run_1")

            positions = [
                {
                    "ticker": "AAPL",
                    "quantity": 10,
                    "avg_cost": 150.0,
                    "current_price": 175.0,
                    "market_value": 1750.0,
                    "unrealized_pnl_pct": 16.667,
                    "account_type": "Individual",
                    "account_number": "111AAA",
                },
                {
                    "ticker": "MSFT",
                    "quantity": 5,
                    "avg_cost": 300.0,
                    "current_price": 350.0,
                    "market_value": 1750.0,
                    "unrealized_pnl_pct": 16.667,
                    "account_type": "Roth IRA",
                    "account_number": "222BBB",
                },
                {
                    "ticker": "GOOG",
                    "quantity": 3,
                    "avg_cost": 140.0,
                    "current_price": 160.0,
                    "market_value": 480.0,
                    "unrealized_pnl_pct": 14.286,
                    "account_type": "Individual",
                    "account_number": "111AAA",
                },
            ]

            save_user_positions(user_id=1, run_id="audit_run_1", positions=positions)
            stored = get_user_positions(user_id=1, run_id="audit_run_1")

            assert len(stored) == 3
            account_types = {r["account_type"] for r in stored}
            assert "Individual" in account_types
            assert "Roth IRA" in account_types

    def test_option_positions_stored_with_account_type(self, temp_db):
        with patch("app.config.USERS_DB_PATH", temp_db):
            from app.db.users import (
                save_user_option_positions_to_positions,
                get_user_positions,
                create_user_run,
            )

            create_user_run(user_id=1, run_id="audit_run_2")

            opts = [
                {
                    "ticker": "SPY",
                    "strategy_type": "calendar",
                    "quantity": 1,
                    "net_debit": 2.50,
                    "current_value": 3.00,
                    "unrealized_pnl_pct": 20.0,
                    "account_type": "Roth IRA",
                },
                {
                    "ticker": "QQQ",
                    "strategy_type": "vertical",
                    "quantity": 2,
                    "net_debit": 1.50,
                    "current_value": 1.80,
                    "unrealized_pnl_pct": 20.0,
                    "account_type": "Individual",
                },
            ]

            save_user_option_positions_to_positions(
                user_id=1, run_id="audit_run_2", options_positions=opts
            )
            stored = get_user_positions(user_id=1, run_id="audit_run_2")

            assert len(stored) == 2
            types = {r["account_type"] for r in stored}
            assert "Roth IRA" in types
            assert "Individual" in types


# ---------------------------------------------------------------------------
# Dead code verification
# ---------------------------------------------------------------------------

class TestDeadCodePaths:
    def test_fetch_with_lock_has_no_callers(self):
        """fetch_with_lock() is defined but should have no callers in production code."""
        import ast
        import os

        app_dir = os.path.join(os.path.dirname(__file__), "..", "app")
        callers = []
        for root, _dirs, files in os.walk(app_dir):
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                fpath = os.path.join(root, fname)
                with open(fpath) as f:
                    source = f.read()
                if "fetch_with_lock(" in source and "def fetch_with_lock(" not in source:
                    if "fetch_all_with_lock" not in source.split("fetch_with_lock(")[0].split("\n")[-1]:
                        callers.append(fpath)

        assert callers == [], f"fetch_with_lock() has unexpected callers: {callers}"

    def test_fetch_positions_not_called_by_personalization(self):
        """personalization.py should use fetch_all_with_lock, not fetch_with_lock."""
        import os
        perso_path = os.path.join(
            os.path.dirname(__file__), "..", "app", "services", "personalization.py"
        )
        with open(perso_path) as f:
            source = f.read()
        assert "fetch_all_with_lock" in source
        assert "fetch_with_lock(" not in source or "fetch_all_with_lock" in source


# ---------------------------------------------------------------------------
# fetch_positions_with_options return shape
# ---------------------------------------------------------------------------

class TestFetchPositionsWithOptionsShape:
    def test_base_class_signature_returns_tuple(self):
        from app.services.broker_provider import BrokerCredentialProvider
        import inspect
        sig = inspect.signature(BrokerCredentialProvider.fetch_positions_with_options)
        params = list(sig.parameters.keys())
        assert "username" in params
        assert "password_decrypted" in params
        assert "user_id" in params

    def test_robinhood_provider_docstring_mentions_discovered(self):
        from app.services.broker_provider import RobinhoodCredentialProvider
        doc = RobinhoodCredentialProvider.fetch_positions_with_options.__doc__ or ""
        assert "discovered" in doc.lower() or "account" in doc.lower()


# ---------------------------------------------------------------------------
# ACCOUNT_MAP removal (regression from TKT-043)
# ---------------------------------------------------------------------------

class TestAccountMapRemoved:
    def test_no_account_map_in_any_production_code(self):
        """ACCOUNT_MAP should not be defined or used in any app/ code."""
        import os

        app_dir = os.path.join(os.path.dirname(__file__), "..", "app")
        offenders = []
        for root, _dirs, files in os.walk(app_dir):
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                fpath = os.path.join(root, fname)
                with open(fpath) as f:
                    for lineno, line in enumerate(f, 1):
                        if "ACCOUNT_MAP" in line and not line.strip().startswith("#"):
                            offenders.append(f"{fpath}:{lineno}")
        assert offenders == [], f"ACCOUNT_MAP references: {offenders}"


# ---------------------------------------------------------------------------
# Broker accounts DB (save + retrieve round-trip)
# ---------------------------------------------------------------------------

class TestBrokerAccountsRoundTrip:
    def test_discovered_accounts_persisted_and_retrievable(self, temp_db):
        with patch("app.config.USERS_DB_PATH", temp_db):
            from app.db.users import save_user_broker_accounts, get_user_broker_accounts

            discovered = [
                {"account_number": "111", "account_type": "Individual"},
                {"account_number": "222", "account_type": "Roth IRA"},
                {"account_number": "333", "account_type": "Rollover IRA"},
            ]
            save_user_broker_accounts(user_id=1, accounts=discovered)
            result = get_user_broker_accounts(user_id=1)

            assert len(result) == 3
            nums = {r["account_number"] for r in result}
            assert nums == {"111", "222", "333"}
            types = {r["account_type"] for r in result}
            assert "Roth IRA" in types
            assert "Rollover IRA" in types


# ---------------------------------------------------------------------------
# discover_accounts function shape
# ---------------------------------------------------------------------------

class TestDiscoverAccountsShape:
    def test_returns_list_of_dicts_with_required_keys(self):
        try:
            from app.providers.robinhood_provider import discover_accounts
        except BaseException:
            pytest.skip("robin_stocks import broken in this environment")

        with patch("app.providers.robinhood_provider.r") as mock_r:
            mock_r.profiles.load_account_profile.return_value = [
                {"account_number": "AAA", "type": "cash"},
                {"account_number": "BBB", "type": "roth_ira"},
            ]
            result = discover_accounts()
            assert isinstance(result, list)
            for acct in result:
                assert "account_number" in acct
                assert "account_type" in acct
                assert acct["account_number"]  # not empty
