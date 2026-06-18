"""
app/db/users.py — users.db schema, init, and helpers.

Tables: users, invite_codes, sessions.
Passwords: bcrypt. Robinhood password: Fernet encryption.
API keys: asa_<32 hex>. Session tokens: 32 hex chars.
Invite codes: ASA-XXXX-XXXX-XXXX (uppercase alphanum segments).
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import string
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    api_key TEXT NOT NULL UNIQUE,
    robinhood_username TEXT,
    robinhood_password_encrypted TEXT,
    is_admin INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS invite_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    created_by_user_id INTEGER,
    used_by_user_id INTEGER,
    used_at TEXT,
    is_used INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    session_token TEXT NOT NULL UNIQUE,
    created_at TEXT DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL,
    last_used_at TEXT
);
"""


def _db_path() -> str:
    return str(config.USERS_DB_PATH)


@contextmanager
def _connect():
    path = _db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Create tables if they don't exist."""
    with _connect() as conn:
        conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# Key / token generation
# ---------------------------------------------------------------------------

def generate_api_key() -> str:
    return "asa_" + secrets.token_hex(32)


def generate_session_token() -> str:
    return secrets.token_hex(32)


def generate_invite_code() -> str:
    chars = string.ascii_uppercase + string.digits
    parts = ["ASA"] + ["".join(secrets.choice(chars) for _ in range(4)) for _ in range(3)]
    return "-".join(parts)


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    import bcrypt  # type: ignore
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def check_password(plain: str, hashed: str) -> bool:
    try:
        import bcrypt  # type: ignore
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Robinhood credential encryption
# ---------------------------------------------------------------------------

def _fernet():
    from cryptography.fernet import Fernet  # type: ignore
    key = config.ROBINHOOD_ENCRYPTION_KEY
    if not key:
        raise RuntimeError("ROBINHOOD_ENCRYPTION_KEY not set")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_robinhood_password(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_robinhood_password(encrypted: str) -> str:
    return _fernet().decrypt(encrypted.encode()).decode()


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------

def create_user(
    username: str,
    password_plain: str,
    robinhood_username: str = "",
    robinhood_password_plain: str = "",
    is_admin: int = 0,
) -> dict[str, Any]:
    """Create user. Returns user dict. Raises ValueError on duplicate username."""
    pw_hash = hash_password(password_plain)
    api_key = generate_api_key()
    rh_enc = ""
    if robinhood_password_plain and config.ROBINHOOD_ENCRYPTION_KEY:
        try:
            rh_enc = encrypt_robinhood_password(robinhood_password_plain)
        except Exception:
            pass  # encryption not configured — store empty
    with _connect() as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, api_key, robinhood_username, "
            "robinhood_password_encrypted, is_admin) VALUES (?,?,?,?,?,?)",
            (username, pw_hash, api_key, robinhood_username or "", rh_enc, is_admin),
        )
    return get_user_by_username(username) or {}


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_by_username(username: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    return dict(row) if row else None


def get_user_by_api_key(api_key: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE api_key=? AND is_active=1", (api_key,)).fetchone()
    return dict(row) if row else None


def update_last_login(user_id: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute("UPDATE users SET last_login_at=? WHERE id=?", (now, user_id))


def rotate_api_key(user_id: int) -> str:
    new_key = generate_api_key()
    with _connect() as conn:
        conn.execute("UPDATE users SET api_key=? WHERE id=?", (new_key, user_id))
    return new_key


def user_count() -> int:
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
    return row[0] if row else 0


# ---------------------------------------------------------------------------
# Session CRUD
# ---------------------------------------------------------------------------

def create_session(user_id: int) -> str:
    token = generate_session_token()
    expires = (
        datetime.now(timezone.utc) + timedelta(hours=int(config.SESSION_EXPIRY_HOURS))
    ).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sessions (user_id, session_token, expires_at, last_used_at) VALUES (?,?,?,?)",
            (user_id, token, expires, now),
        )
    return token


def get_session(token: str) -> dict[str, Any] | None:
    """Return session row if valid and not expired."""
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE session_token=? AND expires_at > ?",
            (token, now),
        ).fetchone()
        if row:
            conn.execute("UPDATE sessions SET last_used_at=? WHERE session_token=?", (now, token))
    return dict(row) if row else None


def delete_session(token: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE session_token=?", (token,))


def get_user_by_session_token(token: str) -> dict[str, Any] | None:
    session = get_session(token)
    if not session:
        return None
    return get_user_by_id(session["user_id"])


# ---------------------------------------------------------------------------
# Invite code CRUD
# ---------------------------------------------------------------------------

def create_invite_code(created_by_user_id: int | None = None) -> str:
    code = generate_invite_code()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO invite_codes (code, created_by_user_id) VALUES (?,?)",
            (code, created_by_user_id),
        )
    return code


def get_invite_code(code: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM invite_codes WHERE code=?", (code,)).fetchone()
    return dict(row) if row else None


def consume_invite_code(code: str, used_by_user_id: int) -> bool:
    """Mark invite code used. Returns True if succeeded."""
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE invite_codes SET is_used=1, used_by_user_id=?, used_at=? "
            "WHERE code=? AND is_used=0",
            (used_by_user_id, now, code),
        )
    return cur.rowcount == 1


# ---------------------------------------------------------------------------
# Admin seed
# ---------------------------------------------------------------------------

def seed_admin_if_needed() -> None:
    """Create admin user from env vars if no users exist. Logs API key once."""
    try:
        init_db()
        if user_count() > 0:
            return
        username = config.ASA_ADMIN_USERNAME
        password = config.ASA_ADMIN_PASSWORD
        if not username or not password:
            print("28A: ASA_ADMIN_USERNAME or ASA_ADMIN_PASSWORD not set — skipping admin seed.", flush=True)
            return
        user = create_user(username, password, is_admin=1)
        # Log API key ONCE on first boot — never again.
        api_key = user.get("api_key", "")
        print(f"28A: Admin user '{username}' created on first boot.", flush=True)
        print(f"28A: Admin API key (save this): {api_key}", flush=True)
    except Exception as exc:
        print(f"28A: Admin seed failed (non-fatal): {exc}", flush=True)
