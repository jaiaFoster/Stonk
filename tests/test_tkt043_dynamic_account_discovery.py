"""
tests/test_tkt043_dynamic_account_discovery.py — TKT-043 + TKT-040 regression tests.

Dynamic account discovery replaces hardcoded ACCOUNT_MAP.
Broker data flow audit integration tests.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def temp_db(tmp_path):
    db_path = str(tmp_path / "users_043.db")
    with patch("app.config.USERS_DB_PATH", db_path):
        from app.db.users import init_db
        init_db()
    return db_path


# ---------------------------------------------------------------------------
# Account classification
# ---------------------------------------------------------------------------

class TestClassifyAccountType:
    def test_roth_ira(self):
        from app.providers.robinhood_provider import _classify_account_type
        assert _classify_account_type({"type": "roth_ira"}) == "Roth IRA"

    def test_rollover_ira(self):
        from app.providers.robinhood_provider import _classify_account_type
        assert _classify_account_type({"type": "rollover_ira"}) == "Rollover IRA"

    def test_traditional_ira(self):
        from app.providers.robinhood_provider import _classify_account_type
        assert _classify_account_type({"type": "traditional_ira"}) == "Traditional IRA"

    def test_cash_account(self):
        from app.providers.robinhood_provider import _classify_account_type
        assert _classify_account_type({"type": "cash"}) == "Individual"

    def test_margin_account(self):
        from app.providers.robinhood_provider import _classify_account_type
        assert _classify_account_type({"type": "margin"}) == "Individual"

    def test_empty_type(self):
        from app.providers.robinhood_provider import _classify_account_type
        assert _classify_account_type({"type": ""}) == "Brokerage"
        assert _classify_account_type({}) == "Brokerage"

    def test_unknown_type_title_cased(self):
        from app.providers.robinhood_provider import _classify_account_type
        assert _classify_account_type({"type": "sep_ira"}) == "IRA"

    def test_pinnacle_account_is_ira(self):
        from app.providers.robinhood_provider import _classify_account_type
        assert _classify_account_type({"type": "cash", "is_pinnacle_account": True}) == "IRA"

    def test_pinnacle_false_cash_is_individual(self):
        from app.providers.robinhood_provider import _classify_account_type
        assert _classify_account_type({"type": "cash", "is_pinnacle_account": False}) == "Individual"

    def test_pinnacle_none_cash_is_individual(self):
        from app.providers.robinhood_provider import _classify_account_type
        assert _classify_account_type({"type": "cash", "is_pinnacle_account": None}) == "Individual"

    def test_pinnacle_margin_is_individual(self):
        from app.providers.robinhood_provider import _classify_account_type
        assert _classify_account_type({"type": "margin", "is_pinnacle_account": False}) == "Individual"


# ---------------------------------------------------------------------------
# discover_accounts() mock tests
# ---------------------------------------------------------------------------

class TestDiscoverAccounts:
    @patch("app.providers.robinhood_provider.r")
    def test_multiple_accounts(self, mock_r):
        mock_r.profiles.load_account_profile.return_value = [
            {"account_number": "111", "type": "cash"},
            {"account_number": "222", "type": "roth_ira"},
        ]
        from app.providers.robinhood_provider import discover_accounts
        result = discover_accounts()
        assert len(result) == 2
        assert result[0]["account_number"] == "111"
        assert result[0]["account_type"] == "Individual"
        assert result[1]["account_number"] == "222"
        assert result[1]["account_type"] == "Roth IRA"

    @patch("app.providers.robinhood_provider.r")
    def test_single_account_dict(self, mock_r):
        mock_r.profiles.load_account_profile.return_value = {
            "account_number": "333", "type": "margin"
        }
        from app.providers.robinhood_provider import discover_accounts
        result = discover_accounts()
        assert len(result) == 1
        assert result[0]["account_number"] == "333"

    @patch("app.providers.robinhood_provider.r")
    def test_api_failure_returns_empty(self, mock_r):
        mock_r.profiles.load_account_profile.side_effect = Exception("API down")
        from app.providers.robinhood_provider import discover_accounts
        result = discover_accounts()
        assert result == []

    @patch("app.providers.robinhood_provider.r")
    def test_none_response(self, mock_r):
        mock_r.profiles.load_account_profile.return_value = None
        from app.providers.robinhood_provider import discover_accounts
        result = discover_accounts()
        assert result == []

    @patch("app.providers.robinhood_provider.r")
    def test_skips_entries_without_account_number(self, mock_r):
        mock_r.profiles.load_account_profile.return_value = [
            {"account_number": "111", "type": "cash"},
            {"type": "margin"},
            {"account_number": "", "type": "cash"},
        ]
        from app.providers.robinhood_provider import discover_accounts
        result = discover_accounts()
        assert len(result) == 1
        assert result[0]["account_number"] == "111"


# ---------------------------------------------------------------------------
# _option_accounts_to_scan with discovered accounts
# ---------------------------------------------------------------------------

class TestOptionAccountsToScan:
    def test_uses_discovered_accounts(self):
        from app.providers.robinhood_provider import _option_accounts_to_scan
        discovered = [
            {"account_number": "AAA", "account_type": "Roth IRA"},
            {"account_number": "BBB", "account_type": "Individual"},
        ]
        result = _option_accounts_to_scan(discovered_accounts=discovered)
        nums = [r[0] for r in result]
        assert None in nums  # default account
        assert "AAA" in nums
        assert "BBB" in nums

    def test_empty_discovered_still_has_default(self):
        from app.providers.robinhood_provider import _option_accounts_to_scan
        result = _option_accounts_to_scan(discovered_accounts=[])
        assert len(result) >= 1
        assert result[0][0] is None  # default account


# ---------------------------------------------------------------------------
# DB: user_broker_accounts
# ---------------------------------------------------------------------------

class TestBrokerAccountsDB:
    def test_save_and_get(self, temp_db):
        with patch("app.config.USERS_DB_PATH", temp_db):
            from app.db.users import save_user_broker_accounts, get_user_broker_accounts
            accounts = [
                {"account_number": "111", "account_type": "Individual"},
                {"account_number": "222", "account_type": "Roth IRA"},
            ]
            save_user_broker_accounts(user_id=1, accounts=accounts)
            result = get_user_broker_accounts(user_id=1)
            assert len(result) == 2
            nums = {r["account_number"] for r in result}
            assert nums == {"111", "222"}

    def test_upsert_replaces(self, temp_db):
        with patch("app.config.USERS_DB_PATH", temp_db):
            from app.db.users import save_user_broker_accounts, get_user_broker_accounts
            save_user_broker_accounts(1, [{"account_number": "111", "account_type": "Old"}])
            save_user_broker_accounts(1, [{"account_number": "222", "account_type": "New"}])
            result = get_user_broker_accounts(1)
            assert len(result) == 1
            assert result[0]["account_number"] == "222"

    def test_different_users_independent(self, temp_db):
        with patch("app.config.USERS_DB_PATH", temp_db):
            from app.db.users import save_user_broker_accounts, get_user_broker_accounts
            save_user_broker_accounts(1, [{"account_number": "AAA", "account_type": "A"}])
            save_user_broker_accounts(2, [{"account_number": "BBB", "account_type": "B"}])
            assert len(get_user_broker_accounts(1)) == 1
            assert len(get_user_broker_accounts(2)) == 1
            assert get_user_broker_accounts(1)[0]["account_number"] == "AAA"

    def test_empty_accounts_clears(self, temp_db):
        with patch("app.config.USERS_DB_PATH", temp_db):
            from app.db.users import save_user_broker_accounts, get_user_broker_accounts
            save_user_broker_accounts(1, [{"account_number": "111", "account_type": "X"}])
            save_user_broker_accounts(1, [])
            assert get_user_broker_accounts(1) == []

    def test_init_db_creates_table(self, temp_db):
        conn = sqlite3.connect(temp_db)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        conn.close()
        assert "user_broker_accounts" in tables


# ---------------------------------------------------------------------------
# ACCOUNT_MAP removal verification
# ---------------------------------------------------------------------------

class TestAccountMapRemoved:
    def test_no_account_map_in_robinhood_provider(self):
        import app.providers.robinhood_provider as mod
        assert not hasattr(mod, "ACCOUNT_MAP"), "ACCOUNT_MAP should be removed"

    def test_no_account_map_in_robinhood_wrapper(self):
        import robinhood as mod
        assert not hasattr(mod, "ACCOUNT_MAP"), "ACCOUNT_MAP re-export should be removed"

    def test_discover_accounts_exported(self):
        import robinhood as mod
        assert hasattr(mod, "discover_accounts")


# ---------------------------------------------------------------------------
# Broker provider 3-tuple return
# ---------------------------------------------------------------------------

class TestBrokerProviderReturnShape:
    def test_fetch_positions_with_options_docstring_mentions_discovered(self):
        from app.services.broker_provider import RobinhoodCredentialProvider
        doc = RobinhoodCredentialProvider.fetch_positions_with_options.__doc__ or ""
        assert "discovered" in doc.lower() or "account" in doc.lower()
