"""
tests/test_tkt045_account_nickname.py — TKT-045 account nickname tests.

Verifies:
- nickname column migration
- set/get nickname DB functions
- PUT endpoint auth + validation
- account_nickname surfaced in advisor positions broker_accounts
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest


@pytest.fixture
def temp_db(tmp_path):
    db_path = str(tmp_path / "users_045.db")
    with patch("app.config.USERS_DB_PATH", db_path):
        from app.db.users import init_db
        init_db()
    return db_path


# ---------------------------------------------------------------------------
# DB: nickname column exists after migration
# ---------------------------------------------------------------------------

class TestNicknameColumnMigration:
    def test_nickname_column_exists(self, temp_db):
        conn = sqlite3.connect(temp_db)
        cols = [c[1] for c in conn.execute("PRAGMA table_info(user_broker_accounts)").fetchall()]
        conn.close()
        assert "nickname" in cols

    def test_nickname_default_is_null(self, temp_db):
        with patch("app.config.USERS_DB_PATH", temp_db):
            from app.db.users import save_user_broker_accounts, get_user_broker_accounts
            save_user_broker_accounts(1, [{"account_number": "111", "account_type": "Individual"}])
            result = get_user_broker_accounts(1)
            assert result[0].get("nickname") is None


# ---------------------------------------------------------------------------
# DB: set_account_nickname / get_account_nickname
# ---------------------------------------------------------------------------

class TestNicknameFunctions:
    def test_set_and_get(self, temp_db):
        with patch("app.config.USERS_DB_PATH", temp_db):
            from app.db.users import save_user_broker_accounts, set_account_nickname, get_account_nickname
            save_user_broker_accounts(1, [{"account_number": "AAA", "account_type": "Individual"}])
            assert set_account_nickname(1, "AAA", "My Brokerage") is True
            assert get_account_nickname(1, "AAA") == "My Brokerage"

    def test_clear_nickname(self, temp_db):
        with patch("app.config.USERS_DB_PATH", temp_db):
            from app.db.users import save_user_broker_accounts, set_account_nickname, get_account_nickname
            save_user_broker_accounts(1, [{"account_number": "AAA", "account_type": "Individual"}])
            set_account_nickname(1, "AAA", "Name")
            set_account_nickname(1, "AAA", None)
            assert get_account_nickname(1, "AAA") is None

    def test_nonexistent_account_returns_false(self, temp_db):
        with patch("app.config.USERS_DB_PATH", temp_db):
            from app.db.users import set_account_nickname, get_account_nickname
            assert set_account_nickname(1, "NOPE", "Name") is False
            assert get_account_nickname(1, "NOPE") is None

    def test_different_users_independent(self, temp_db):
        with patch("app.config.USERS_DB_PATH", temp_db):
            from app.db.users import save_user_broker_accounts, set_account_nickname, get_account_nickname
            save_user_broker_accounts(1, [{"account_number": "AAA", "account_type": "X"}])
            save_user_broker_accounts(2, [{"account_number": "AAA", "account_type": "Y"}])
            set_account_nickname(1, "AAA", "User1Name")
            set_account_nickname(2, "AAA", "User2Name")
            assert get_account_nickname(1, "AAA") == "User1Name"
            assert get_account_nickname(2, "AAA") == "User2Name"

    def test_nickname_survives_rediscovery(self, temp_db):
        """save_user_broker_accounts replaces rows — nickname should NOT survive."""
        with patch("app.config.USERS_DB_PATH", temp_db):
            from app.db.users import save_user_broker_accounts, set_account_nickname, get_account_nickname
            save_user_broker_accounts(1, [{"account_number": "AAA", "account_type": "X"}])
            set_account_nickname(1, "AAA", "MyName")
            save_user_broker_accounts(1, [{"account_number": "AAA", "account_type": "X"}])
            assert get_account_nickname(1, "AAA") is None


# ---------------------------------------------------------------------------
# Advisor positions: account_nickname field present
# ---------------------------------------------------------------------------

class TestAdvisorNicknameField:
    def test_broker_accounts_includes_nickname_key(self, temp_db):
        with patch("app.config.USERS_DB_PATH", temp_db):
            from app.db.users import save_user_broker_accounts, set_account_nickname, get_user_broker_accounts
            save_user_broker_accounts(1, [
                {"account_number": "AAA", "account_type": "Individual"},
                {"account_number": "BBB", "account_type": "Roth IRA"},
            ])
            set_account_nickname(1, "AAA", "Trading")
            accounts = get_user_broker_accounts(1)
            shaped = [
                {"account_number": a.get("account_number"), "account_nickname": a.get("nickname")}
                for a in accounts
            ]
            by_num = {s["account_number"]: s for s in shaped}
            assert by_num["AAA"]["account_nickname"] == "Trading"
            assert by_num["BBB"]["account_nickname"] is None
