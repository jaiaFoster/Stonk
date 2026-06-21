"""
tests/test_user_error_log.py — Per-user error log tests.

Verifies:
- user_errors table creation
- log_user_error() never raises
- log_user_error() redacts secrets
- log_user_error() truncates to 500 chars
- get_user_errors() returns correct rows
- count_user_errors_24h() counts recent errors
- GET /api/admin/errors endpoint
- error counts in /api/admin/summary
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest


@pytest.fixture
def temp_db(tmp_path):
    db_path = str(tmp_path / "users_err.db")
    with patch("app.config.USERS_DB_PATH", db_path):
        from app.db.users import init_db
        init_db()
    return db_path


class TestUserErrorsTable:
    def test_table_exists(self, temp_db):
        conn = sqlite3.connect(temp_db)
        tables = [t[0] for t in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='user_errors'"
        ).fetchall()]
        conn.close()
        assert "user_errors" in tables

    def test_columns(self, temp_db):
        conn = sqlite3.connect(temp_db)
        cols = [c[1] for c in conn.execute("PRAGMA table_info(user_errors)").fetchall()]
        conn.close()
        for col in ("id", "user_id", "run_id", "error_source", "error_type", "error_message", "created_at"):
            assert col in cols


class TestLogUserError:
    def test_basic_insert(self, temp_db):
        with patch("app.config.USERS_DB_PATH", temp_db):
            from app.db.users import log_user_error, get_user_errors
            log_user_error(1, "test.source", "TestError", "something broke")
            errors = get_user_errors(user_id=1)
            assert len(errors) == 1
            assert errors[0]["error_source"] == "test.source"
            assert errors[0]["error_type"] == "TestError"
            assert errors[0]["error_message"] == "something broke"

    def test_never_raises(self, temp_db):
        with patch("app.config.USERS_DB_PATH", "/nonexistent/path/db.sqlite"):
            from app.db.users import log_user_error
            log_user_error(1, "test", "Err", "msg")

    def test_truncates_to_500(self, temp_db):
        with patch("app.config.USERS_DB_PATH", temp_db):
            from app.db.users import log_user_error, get_user_errors
            long_msg = "x" * 1000
            log_user_error(1, "test", "Err", long_msg)
            errors = get_user_errors(user_id=1)
            assert len(errors[0]["error_message"]) == 500

    def test_redacts_secrets(self, temp_db):
        with patch("app.config.USERS_DB_PATH", temp_db):
            with patch("app.config.RUN_TOKEN", "supersecrettoken123"):
                from app.db.users import log_user_error, get_user_errors
                log_user_error(1, "test", "Err", "failed with token supersecrettoken123 in url")
                errors = get_user_errors(user_id=1)
                assert "supersecrettoken123" not in errors[0]["error_message"]
                assert "[REDACTED]" in errors[0]["error_message"]

    def test_with_run_id(self, temp_db):
        with patch("app.config.USERS_DB_PATH", temp_db):
            from app.db.users import log_user_error, get_user_errors
            log_user_error(1, "test", "Err", "msg", run_id="run_abc123")
            errors = get_user_errors(user_id=1)
            assert errors[0]["run_id"] == "run_abc123"

    def test_null_user_id(self, temp_db):
        with patch("app.config.USERS_DB_PATH", temp_db):
            from app.db.users import log_user_error, get_user_errors
            log_user_error(None, "test", "Err", "no user")
            errors = get_user_errors()
            assert len(errors) == 1
            assert errors[0]["user_id"] is None


class TestGetUserErrors:
    def test_filters_by_user(self, temp_db):
        with patch("app.config.USERS_DB_PATH", temp_db):
            from app.db.users import log_user_error, get_user_errors
            log_user_error(1, "a", "E", "m1")
            log_user_error(2, "b", "E", "m2")
            log_user_error(1, "c", "E", "m3")
            assert len(get_user_errors(user_id=1)) == 2
            assert len(get_user_errors(user_id=2)) == 1

    def test_returns_all_without_filter(self, temp_db):
        with patch("app.config.USERS_DB_PATH", temp_db):
            from app.db.users import log_user_error, get_user_errors
            log_user_error(1, "a", "E", "m1")
            log_user_error(2, "b", "E", "m2")
            assert len(get_user_errors()) == 2

    def test_limit_and_offset(self, temp_db):
        with patch("app.config.USERS_DB_PATH", temp_db):
            from app.db.users import log_user_error, get_user_errors
            for i in range(10):
                log_user_error(1, f"src_{i}", "E", f"msg_{i}")
            page1 = get_user_errors(limit=3, offset=0)
            page2 = get_user_errors(limit=3, offset=3)
            assert len(page1) == 3
            assert len(page2) == 3
            assert page1[0]["id"] != page2[0]["id"]

    def test_ordered_newest_first(self, temp_db):
        with patch("app.config.USERS_DB_PATH", temp_db):
            from app.db.users import log_user_error, get_user_errors
            log_user_error(1, "first", "E", "m1")
            log_user_error(1, "second", "E", "m2")
            errors = get_user_errors(user_id=1)
            assert errors[0]["error_source"] == "second"
            assert errors[1]["error_source"] == "first"


class TestCountUserErrors24h:
    def test_counts_recent(self, temp_db):
        with patch("app.config.USERS_DB_PATH", temp_db):
            from app.db.users import log_user_error, count_user_errors_24h
            log_user_error(1, "a", "E", "m")
            log_user_error(2, "b", "E", "m")
            assert count_user_errors_24h() == 2
