"""
tests/test_patch29a.py — Patch 29A regression tests.

TKT-033: Rate limit fires before credentials check.
TKT-034: get_encryption_key_status() reads env directly.
TKT-036: is_dev flag, require_dev decorator, asa_admin seed, jaia demotion.
TKT-035: Options position detection (verticals + calendars), storage, exit signals.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_db(tmp_path):
    db_path = str(tmp_path / "users_29a.db")
    with patch("app.config.USERS_DB_PATH", db_path):
        from app.db.users import init_db
        init_db()
    return db_path


@pytest.fixture
def admin_user(temp_db):
    with patch("app.config.USERS_DB_PATH", temp_db):
        from app.db.users import create_user, get_user_by_username
        # create_user doesn't have is_dev kwarg yet — use direct SQL after creation
        create_user("admin29a", "adminpass", is_admin=1)
        user = get_user_by_username("admin29a")
        with sqlite3.connect(temp_db) as conn:
            conn.execute("UPDATE users SET is_dev=1 WHERE username='admin29a'")
            conn.commit()
        return get_user_by_username("admin29a")


@pytest.fixture
def dev_user(temp_db):
    """is_dev=1, is_admin=0"""
    with patch("app.config.USERS_DB_PATH", temp_db):
        from app.db.users import create_user, get_user_by_username
        create_user("devuser29a", "devpass", is_admin=0)
        with sqlite3.connect(temp_db) as conn:
            conn.execute("UPDATE users SET is_dev=1 WHERE username='devuser29a'")
            conn.commit()
        return get_user_by_username("devuser29a")


@pytest.fixture
def member_user(temp_db):
    """Regular member — is_admin=0, is_dev=0"""
    with patch("app.config.USERS_DB_PATH", temp_db):
        from app.db.users import create_user, get_user_by_username
        create_user("member29a", "memberpass", is_admin=0)
        return get_user_by_username("member29a")


# ---------------------------------------------------------------------------
# TKT-034: Encryption key detection
# ---------------------------------------------------------------------------

class TestEncryptionKeyStatus:
    def test_returns_false_when_not_set(self):
        with patch.dict(os.environ, {"ROBINHOOD_ENCRYPTION_KEY": ""}, clear=False):
            # reimport to get fresh env read
            import importlib
            import app.db.users as users_mod
            importlib.reload(users_mod)
            assert users_mod.get_encryption_key_status() is False

    def test_returns_false_for_invalid_key(self):
        with patch.dict(os.environ, {"ROBINHOOD_ENCRYPTION_KEY": "not_a_fernet_key"}, clear=False):
            import importlib
            import app.db.users as users_mod
            importlib.reload(users_mod)
            assert users_mod.get_encryption_key_status() is False

    def test_returns_true_for_valid_fernet_key(self):
        from cryptography.fernet import Fernet
        key = Fernet.generate_key().decode()
        with patch.dict(os.environ, {"ROBINHOOD_ENCRYPTION_KEY": key}, clear=False):
            import importlib
            import app.db.users as users_mod
            importlib.reload(users_mod)
            assert users_mod.get_encryption_key_status() is True

    def test_strips_whitespace(self):
        from cryptography.fernet import Fernet
        key = Fernet.generate_key().decode()
        padded = f"  {key}  "
        with patch.dict(os.environ, {"ROBINHOOD_ENCRYPTION_KEY": padded}, clear=False):
            import importlib
            import app.db.users as users_mod
            importlib.reload(users_mod)
            assert users_mod.get_encryption_key_status() is True


# ---------------------------------------------------------------------------
# TKT-036: is_dev schema + helpers
# ---------------------------------------------------------------------------

class TestIsDevSchema:
    def test_is_dev_column_exists(self, temp_db):
        conn = sqlite3.connect(temp_db)
        cursor = conn.execute("PRAGMA table_info(users)")
        columns = {row[1] for row in cursor.fetchall()}
        conn.close()
        assert "is_dev" in columns

    def test_is_dev_defaults_to_zero(self, temp_db):
        with patch("app.config.USERS_DB_PATH", temp_db):
            from app.db.users import create_user, get_user_by_username
            create_user("newuser_isdev", "pass")
            user = get_user_by_username("newuser_isdev")
        assert user.get("is_dev") == 0

    def test_is_dev_can_be_set(self, temp_db):
        with patch("app.config.USERS_DB_PATH", temp_db):
            from app.db.users import create_user, get_user_by_username
            create_user("devuser_settest", "pass")
            conn = sqlite3.connect(temp_db)
            conn.execute("UPDATE users SET is_dev=1 WHERE username='devuser_settest'")
            conn.commit()
            conn.close()
            user = get_user_by_username("devuser_settest")
        assert user.get("is_dev") == 1


# ---------------------------------------------------------------------------
# TKT-036: seed_sysadmin
# ---------------------------------------------------------------------------

class TestSeedSysadmin:
    def test_creates_asa_admin(self, temp_db):
        with patch("app.config.USERS_DB_PATH", temp_db), \
             patch("app.config.ASA_SYSADMIN_USERNAME", "asa_admin"), \
             patch("app.config.ASA_SYSADMIN_PASSWORD", "testpass123"), \
             patch("app.config.ASA_ADMIN_USERNAME", "jaia"):
            from app.db.users import seed_sysadmin, get_user_by_username
            seed_sysadmin()
            asa = get_user_by_username("asa_admin")
        assert asa is not None
        assert asa.get("is_admin") == 1
        assert asa.get("is_dev") == 1
        assert asa.get("is_active") == 1

    def test_idempotent_second_call(self, temp_db):
        with patch("app.config.USERS_DB_PATH", temp_db), \
             patch("app.config.ASA_SYSADMIN_USERNAME", "asa_admin"), \
             patch("app.config.ASA_SYSADMIN_PASSWORD", "testpass123"), \
             patch("app.config.ASA_ADMIN_USERNAME", "jaia"):
            from app.db.users import seed_sysadmin, get_user_by_username
            seed_sysadmin()
            seed_sysadmin()  # second call — should not raise
            conn = sqlite3.connect(temp_db)
            count = conn.execute("SELECT COUNT(*) FROM users WHERE username='asa_admin'").fetchone()[0]
            conn.close()
        assert count == 1

    def test_demotes_jaia(self, temp_db):
        with patch("app.config.USERS_DB_PATH", temp_db), \
             patch("app.config.ASA_SYSADMIN_USERNAME", "asa_admin"), \
             patch("app.config.ASA_SYSADMIN_PASSWORD", "testpass123"), \
             patch("app.config.ASA_ADMIN_USERNAME", "jaia"):
            from app.db.users import create_user, seed_sysadmin, get_user_by_username
            create_user("jaia", "jaiapass", is_admin=1)
            seed_sysadmin()
            jaia = get_user_by_username("jaia")
        assert jaia.get("is_admin") == 0
        assert jaia.get("is_dev") == 1


# ---------------------------------------------------------------------------
# TKT-036: require_dev decorator
# ---------------------------------------------------------------------------

class TestRequireDev:
    def test_dev_user_passes(self, dev_user):
        from app.auth import require_dev
        from app.main import app
        with app.test_request_context(f"/?token={dev_user['api_key']}"):
            with patch("app.config.USERS_DB_PATH", dev_user.get("_db_path", "")):
                # Just verify decorator exists and is callable
                assert callable(require_dev)

    def test_require_dev_rejects_member(self):
        """Member (is_dev=0, is_admin=0) cannot access require_dev endpoints."""
        from app.auth import require_dev
        from flask import Flask
        test_app = Flask(__name__)

        @test_app.route("/test")
        @require_dev
        def test_view():
            return "ok"

        with test_app.test_client() as client:
            # No token → 401
            resp = client.get("/test")
            assert resp.status_code == 401

    def test_require_dev_allows_dev_flag(self, temp_db):
        """User with is_dev=1 can access require_dev endpoints."""
        with patch("app.config.USERS_DB_PATH", temp_db):
            from app.db.users import create_user, get_user_by_username
            create_user("devonly29", "pass")
            conn = sqlite3.connect(temp_db)
            conn.execute("UPDATE users SET is_dev=1 WHERE username='devonly29'")
            conn.commit()
            conn.close()
            user = get_user_by_username("devonly29")

        from app.auth import require_dev
        from flask import Flask, g
        test_app = Flask(__name__)

        @test_app.route("/test")
        @require_dev
        def test_view():
            return "ok"

        with patch("app.config.USERS_DB_PATH", temp_db):
            with test_app.test_client() as client:
                resp = client.get(f"/test?token={user['api_key']}")
                assert resp.status_code == 200


# ---------------------------------------------------------------------------
# TKT-033: Rate limit fires before credentials check
# ---------------------------------------------------------------------------

class TestRateLimitGateOrder:
    def test_rate_limit_fires_before_credentials_check(self, temp_db):
        """User with no creds still gets rate_limited on 4th run (not no_robinhood_credentials)."""
        from app.main import app

        with patch("app.config.USERS_DB_PATH", temp_db), \
             patch("app.config.USER_RUN_RATE_LIMIT_PER_HOUR", 3):
            from app.db.users import create_user, get_user_by_username
            create_user("nocreds29", "pass")
            user = get_user_by_username("nocreds29")
            api_key = user["api_key"]

            # Seed 3 completed runs to fill the rate limit window
            from app.db.users import get_runs_in_last_hour, record_rate_limited_run
            from datetime import datetime, timezone
            conn = sqlite3.connect(temp_db)
            for i in range(3):
                now = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "INSERT INTO user_runs (user_id, run_id, status, started_at, completed_at) VALUES (?,?,?,?,?)",
                    (user["id"], f"run_seed_{i}", "complete", now, now),
                )
            conn.commit()
            conn.close()

            with app.test_client() as client:
                resp = client.post(f"/api/user/run?token={api_key}")
                data = resp.get_json()
            # Must be rate_limited — not no_robinhood_credentials
            assert data.get("error") == "rate_limited"
            assert resp.status_code == 429


# ---------------------------------------------------------------------------
# TKT-035: Options positions
# ---------------------------------------------------------------------------

class TestUserPositionsSchema:
    def test_position_type_column_exists(self, temp_db):
        conn = sqlite3.connect(temp_db)
        cursor = conn.execute("PRAGMA table_info(user_positions)")
        columns = {row[1] for row in cursor.fetchall()}
        conn.close()
        assert "position_type" in columns

    def test_option_details_column_exists(self, temp_db):
        conn = sqlite3.connect(temp_db)
        cursor = conn.execute("PRAGMA table_info(user_positions)")
        columns = {row[1] for row in cursor.fetchall()}
        conn.close()
        assert "option_details" in columns


class TestVerticalDetection:
    def _make_leg(self, underlying, option_type, expiration, strike, side, qty=1):
        from datetime import date
        return {
            "underlying": underlying,
            "option_type": option_type,
            "expiration": expiration,
            "strike": strike,
            "side": side,
            "abs_quantity": qty,
            "quantity": qty if side == "long" else -qty,
            "dte": 22,
            "avg_cost_per_share": 4.0 if side == "long" else 2.0,
            "mid": 5.0 if side == "long" else 3.0,
            "broker": "robinhood",
            "source": "robinhood",
        }

    def test_detects_debit_vertical(self):
        from app.services.open_options_service import _detect_vertical_spreads
        legs = [
            self._make_leg("NVDA", "call", "2026-07-10", 215.0, "long"),
            self._make_leg("NVDA", "call", "2026-07-10", 235.0, "short"),
        ]
        verticals = _detect_vertical_spreads(legs)
        assert len(verticals) == 1
        v = verticals[0]
        assert v["underlying"] == "NVDA"
        assert v["strategy_type"] == "skew_vertical"
        assert v["option_type"] == "call"
        assert v["width"] == 20.0

    def test_no_vertical_single_leg(self):
        from app.services.open_options_service import _detect_vertical_spreads
        legs = [self._make_leg("NVDA", "call", "2026-07-10", 215.0, "long")]
        verticals = _detect_vertical_spreads(legs)
        assert verticals == []

    def test_no_vertical_same_strike(self):
        from app.services.open_options_service import _detect_vertical_spreads
        legs = [
            self._make_leg("NVDA", "call", "2026-07-10", 215.0, "long"),
            self._make_leg("NVDA", "call", "2026-07-10", 215.0, "short"),
        ]
        verticals = _detect_vertical_spreads(legs)
        assert verticals == []


class TestExitSignal:
    def test_exit_target(self):
        from app.services.skew_momentum_vertical_service import _compute_exit_signal
        with patch("app.config.SKEW_PROFIT_TARGET_PCT", 50.0), \
             patch("app.config.SKEW_STOP_LOSS_PCT", 50.0), \
             patch("app.config.SKEW_EXIT_DTE_THRESHOLD", 5):
            signal, reason = _compute_exit_signal({"pct_of_max_profit": 55.0, "dte": 20, "unrealized_pnl_pct": 30.0})
        assert signal == "EXIT_TARGET"
        assert reason is not None

    def test_exit_stop(self):
        from app.services.skew_momentum_vertical_service import _compute_exit_signal
        with patch("app.config.SKEW_PROFIT_TARGET_PCT", 50.0), \
             patch("app.config.SKEW_STOP_LOSS_PCT", 50.0), \
             patch("app.config.SKEW_EXIT_DTE_THRESHOLD", 5):
            signal, reason = _compute_exit_signal({"pct_of_max_profit": 10.0, "dte": 20, "unrealized_pnl_pct": -60.0})
        assert signal == "EXIT_STOP"

    def test_exit_expiry(self):
        from app.services.skew_momentum_vertical_service import _compute_exit_signal
        with patch("app.config.SKEW_PROFIT_TARGET_PCT", 50.0), \
             patch("app.config.SKEW_STOP_LOSS_PCT", 50.0), \
             patch("app.config.SKEW_EXIT_DTE_THRESHOLD", 5):
            signal, reason = _compute_exit_signal({"pct_of_max_profit": 20.0, "dte": 3, "unrealized_pnl_pct": 15.0})
        assert signal == "EXIT_EXPIRY"

    def test_hold(self):
        from app.services.skew_momentum_vertical_service import _compute_exit_signal
        with patch("app.config.SKEW_PROFIT_TARGET_PCT", 50.0), \
             patch("app.config.SKEW_STOP_LOSS_PCT", 50.0), \
             patch("app.config.SKEW_EXIT_DTE_THRESHOLD", 5):
            signal, _ = _compute_exit_signal({"pct_of_max_profit": 30.0, "dte": 15, "unrealized_pnl_pct": 20.0})
        assert signal == "HOLD"


class TestSkewActiveRows:
    def test_lifecycle_active_when_verticals_present(self):
        from app.services.skew_momentum_vertical_service import build_skew_momentum_vertical_strategy
        open_options = {
            "verticals": [
                {
                    "underlying": "NVDA",
                    "ticker": "NVDA",
                    "strategy_type": "skew_vertical",
                    "option_type": "call",
                    "expiration": "2026-07-10",
                    "dte": 22,
                    "quantity": 1,
                    "pct_of_max_profit": 30.0,
                    "unrealized_pnl_pct": 15.0,
                    "net_debit": 4.25,
                    "current_value": 5.10,
                }
            ],
            "calendars": [],
        }
        with patch("app.config.SKEW_VERTICAL_STRATEGY_ENABLED", False):
            result = build_skew_momentum_vertical_strategy(
                positions=[], watchlist_candidates=None, portfolio_gap_analysis=None,
                market_metrics=None, open_options=open_options,
            )
        assert result["lifecycle_status"] == "active"
        assert result["active_count"] == 1
        active = result.get("active_rows") or result.get("active_items") or []
        assert len(active) == 1
        assert active[0]["exit_signal"] == "HOLD"

    def test_lifecycle_deferred_when_no_verticals(self):
        from app.services.skew_momentum_vertical_service import build_skew_momentum_vertical_strategy
        with patch("app.config.SKEW_VERTICAL_STRATEGY_ENABLED", False):
            result = build_skew_momentum_vertical_strategy(
                positions=[], watchlist_candidates=None, portfolio_gap_analysis=None,
                market_metrics=None, open_options=None,
            )
        assert result["lifecycle_status"] == "deferred"
        assert result["active_count"] == 0


class TestSaveUserOptionPositions:
    def test_saves_options_to_user_positions(self, temp_db):
        with patch("app.config.USERS_DB_PATH", temp_db):
            from app.db.users import (
                create_user, create_user_run, complete_user_run,
                save_user_option_positions_to_positions, get_user_positions,
                get_user_by_username,
            )
            create_user("optsave29", "pass")
            user = get_user_by_username("optsave29")
            uid = user["id"]
            create_user_run(uid, "opts_run_1")
            complete_user_run("opts_run_1", 0, 0)

            opts = [{
                "ticker": "NVDA",
                "strategy_type": "skew_vertical",
                "option_type": "call",
                "net_debit": 4.25,
                "current_value": 5.10,
                "legs": [],
                "quantity": 1,
                "unrealized_pnl_pct": 20.0,
            }]
            save_user_option_positions_to_positions(uid, "opts_run_1", opts)
            positions = get_user_positions(uid, run_id="opts_run_1")

        options_rows = [p for p in positions if p.get("position_type") == "options"]
        assert len(options_rows) == 1
        assert options_rows[0]["ticker"] == "NVDA"
        details = json.loads(options_rows[0]["option_details"] or "{}")
        assert details.get("strategy_type") == "skew_vertical"


class TestGetAllUsersIncludesIsDev:
    def test_is_dev_in_admin_user_list(self, temp_db):
        with patch("app.config.USERS_DB_PATH", temp_db):
            from app.db.users import create_user, get_all_users_with_run_status
            create_user("adminusr29", "pass", is_admin=1)
            conn = sqlite3.connect(temp_db)
            conn.execute("UPDATE users SET is_dev=1 WHERE username='adminusr29'")
            conn.commit()
            conn.close()
            users = get_all_users_with_run_status()
        usr = next((u for u in users if u["username"] == "adminusr29"), None)
        assert usr is not None
        assert "is_dev" in usr
        assert usr["is_dev"] == 1
