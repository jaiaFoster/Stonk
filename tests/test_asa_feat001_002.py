"""Tests for TKT-FEAT-001 (broker-optional users) and TKT-FEAT-002 (signal engagement telemetry)."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# TKT-FEAT-001 — DB: migration + create_user_broker_optional
# ---------------------------------------------------------------------------

class TestBrokerOptionalMigration:
    def test_migration_adds_columns(self, tmp_path):
        import sqlite3
        from app.db import users as u
        import app.config as _cfg

        db = str(tmp_path / "users.db")
        with patch.object(_cfg, "USERS_DB_PATH", db):
            with patch("app.db.users._db_path", return_value=db):
                u.init_db()
                import sqlite3 as sq
                conn = sq.connect(db)
                cols = [c[1] for c in conn.execute("PRAGMA table_info(users)").fetchall()]
                conn.close()
        assert "broker_connected" in cols
        assert "broker_connection_optional" in cols

    def test_migration_idempotent(self, tmp_path):
        import sqlite3 as sq
        from app.db import users as u
        import app.config as _cfg

        db = str(tmp_path / "users.db")
        with patch.object(_cfg, "USERS_DB_PATH", db), \
             patch("app.db.users._db_path", return_value=db):
            u.init_db()
            u.init_db()  # second call must not raise
            conn = sq.connect(db)
            cols = [c[1] for c in conn.execute("PRAGMA table_info(users)").fetchall()]
            conn.close()
        assert "broker_connected" in cols

    def test_backfill_sets_broker_connected_for_rh_users(self, tmp_path):
        import sqlite3 as sq
        from app.db import users as u
        import app.config as _cfg

        db = str(tmp_path / "users.db")
        with patch.object(_cfg, "USERS_DB_PATH", db), \
             patch("app.db.users._db_path", return_value=db):
            u.init_db()
            # Insert a user with Robinhood credentials (simulating pre-migration user)
            conn = sq.connect(db)
            conn.execute(
                "INSERT INTO users (username, password_hash, api_key, robinhood_username) "
                "VALUES ('rh_user', 'hash', 'asa_key1', 'rh@test.com')"
            )
            conn.execute("UPDATE users SET broker_connected=0 WHERE username='rh_user'")
            conn.commit()
            conn.close()
            # Re-run migration — should backfill
            from app.db.users import _connect, _migrate_feat001
            with _connect() as c:
                _migrate_feat001(c)
            conn = sq.connect(db)
            row = conn.execute(
                "SELECT broker_connected FROM users WHERE username='rh_user'"
            ).fetchone()
            conn.close()
        assert row[0] == 1


class TestCreateUserBrokerOptional:
    def _setup_db(self, tmp_path, monkeypatch):
        import app.config as _cfg
        db = str(tmp_path / "users.db")
        monkeypatch.setattr(_cfg, "USERS_DB_PATH", db)
        monkeypatch.setattr("app.db.users._db_path", lambda: db)
        from app.db.users import init_db
        init_db()
        return db

    def test_creates_user_with_no_broker(self, tmp_path, monkeypatch):
        self._setup_db(tmp_path, monkeypatch)
        from app.db.users import create_user_broker_optional
        user = create_user_broker_optional("test@example.com", "password123")
        assert user["username"] == "test@example.com"
        assert user["broker_connected"] == 0
        assert user["broker_connection_optional"] == 1
        assert user["api_key"].startswith("asa_")
        assert not user.get("robinhood_username")

    def test_duplicate_email_raises_value_error(self, tmp_path, monkeypatch):
        self._setup_db(tmp_path, monkeypatch)
        from app.db.users import create_user_broker_optional
        import pytest
        create_user_broker_optional("dup@example.com", "password123")
        with pytest.raises(ValueError, match="already registered"):
            create_user_broker_optional("dup@example.com", "password456")

    def test_set_broker_connected(self, tmp_path, monkeypatch):
        self._setup_db(tmp_path, monkeypatch)
        from app.db.users import create_user_broker_optional, set_broker_connected, get_user_by_id
        user = create_user_broker_optional("connect@example.com", "password123")
        user_id = user["id"]
        assert user["broker_connected"] == 0
        set_broker_connected(user_id, broker_type="robinhood")
        updated = get_user_by_id(user_id)
        assert updated["broker_connected"] == 1
        assert updated["broker_type"] == "robinhood"
        assert updated["credentials_validated_at"] is not None


# ---------------------------------------------------------------------------
# TKT-FEAT-001 — Registration endpoint
# ---------------------------------------------------------------------------

class TestRegisterEndpoint:
    def _client(self):
        from app.main import app
        app.config["TESTING"] = True
        return app.test_client()

    def test_register_creates_user(self):
        client = self._client()
        with patch("app.db.users.create_user_broker_optional") as mock_create:
            mock_create.return_value = {
                "id": 42, "api_key": "asa_" + "a" * 64,
                "broker_connected": 0, "broker_connection_optional": 1,
                "username": "new@example.com",
            }
            resp = client.post(
                "/api/auth/register",
                json={"email": "new@example.com", "password": "password123"},
            )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["broker_connected"] is False
        assert "user_key" in data
        assert "user_id" in data

    def test_register_invalid_email_400(self):
        client = self._client()
        resp = client.post(
            "/api/auth/register",
            json={"email": "not-an-email", "password": "password123"},
        )
        assert resp.status_code == 400

    def test_register_short_password_400(self):
        client = self._client()
        resp = client.post(
            "/api/auth/register",
            json={"email": "user@example.com", "password": "short"},
        )
        assert resp.status_code == 400

    def test_register_duplicate_email_409(self):
        client = self._client()
        with patch("app.db.users.create_user_broker_optional", side_effect=ValueError("already registered")):
            resp = client.post(
                "/api/auth/register",
                json={"email": "dup@example.com", "password": "password123"},
            )
        assert resp.status_code == 409

    def test_register_rate_limit(self):
        """6th request from same IP within window should be rejected."""
        from app.api.auth import _reg_attempts, _rate_lock
        client = self._client()
        fake_ip = "10.0.0.99"
        now = time.time()
        with _rate_lock:
            _reg_attempts[fake_ip] = [now] * 5  # pre-fill 5 attempts

        with patch("app.api.auth._client_ip", return_value=fake_ip):
            resp = client.post(
                "/api/auth/register",
                json={"email": "rl@example.com", "password": "password123"},
            )
        assert resp.status_code == 429

        # Cleanup
        with _rate_lock:
            _reg_attempts.pop(fake_ip, None)


# ---------------------------------------------------------------------------
# TKT-FEAT-001 — Personalization: signals-only path
# ---------------------------------------------------------------------------

class TestPersonalizationSignalsOnly:
    def _make_user(self, broker_connection_optional=True, broker_connected=False):
        return {
            "id": 1,
            "broker_type": None,
            "robinhood_username": "",
            "robinhood_password_encrypted": "",
            "plaid_access_token_encrypted": "",
            "broker_connection_optional": int(broker_connection_optional),
            "broker_connected": int(broker_connected),
        }

    def test_signals_only_returns_broker_mode(self):
        from app.services.personalization import run_personalization

        user = self._make_user()
        with patch("app.services.personalization._load_latest_core_run", return_value=(None, None)), \
             patch("app.services.personalization.create_user_run"), \
             patch("app.services.personalization.complete_user_run"), \
             patch("app.services.personalization.save_user_positions"), \
             patch("app.services.personalization.save_user_daily_opportunity"), \
             patch("app.services.personalization.get_active_user_run", return_value=None):
            result = run_personalization(1, user)

        assert result["broker_mode"] == "signals_only"
        assert result["status"] == "ok"
        assert result["positions_fetched"] == 0

    def test_signals_only_run_completes_not_fails(self):
        from app.services.personalization import run_personalization

        user = self._make_user()
        completed_calls = []
        failed_calls = []

        with patch("app.services.personalization._load_latest_core_run", return_value=(None, None)), \
             patch("app.services.personalization.create_user_run"), \
             patch("app.services.personalization.complete_user_run", side_effect=lambda *a, **k: completed_calls.append(k)), \
             patch("app.services.personalization.fail_user_run", side_effect=lambda *a, **k: failed_calls.append(k)), \
             patch("app.services.personalization.save_user_positions"), \
             patch("app.services.personalization.save_user_daily_opportunity"), \
             patch("app.services.personalization.get_active_user_run", return_value=None):
            run_personalization(1, user)

        assert len(completed_calls) == 1
        assert len(failed_calls) == 0

    def test_broker_connected_user_uses_normal_path(self):
        """Users with broker_connected=1 must not take the signals-only shortcut."""
        from app.services.personalization import run_personalization

        user = self._make_user(broker_connection_optional=True, broker_connected=True)
        # This user has no actual creds, so it should fall to the has_creds=False path
        # (not the signals_only path), triggering "no_broker_credentials"
        with patch("app.services.personalization._load_latest_core_run", return_value=(None, None)), \
             patch("app.services.personalization.create_user_run"), \
             patch("app.services.personalization.fail_user_run"), \
             patch("app.services.personalization.get_active_user_run", return_value=None):
            result = run_personalization(1, user)

        assert result.get("broker_mode") != "signals_only"
        assert result.get("reason") == "no_broker_credentials"

    def test_signals_only_provider_calls_false(self):
        from app.services.personalization import run_personalization

        user = self._make_user()
        with patch("app.services.personalization._load_latest_core_run", return_value=(None, None)), \
             patch("app.services.personalization.create_user_run"), \
             patch("app.services.personalization.complete_user_run"), \
             patch("app.services.personalization.save_user_positions"), \
             patch("app.services.personalization.save_user_daily_opportunity"), \
             patch("app.services.personalization.get_active_user_run", return_value=None):
            result = run_personalization(1, user)

        # Broker-optional path must not trigger provider calls
        assert result["provider_calls_triggered"] is False


# ---------------------------------------------------------------------------
# TKT-FEAT-001 — connect-broker endpoint
# ---------------------------------------------------------------------------

class TestConnectBrokerEndpoint:
    def _client(self):
        from app.main import app
        app.config["TESTING"] = True
        return app.test_client()

    def test_connect_broker_validates_and_sets_connected(self):
        client = self._client()
        with patch("app.config.DEV_API_TOKEN", "test_token"), \
             patch("app.config.RUN_TOKEN", "test_token"), \
             patch("app.db.users.get_encryption_key_status", return_value=True), \
             patch("app.services.broker_provider.BrokerCredentialProvider.get_provider") as mock_prov, \
             patch("app.db.users.update_broker_credentials"), \
             patch("app.db.users.set_broker_connected") as mock_set:
            mock_prov.return_value.validate_credentials.return_value = (True, None)
            resp = client.post(
                "/api/auth/connect-broker",
                json={"robinhood_username": "rh@test.com", "robinhood_password": "pass"},
                headers={"Authorization": "Bearer test_token"},
            )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["broker_connected"] is True
        mock_set.assert_called_once()

    def test_connect_broker_wrong_creds_400(self):
        client = self._client()
        with patch("app.config.DEV_API_TOKEN", "test_token"), \
             patch("app.config.RUN_TOKEN", "test_token"), \
             patch("app.db.users.get_encryption_key_status", return_value=True), \
             patch("app.services.broker_provider.BrokerCredentialProvider.get_provider") as mock_prov:
            mock_prov.return_value.validate_credentials.return_value = (False, "login_failed")
            resp = client.post(
                "/api/auth/connect-broker",
                json={"robinhood_username": "rh@test.com", "robinhood_password": "wrong"},
                headers={"Authorization": "Bearer test_token"},
            )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["broker_connected"] is False

    def test_connect_broker_requires_auth(self):
        client = self._client()
        with patch("app.config.LEGACY_DEV_TOKEN_ENABLED", False):
            resp = client.post(
                "/api/auth/connect-broker",
                json={"robinhood_username": "rh@test.com", "robinhood_password": "pass"},
            )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# TKT-FEAT-002 — Signal engagement DB functions
# ---------------------------------------------------------------------------

class TestSignalEngagementDB:
    def test_record_signal_engagement(self, tmp_path):
        import app.config as _cfg
        db = str(tmp_path / "telemetry.db")
        with patch.object(_cfg, "TELEMETRY_DB_PATH", db):
            from app.db.telemetry import record_signal_engagement, signal_engagement_summary
            record_signal_engagement(
                ticker="AAPL",
                strategy_id="earnings_calendar",
                action="expand_detail",
                verdict="PASS",
                user_id="42",
                broker_mode="connected",
                session_id="sess123",
                run_id="run_abc",
                db_path=db,
            )
            summary = signal_engagement_summary(days=7, db_path=db)
        assert summary["total_engagements"] == 1
        assert summary["by_ticker"][0]["ticker"] == "AAPL"
        assert summary["by_ticker"][0]["strategy_id"] == "earnings_calendar"

    def test_invalid_action_normalized(self, tmp_path):
        import app.config as _cfg
        db = str(tmp_path / "telemetry.db")
        import sqlite3 as sq
        with patch.object(_cfg, "TELEMETRY_DB_PATH", db):
            from app.db.telemetry import record_signal_engagement
            record_signal_engagement(
                ticker="MSFT",
                strategy_id="skew_momentum_vertical",
                action="invalid_action_xyz",
                db_path=db,
            )
            conn = sq.connect(db)
            row = conn.execute("SELECT action FROM signal_engagement WHERE ticker='MSFT'").fetchone()
            conn.close()
        assert row[0] == "view_signal"

    def test_no_pii_stored(self, tmp_path):
        """user_id is the internal integer ID — no email stored."""
        import sqlite3 as sq
        db = str(tmp_path / "telemetry.db")
        from app.db.telemetry import record_signal_engagement
        record_signal_engagement(
            ticker="TSLA",
            strategy_id="forward_factor_calendar",
            action="view_signal",
            user_id="99",
            db_path=db,
        )
        conn = sq.connect(db)
        row = conn.execute("SELECT user_id FROM signal_engagement WHERE ticker='TSLA'").fetchone()
        conn.close()
        assert row[0] == "99"
        # Confirm no email-like value
        assert "@" not in (row[0] or "")

    def test_broker_optional_pct_computed(self, tmp_path):
        db = str(tmp_path / "telemetry.db")
        from app.db.telemetry import record_signal_engagement, signal_engagement_summary
        record_signal_engagement("AAPL", "earnings_calendar", "view_signal",
                                 broker_mode="connected", db_path=db)
        record_signal_engagement("MSFT", "earnings_calendar", "view_signal",
                                 broker_mode="signals_only", db_path=db)
        summary = signal_engagement_summary(days=7, db_path=db)
        assert summary["broker_optional_pct"] == 50.0

    def test_summary_empty_db(self, tmp_path):
        db = str(tmp_path / "telemetry.db")
        from app.db.telemetry import signal_engagement_summary
        summary = signal_engagement_summary(days=7, db_path=db)
        assert summary["total_engagements"] == 0
        assert summary["by_ticker"] == []


# ---------------------------------------------------------------------------
# TKT-FEAT-002 — Signal engagement endpoint
# ---------------------------------------------------------------------------

class TestSignalEngagementEndpoint:
    def _client(self):
        from app.main import app
        app.config["TESTING"] = True
        return app.test_client()

    def test_record_signal_returns_recorded(self):
        client = self._client()
        with patch("app.db.telemetry.record_signal_engagement") as mock_rec, \
             patch("app.api.telemetry._resolve_user_id", return_value=None), \
             patch("app.api.telemetry._get_broker_mode", return_value=None):
            resp = client.post(
                "/api/telemetry/signal-engagement",
                json={
                    "ticker": "SBUX",
                    "strategy_id": "forward_factor_calendar",
                    "verdict": "PASS",
                    "action": "expand_detail",
                    "session_id": "sess123",
                },
            )
        assert resp.status_code == 200
        assert resp.get_json()["recorded"] is True
        mock_rec.assert_called_once()

    def test_missing_ticker_returns_400(self):
        client = self._client()
        resp = client.post(
            "/api/telemetry/signal-engagement",
            json={"strategy_id": "earnings_calendar"},
        )
        assert resp.status_code == 400

    def test_missing_strategy_returns_400(self):
        client = self._client()
        resp = client.post(
            "/api/telemetry/signal-engagement",
            json={"ticker": "AAPL"},
        )
        assert resp.status_code == 400

    def test_rate_limit_60_per_minute(self):
        from app.api.telemetry import _engagement_attempts, _rate_lock
        client = self._client()
        fake_ip = "10.0.0.88"
        now = time.time()
        with _rate_lock:
            _engagement_attempts[fake_ip] = [now] * 60  # pre-fill 60

        with patch("app.api.telemetry._client_ip", return_value=fake_ip), \
             patch("app.db.telemetry.record_signal_engagement"):
            resp = client.post(
                "/api/telemetry/signal-engagement",
                json={"ticker": "AAPL", "strategy_id": "earnings_calendar"},
            )
        assert resp.status_code == 429

        with _rate_lock:
            _engagement_attempts.pop(fake_ip, None)


# ---------------------------------------------------------------------------
# TKT-FEAT-002 — Admin signal telemetry endpoint
# ---------------------------------------------------------------------------

class TestAdminSignalTelemetry:
    def _client(self):
        from app.main import app
        app.config["TESTING"] = True
        return app.test_client()

    def test_admin_endpoint_returns_aggregate(self):
        client = self._client()
        fake_summary = {
            "period_days": 7,
            "total_engagements": 3,
            "by_ticker": [{"ticker": "SBUX", "strategy_id": "forward_factor_calendar", "count": 3, "last_seen": "2026-07-04"}],
            "by_strategy": [{"strategy_id": "forward_factor_calendar", "count": 3}],
            "broker_optional_pct": 33.3,
        }
        with patch("app.config.DEV_API_TOKEN", "admin_token"), \
             patch("app.config.RUN_TOKEN", "admin_token"), \
             patch("app.db.telemetry.signal_engagement_summary", return_value=fake_summary):
            resp = client.get(
                "/api/admin/signal-telemetry",
                headers={"Authorization": "Bearer admin_token"},
            )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total_engagements"] == 3
        assert data["broker_optional_pct"] == 33.3
        assert data["by_ticker"][0]["ticker"] == "SBUX"

    def test_admin_endpoint_requires_auth(self):
        client = self._client()
        with patch("app.config.LEGACY_DEV_TOKEN_ENABLED", False):
            resp = client.get("/api/admin/signal-telemetry")
        assert resp.status_code == 401
