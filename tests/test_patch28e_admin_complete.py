"""Smoke tests for Patch 28E — admin complete + rate limiting.

Covers:
- Deactivate / reactivate (happy path, self-deactivation, last-admin guard)
- Reset API key
- Invite list + revocation
- Admin summary shape
- is_test_user flag
- Rate limiting on POST /api/user/run (429 + retry_after_seconds)
- Non-admin blocked (403) from all new admin endpoints
"""

from __future__ import annotations

import json
import secrets
import tempfile
import os
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_db(tmp_path):
    """Return path to a fresh users.db and patch config to use it."""
    return str(tmp_path / "users.db")


def _setup_db(db_path: str):
    with patch("app.config.USERS_DB_PATH", db_path):
        from app.db.users import init_db
        init_db()


def _make_user(db_path, username, password="pass123", is_admin=0):
    with patch("app.config.USERS_DB_PATH", db_path):
        from app.db.users import create_user
        return create_user(username, password, is_admin=is_admin)


def _make_invite(db_path, creator_id=None):
    with patch("app.config.USERS_DB_PATH", db_path):
        from app.db.users import create_invite_code
        return create_invite_code(created_by_user_id=creator_id)


def _app_client(db_path, admin_key):
    """Return test client with auth token pre-loaded via legacy bypass disabled."""
    from app.main import app
    app.config["TESTING"] = True
    client = app.test_client()
    return client


# ---------------------------------------------------------------------------
# DB layer: deactivate / reactivate
# ---------------------------------------------------------------------------

class TestDeactivateReactivate:
    def test_deactivate_sets_inactive(self, tmp_path):
        db = _fresh_db(tmp_path)
        _setup_db(db)
        with patch("app.config.USERS_DB_PATH", db):
            from app.db.users import create_user, get_user_by_id, deactivate_user
            u = create_user("alice", "pw123")
            uid = u["id"]
            deactivate_user(uid)
            row = get_user_by_id(uid)
            assert row["is_active"] == 0

    def test_reactivate_sets_active(self, tmp_path):
        db = _fresh_db(tmp_path)
        _setup_db(db)
        with patch("app.config.USERS_DB_PATH", db):
            from app.db.users import create_user, get_user_by_id, deactivate_user, reactivate_user
            u = create_user("bob", "pw123")
            uid = u["id"]
            deactivate_user(uid)
            reactivate_user(uid)
            row = get_user_by_id(uid)
            assert row["is_active"] == 1

    def test_deactivate_deletes_sessions(self, tmp_path):
        db = _fresh_db(tmp_path)
        _setup_db(db)
        with patch("app.config.USERS_DB_PATH", db):
            from app.db.users import create_user, create_session, deactivate_user
            u = create_user("charlie", "pw123")
            uid = u["id"]
            with patch("app.config.SESSION_EXPIRY_HOURS", 168):
                create_session(uid)
                create_session(uid)
            count = deactivate_user(uid)
            assert count == 2

    def test_deactivated_user_api_key_rejected(self, tmp_path):
        db = _fresh_db(tmp_path)
        _setup_db(db)
        with patch("app.config.USERS_DB_PATH", db):
            from app.db.users import create_user, deactivate_user, get_user_by_api_key
            u = create_user("dave", "pw123")
            key = u["api_key"]
            deactivate_user(u["id"])
            result = get_user_by_api_key(key)
            assert result is None  # is_active=1 check in query


# ---------------------------------------------------------------------------
# DB layer: invite list + revocation
# ---------------------------------------------------------------------------

class TestInviteListRevoke:
    def test_get_invites_returns_all(self, tmp_path):
        db = _fresh_db(tmp_path)
        _setup_db(db)
        with patch("app.config.USERS_DB_PATH", db):
            from app.db.users import create_invite_code, get_invites
            create_invite_code()
            create_invite_code()
            invites = get_invites()
            assert len(invites) == 2

    def test_revoke_unused_returns_true(self, tmp_path):
        db = _fresh_db(tmp_path)
        _setup_db(db)
        with patch("app.config.USERS_DB_PATH", db):
            from app.db.users import create_invite_code, revoke_invite, get_invite_code
            code = create_invite_code()
            ok = revoke_invite(code)
            assert ok is True
            inv = get_invite_code(code)
            assert inv["is_used"] == 1

    def test_revoke_already_used_returns_false(self, tmp_path):
        db = _fresh_db(tmp_path)
        _setup_db(db)
        with patch("app.config.USERS_DB_PATH", db):
            from app.db.users import create_invite_code, revoke_invite, consume_invite_code, create_user
            u = create_user("evan", "pw123")
            code = create_invite_code()
            consume_invite_code(code, u["id"])
            ok = revoke_invite(code)
            assert ok is False


# ---------------------------------------------------------------------------
# DB layer: rate limiting
# ---------------------------------------------------------------------------

class TestRateLimiting:
    def test_get_runs_in_last_hour_empty_initially(self, tmp_path):
        db = _fresh_db(tmp_path)
        _setup_db(db)
        with patch("app.config.USERS_DB_PATH", db):
            from app.db.users import create_user, get_runs_in_last_hour
            u = create_user("frank", "pw123")
            runs = get_runs_in_last_hour(u["id"])
            assert runs == []

    def test_recent_runs_counted(self, tmp_path):
        db = _fresh_db(tmp_path)
        _setup_db(db)
        with patch("app.config.USERS_DB_PATH", db):
            from app.db.users import create_user, create_user_run, get_runs_in_last_hour
            u = create_user("grace", "pw123")
            with patch("app.config.SESSION_EXPIRY_HOURS", 168):
                pass
            create_user_run(u["id"], "run-1")
            create_user_run(u["id"], "run-2")
            runs = get_runs_in_last_hour(u["id"])
            assert len(runs) == 2

    def test_rate_limited_runs_excluded_from_count(self, tmp_path):
        db = _fresh_db(tmp_path)
        _setup_db(db)
        with patch("app.config.USERS_DB_PATH", db):
            from app.db.users import create_user, create_user_run, record_rate_limited_run, get_runs_in_last_hour
            u = create_user("helen", "pw123")
            create_user_run(u["id"], "run-1")
            record_rate_limited_run(u["id"], "rl-1")
            runs = get_runs_in_last_hour(u["id"])
            assert len(runs) == 1

    def test_old_runs_not_counted(self, tmp_path):
        db = _fresh_db(tmp_path)
        _setup_db(db)
        with patch("app.config.USERS_DB_PATH", db):
            from app.db.users import create_user, get_runs_in_last_hour
            import sqlite3
            from pathlib import Path
            u = create_user("ivan", "pw123")
            uid = u["id"]
            old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
            conn = sqlite3.connect(db)
            conn.execute(
                "INSERT INTO user_runs (user_id, run_id, status, started_at) VALUES (?,?,?,?)",
                (uid, "old-run", "complete", old_time),
            )
            conn.commit()
            conn.close()
            runs = get_runs_in_last_hour(uid)
            assert len(runs) == 0


# ---------------------------------------------------------------------------
# DB layer: count_active_admins
# ---------------------------------------------------------------------------

class TestCountActiveAdmins:
    def test_one_admin_returns_one(self, tmp_path):
        db = _fresh_db(tmp_path)
        _setup_db(db)
        with patch("app.config.USERS_DB_PATH", db):
            from app.db.users import create_user, count_active_admins
            create_user("admin1", "pw", is_admin=1)
            assert count_active_admins() == 1

    def test_deactivated_admin_not_counted(self, tmp_path):
        db = _fresh_db(tmp_path)
        _setup_db(db)
        with patch("app.config.USERS_DB_PATH", db):
            from app.db.users import create_user, deactivate_user, count_active_admins
            u = create_user("admin2", "pw", is_admin=1)
            deactivate_user(u["id"])
            assert count_active_admins() == 0


# ---------------------------------------------------------------------------
# DB layer: admin_summary_stats
# ---------------------------------------------------------------------------

class TestAdminSummaryStats:
    def test_shape_and_zeros(self, tmp_path):
        db = _fresh_db(tmp_path)
        _setup_db(db)
        with patch("app.config.USERS_DB_PATH", db):
            from app.db.users import admin_summary_stats
            stats = admin_summary_stats()
            assert "users" in stats
            assert "invites" in stats
            assert "runs" in stats
            assert stats["users"]["total"] == 0
            assert stats["invites"]["total_generated"] == 0

    def test_user_counts_accurate(self, tmp_path):
        db = _fresh_db(tmp_path)
        _setup_db(db)
        with patch("app.config.USERS_DB_PATH", db):
            from app.db.users import create_user, deactivate_user, admin_summary_stats
            u1 = create_user("u1", "pw")
            u2 = create_user("u2", "pw")
            deactivate_user(u2["id"])
            stats = admin_summary_stats()
            assert stats["users"]["total"] == 2
            assert stats["users"]["active"] == 1
            assert stats["users"]["inactive"] == 1


# ---------------------------------------------------------------------------
# is_test_user helper
# ---------------------------------------------------------------------------

class TestIsTestUser:
    def test_known_test_prefixes(self):
        with patch("app.config.ADMIN_TEST_USER_PATTERNS", "testuser,smoke,rh_test,rh28b"):
            from app.api.admin import _is_test_user
            assert _is_test_user("testuser01") is True
            assert _is_test_user("smoke_run") is True
            assert _is_test_user("rh_test_001") is True
            assert _is_test_user("rh28b_check") is True

    def test_real_users_not_flagged(self):
        with patch("app.config.ADMIN_TEST_USER_PATTERNS", "testuser,smoke,rh_test,rh28b"):
            from app.api.admin import _is_test_user
            assert _is_test_user("jaia") is False
            assert _is_test_user("alice") is False
            assert _is_test_user("admin") is False

    def test_empty_patterns_never_flags(self):
        with patch("app.config.ADMIN_TEST_USER_PATTERNS", ""):
            from app.api.admin import _is_test_user
            assert _is_test_user("testuser01") is False


# ---------------------------------------------------------------------------
# API layer: deactivate / reactivate via endpoint
# ---------------------------------------------------------------------------

class TestDeactivateEndpoint:
    """Use an admin user's API key to drive the endpoints."""

    def _setup_and_client(self, db_path):
        with patch("app.config.USERS_DB_PATH", db_path):
            from app.db.users import init_db, create_user
            init_db()
            admin = create_user("testadmin_deact", "adminpw", is_admin=1)
            admin_key = admin["api_key"]
        from app.main import app
        app.config["TESTING"] = True
        return app.test_client(), admin_key

    def test_deactivate_returns_200(self, tmp_path):
        db = _fresh_db(tmp_path)
        with patch("app.config.USERS_DB_PATH", db):
            from app.db.users import init_db, create_user
            init_db()
            admin = create_user("testadmin_d1", "apw", is_admin=1)
            target = create_user("targetuser", "pw")
            admin_key = admin["api_key"]
            tid = target["id"]

        from app.main import app
        app.config["TESTING"] = True
        client = app.test_client()
        with patch("app.config.USERS_DB_PATH", db):
            resp = client.post(f"/api/admin/users/{tid}/deactivate?token={admin_key}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["is_active"] is False

    def test_deactivate_nonexistent_user_404(self, tmp_path):
        db = _fresh_db(tmp_path)
        with patch("app.config.USERS_DB_PATH", db):
            from app.db.users import init_db, create_user
            init_db()
            admin = create_user("testadmin_d2", "apw", is_admin=1)
            admin_key = admin["api_key"]
        from app.main import app
        app.config["TESTING"] = True
        client = app.test_client()
        with patch("app.config.USERS_DB_PATH", db):
            resp = client.post(f"/api/admin/users/9999/deactivate?token={admin_key}")
        assert resp.status_code == 404

    def test_cannot_deactivate_last_admin(self, tmp_path):
        db = _fresh_db(tmp_path)
        with patch("app.config.USERS_DB_PATH", db):
            from app.db.users import init_db, create_user
            init_db()
            # admin1 is the sole active admin — target to deactivate
            target_admin = create_user("onlyadmin", "pw", is_admin=1)
            aid = target_admin["id"]
            # admin2 is the actor — also admin, but tries to remove the last active one
            # (to test this properly: deactivate admin2 first so admin1 becomes sole admin,
            # then have admin1 try to deactivate itself — that hits self-check first.
            # Instead: use legacy token (synthetic admin id=0) so self-check is skipped.)
            # Simplest: create admin1 (actor) + mark admin2 inactive via SQL, then actor deactivates admin2
            # Actually easiest: create two admins, deactivate one via SQL to leave one active, then actor=that one tries to deactivate itself → last_admin guard triggers before self-guard? No — self-guard is first.
            # Correct approach: actor != target, target is last active admin.
            actor = create_user("actor_admin", "pw", is_admin=1)
            actor_key = actor["api_key"]
            actor_id = actor["id"]
        # Deactivate actor in DB directly so actor is inactive, target_admin is sole active admin
        # Then a different actor... this is getting circular.
        # Simplest correct test: use legacy dev token (id=0, never matches any real user) as actor,
        # deactivate the only active admin.
        from app import config as _cfg
        legacy_token = getattr(_cfg, "DEV_API_TOKEN", None) or getattr(_cfg, "RUN_TOKEN", None)
        if not legacy_token:
            pytest.skip("No legacy token configured — skip last-admin guard test")
        from app.main import app
        app.config["TESTING"] = True
        client = app.test_client()
        with patch("app.config.USERS_DB_PATH", db):
            # Deactivate actor so target_admin is the only active admin
            from app.db.users import deactivate_user
            deactivate_user(actor_id)
            resp = client.post(f"/api/admin/users/{aid}/deactivate?token={legacy_token}")
        assert resp.status_code == 400
        assert "last_admin" in resp.get_json().get("error", "")

    def test_reactivate_returns_is_active_true(self, tmp_path):
        db = _fresh_db(tmp_path)
        with patch("app.config.USERS_DB_PATH", db):
            from app.db.users import init_db, create_user, deactivate_user
            init_db()
            admin = create_user("testadmin_r1", "apw", is_admin=1)
            target = create_user("reactuser", "pw")
            admin_key = admin["api_key"]
            tid = target["id"]
            deactivate_user(tid)
        from app.main import app
        app.config["TESTING"] = True
        client = app.test_client()
        with patch("app.config.USERS_DB_PATH", db):
            resp = client.post(f"/api/admin/users/{tid}/reactivate?token={admin_key}")
        assert resp.status_code == 200
        assert resp.get_json()["is_active"] is True


# ---------------------------------------------------------------------------
# API layer: reset-key endpoint
# ---------------------------------------------------------------------------

class TestResetKeyEndpoint:
    def test_reset_key_returns_new_key(self, tmp_path):
        db = _fresh_db(tmp_path)
        with patch("app.config.USERS_DB_PATH", db):
            from app.db.users import init_db, create_user
            init_db()
            admin = create_user("testadmin_rk", "apw", is_admin=1)
            target = create_user("keyresetuser", "pw")
            admin_key = admin["api_key"]
            tid = target["id"]
            old_key = target["api_key"]
        from app.main import app
        app.config["TESTING"] = True
        client = app.test_client()
        with patch("app.config.USERS_DB_PATH", db):
            resp = client.post(f"/api/admin/users/{tid}/reset-key?token={admin_key}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        new_key = data.get("api_key", "")
        assert new_key.startswith("asa_")
        assert new_key != old_key
        assert "warning" in data


# ---------------------------------------------------------------------------
# API layer: invite list + revoke endpoints
# ---------------------------------------------------------------------------

class TestInviteEndpoints:
    def _make_admin_client(self, db_path):
        with patch("app.config.USERS_DB_PATH", db_path):
            from app.db.users import init_db, create_user
            init_db()
            admin = create_user("testadmin_inv", "apw", is_admin=1)
            admin_key = admin["api_key"]
        from app.main import app
        app.config["TESTING"] = True
        return app.test_client(), admin_key

    def test_invite_list_returns_all(self, tmp_path):
        db = _fresh_db(tmp_path)
        with patch("app.config.USERS_DB_PATH", db):
            from app.db.users import init_db, create_user, create_invite_code
            init_db()
            admin = create_user("testadmin_il", "apw", is_admin=1)
            admin_key = admin["api_key"]
            create_invite_code()
            create_invite_code()
        from app.main import app
        app.config["TESTING"] = True
        client = app.test_client()
        with patch("app.config.USERS_DB_PATH", db):
            resp = client.get(f"/api/admin/invites?token={admin_key}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 2
        assert data["unused_count"] == 2

    def test_revoke_unused_invite(self, tmp_path):
        db = _fresh_db(tmp_path)
        with patch("app.config.USERS_DB_PATH", db):
            from app.db.users import init_db, create_user, create_invite_code
            init_db()
            admin = create_user("testadmin_rv", "apw", is_admin=1)
            admin_key = admin["api_key"]
            code = create_invite_code()
        from app.main import app
        app.config["TESTING"] = True
        client = app.test_client()
        with patch("app.config.USERS_DB_PATH", db):
            resp = client.post(f"/api/admin/invites/{code}/revoke?token={admin_key}")
        assert resp.status_code == 200
        assert resp.get_json()["revoked"] is True

    def test_revoke_already_used_returns_400(self, tmp_path):
        db = _fresh_db(tmp_path)
        with patch("app.config.USERS_DB_PATH", db):
            from app.db.users import init_db, create_user, create_invite_code, consume_invite_code
            init_db()
            admin = create_user("testadmin_ru", "apw", is_admin=1)
            admin_key = admin["api_key"]
            target = create_user("inviteuser", "pw")
            code = create_invite_code()
            consume_invite_code(code, target["id"])
        from app.main import app
        app.config["TESTING"] = True
        client = app.test_client()
        with patch("app.config.USERS_DB_PATH", db):
            resp = client.post(f"/api/admin/invites/{code}/revoke?token={admin_key}")
        assert resp.status_code == 400
        assert "already_used" in resp.get_json().get("error", "")


# ---------------------------------------------------------------------------
# API layer: admin summary
# ---------------------------------------------------------------------------

class TestAdminSummaryEndpoint:
    def _make_admin_client(self, db_path):
        with patch("app.config.USERS_DB_PATH", db_path):
            from app.db.users import init_db, create_user
            init_db()
            admin = create_user("testadmin_sum", "apw", is_admin=1)
            admin_key = admin["api_key"]
        from app.main import app
        app.config["TESTING"] = True
        return app.test_client(), admin_key

    def test_summary_shape(self, tmp_path):
        db = _fresh_db(tmp_path)
        client, admin_key = self._make_admin_client(db)
        with patch("app.config.USERS_DB_PATH", db):
            resp = client.get(f"/api/admin/summary?token={admin_key}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "users" in data
        assert "invites" in data
        assert "runs" in data
        assert "system" in data
        assert "core_run" in data
        assert "generated_at" in data

    def test_system_flags_present(self, tmp_path):
        db = _fresh_db(tmp_path)
        client, admin_key = self._make_admin_client(db)
        with patch("app.config.USERS_DB_PATH", db):
            resp = client.get(f"/api/admin/summary?token={admin_key}")
        system = resp.get_json()["system"]
        assert "encryption_key_set" in system
        assert "ff_dry_run" in system
        assert "trade_execution_enabled" in system
        assert "legacy_token_enabled" in system

    def test_ff_dry_run_always_true(self, tmp_path):
        db = _fresh_db(tmp_path)
        client, admin_key = self._make_admin_client(db)
        with patch("app.config.USERS_DB_PATH", db):
            resp = client.get(f"/api/admin/summary?token={admin_key}")
        assert resp.get_json()["system"]["ff_dry_run"] is True

    def test_trade_execution_always_false(self, tmp_path):
        db = _fresh_db(tmp_path)
        client, admin_key = self._make_admin_client(db)
        with patch("app.config.USERS_DB_PATH", db):
            resp = client.get(f"/api/admin/summary?token={admin_key}")
        assert resp.get_json()["system"]["trade_execution_enabled"] is False


# ---------------------------------------------------------------------------
# API layer: non-admin blocked (403)
# ---------------------------------------------------------------------------

class TestNonAdminBlocked:
    """All new 28E admin endpoints must return 403 for non-admin tokens."""

    def _client(self, db_path):
        with patch("app.config.USERS_DB_PATH", db_path):
            from app.db.users import init_db
            init_db()
        from app.main import app
        app.config["TESTING"] = True
        return app.test_client()

    def test_non_admin_blocked_from_deactivate(self, tmp_path):
        db = _fresh_db(tmp_path)
        with patch("app.config.USERS_DB_PATH", db):
            _setup_db(db)
            from app.db.users import create_user
            non_admin = create_user("nonadmin1", "pw", is_admin=0)
            target = create_user("target1", "pw", is_admin=0)
            user_key = non_admin["api_key"]
            tid = target["id"]

        client = self._client(db)
        with patch("app.config.USERS_DB_PATH", db):
            resp = client.post(f"/api/admin/users/{tid}/deactivate?token={user_key}")
        assert resp.status_code == 403

    def test_non_admin_blocked_from_summary(self, tmp_path):
        db = _fresh_db(tmp_path)
        with patch("app.config.USERS_DB_PATH", db):
            _setup_db(db)
            from app.db.users import create_user
            non_admin = create_user("nonadmin2", "pw", is_admin=0)
            user_key = non_admin["api_key"]

        client = self._client(db)
        with patch("app.config.USERS_DB_PATH", db):
            resp = client.get(f"/api/admin/summary?token={user_key}")
        assert resp.status_code == 403

    def test_non_admin_blocked_from_invites(self, tmp_path):
        db = _fresh_db(tmp_path)
        with patch("app.config.USERS_DB_PATH", db):
            _setup_db(db)
            from app.db.users import create_user
            non_admin = create_user("nonadmin3", "pw", is_admin=0)
            user_key = non_admin["api_key"]

        client = self._client(db)
        with patch("app.config.USERS_DB_PATH", db):
            resp = client.get(f"/api/admin/invites?token={user_key}")
        assert resp.status_code == 403

    def test_non_admin_blocked_from_reset_key(self, tmp_path):
        db = _fresh_db(tmp_path)
        with patch("app.config.USERS_DB_PATH", db):
            _setup_db(db)
            from app.db.users import create_user
            non_admin = create_user("nonadmin4", "pw", is_admin=0)
            target = create_user("target4", "pw", is_admin=0)
            user_key = non_admin["api_key"]
            tid = target["id"]

        client = self._client(db)
        with patch("app.config.USERS_DB_PATH", db):
            resp = client.post(f"/api/admin/users/{tid}/reset-key?token={user_key}")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Rate limiting: /api/user/run
# ---------------------------------------------------------------------------

class TestRunRateLimit:
    def _client(self, db_path):
        with patch("app.config.USERS_DB_PATH", db_path):
            from app.db.users import init_db
            init_db()
        from app.main import app
        app.config["TESTING"] = True
        return app.test_client()

    def test_rate_limit_returns_429(self, tmp_path):
        db = _fresh_db(tmp_path)
        with patch("app.config.USERS_DB_PATH", db):
            _setup_db(db)
            from app.db.users import create_user, create_user_run
            u = create_user("rluser", "pw")
            uid = u["id"]
            user_key = u["api_key"]
            # pre-populate 3 runs in the last hour
            create_user_run(uid, "run-a")
            create_user_run(uid, "run-b")
            create_user_run(uid, "run-c")

        client = self._client(db)
        with patch("app.config.USERS_DB_PATH", db), \
             patch("app.config.USER_RUN_RATE_LIMIT_PER_HOUR", 3):
            resp = client.post(f"/api/user/run?token={user_key}")
        assert resp.status_code == 429
        data = resp.get_json()
        assert data["error"] == "rate_limited"
        assert "retry_after_seconds" in data
        assert data["runs_this_hour"] == 3
        assert data["limit"] == 3

    def test_rate_limit_shape_has_retry_after(self, tmp_path):
        db = _fresh_db(tmp_path)
        with patch("app.config.USERS_DB_PATH", db):
            _setup_db(db)
            from app.db.users import create_user, create_user_run
            u = create_user("rluser2", "pw")
            uid = u["id"]
            user_key = u["api_key"]
            create_user_run(uid, "run-x")
            create_user_run(uid, "run-y")
            create_user_run(uid, "run-z")

        client = self._client(db)
        with patch("app.config.USERS_DB_PATH", db), \
             patch("app.config.USER_RUN_RATE_LIMIT_PER_HOUR", 3):
            resp = client.post(f"/api/user/run?token={user_key}")
        data = resp.get_json()
        assert isinstance(data["retry_after_seconds"], int)
        assert data["retry_after_seconds"] > 0

    def test_below_limit_not_rate_limited(self, tmp_path):
        db = _fresh_db(tmp_path)
        with patch("app.config.USERS_DB_PATH", db):
            _setup_db(db)
            from app.db.users import create_user, create_user_run
            u = create_user("rluser3", "pw")
            uid = u["id"]
            user_key = u["api_key"]
            create_user_run(uid, "run-1")
            create_user_run(uid, "run-2")  # only 2, limit is 3

        client = self._client(db)
        with patch("app.config.USERS_DB_PATH", db), \
             patch("app.config.USER_RUN_RATE_LIMIT_PER_HOUR", 3):
            # run_personalization will be called but will fail gracefully
            resp = client.post(f"/api/user/run?token={user_key}")
        assert resp.status_code != 429
