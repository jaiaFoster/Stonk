"""
app/db/users.py — users.db schema, init, and helpers.

Tables: users, invite_codes, sessions, user_errors.
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
    is_dev INTEGER DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS user_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    run_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    error_message TEXT,
    positions_fetched INTEGER DEFAULT 0,
    daily_opportunity_count INTEGER DEFAULT 0,
    core_run_id_used TEXT,
    core_run_freshness_hours REAL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS user_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    quantity REAL,
    avg_cost REAL,
    current_price REAL,
    market_value REAL,
    unrealized_pnl_pct REAL,
    account_type TEXT,
    fetched_at TEXT DEFAULT (datetime('now')),
    position_type TEXT DEFAULT 'stock',
    option_details TEXT
);

CREATE TABLE IF NOT EXISTS user_daily_opportunity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    action TEXT NOT NULL,
    strategy TEXT NOT NULL,
    signal_score REAL,
    verdict TEXT,
    already_held INTEGER DEFAULT 0,
    position_size_context TEXT,
    debit_sizing_context TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS user_option_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    underlying TEXT NOT NULL,
    strategy TEXT,
    option_type TEXT,
    strike REAL,
    front_expiration TEXT,
    back_expiration TEXT,
    front_dte INTEGER,
    back_dte INTEGER,
    quantity REAL,
    side_inferred INTEGER DEFAULT 0,
    exit_signal TEXT,
    action TEXT,
    calendar_json TEXT,
    fetched_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS user_broker_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    account_number TEXT NOT NULL,
    account_type TEXT,
    broker_type TEXT DEFAULT 'robinhood',
    discovered_at TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, account_number)
);

CREATE TABLE IF NOT EXISTS user_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    run_id TEXT,
    error_source TEXT NOT NULL,
    error_type TEXT,
    error_message TEXT,
    created_at TEXT DEFAULT (datetime('now'))
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


def get_encryption_key_status() -> bool:
    """Return True if ROBINHOOD_ENCRYPTION_KEY is set and is a valid Fernet key."""
    try:
        import os
        from cryptography.fernet import Fernet  # type: ignore
        # TKT-034: read directly from env to avoid config-level whitespace issues
        key = os.environ.get("ROBINHOOD_ENCRYPTION_KEY", "").strip()
        if not key:
            return False
        Fernet(key.encode() if isinstance(key, str) else key)
        return True
    except Exception:
        return False


def _migrate_28c(conn: sqlite3.Connection) -> None:
    """Add 28C columns to users table. Safe to run repeatedly (idempotent)."""
    for sql in (
        "ALTER TABLE users ADD COLUMN broker_type TEXT DEFAULT 'robinhood'",
        "ALTER TABLE users ADD COLUMN credentials_validated_at TEXT",
        "ALTER TABLE users ADD COLUMN credentials_last_error TEXT",
    ):
        try:
            conn.execute(sql)
        except Exception:
            pass  # column already exists — safe to ignore


def _migrate_29a(conn: sqlite3.Connection) -> None:
    """Add 29A columns. Idempotent."""
    for sql in (
        "ALTER TABLE users ADD COLUMN is_dev INTEGER DEFAULT 0",
        "ALTER TABLE user_positions ADD COLUMN position_type TEXT DEFAULT 'stock'",
        "ALTER TABLE user_positions ADD COLUMN option_details TEXT",
    ):
        try:
            conn.execute(sql)
        except Exception:
            pass


def _migrate_43(conn: sqlite3.Connection) -> None:
    """TKT-043: user_broker_accounts table + account_number column. Idempotent."""
    for sql in (
        "ALTER TABLE user_positions ADD COLUMN account_number TEXT",
    ):
        try:
            conn.execute(sql)
        except Exception:
            pass


def _migrate_45(conn: sqlite3.Connection) -> None:
    """TKT-045: nickname column on user_broker_accounts. Idempotent."""
    try:
        conn.execute("ALTER TABLE user_broker_accounts ADD COLUMN nickname TEXT")
    except Exception:
        pass


def log_user_error(
    user_id: int | None,
    error_source: str,
    error_type: str | None = None,
    error_message: str | None = None,
    run_id: str | None = None,
) -> None:
    """Fire-and-forget per-user error log. NEVER raises."""
    try:
        if error_message:
            from app.services.redaction_service import known_secrets
            for secret in known_secrets():
                if secret:
                    error_message = error_message.replace(secret, "[REDACTED]")
            error_message = error_message[:500]
        with _connect() as conn:
            conn.execute(
                "INSERT INTO user_errors (user_id, run_id, error_source, error_type, error_message) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, run_id, error_source, error_type, error_message),
            )
    except Exception:
        pass


def get_user_errors(
    user_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Return recent errors, optionally filtered by user_id."""
    with _connect() as conn:
        if user_id is not None:
            rows = conn.execute(
                "SELECT id, user_id, run_id, error_source, error_type, error_message, created_at "
                "FROM user_errors WHERE user_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
                (user_id, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, user_id, run_id, error_source, error_type, error_message, created_at "
                "FROM user_errors ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [dict(r) for r in rows]


def count_user_errors_24h() -> int:
    """Count errors in last 24h for admin summary."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    with _connect() as conn:
        return (conn.execute(
            "SELECT COUNT(*) FROM user_errors WHERE created_at > ?", (cutoff,)
        ).fetchone() or [0])[0]


def init_db() -> None:
    """Create tables if they don't exist, run schema migrations."""
    with _connect() as conn:
        conn.executescript(SCHEMA)
        _migrate_28c(conn)
        _migrate_29a(conn)
        _migrate_43(conn)
        _migrate_45(conn)
        cols = [c[1] for c in conn.execute("PRAGMA table_info(user_positions)").fetchall()]
        if "position_type" not in cols or "option_details" not in cols:
            print(f"[init_db] WARNING: user_positions missing options columns. "
                  f"columns={cols}", flush=True)
        else:
            print(f"[init_db] user_positions schema OK: position_type and option_details present.", flush=True)
        acct_tables = [t[0] for t in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='user_broker_accounts'"
        ).fetchall()]
        if acct_tables:
            print("[init_db] user_broker_accounts table OK.", flush=True)
        else:
            print("[init_db] WARNING: user_broker_accounts table missing.", flush=True)


def seed_sysadmin() -> None:
    """
    TKT-036: Ensure asa_admin sysadmin exists. Demote ASA_ADMIN_USERNAME account to member + is_dev=1.
    Safe to call repeatedly — all operations are idempotent.
    Never logs passwords.
    """
    sysadmin_username = config.ASA_SYSADMIN_USERNAME or "asa_admin"
    sysadmin_password = config.ASA_SYSADMIN_PASSWORD or ""
    if not sysadmin_password:
        import secrets as _sec
        sysadmin_password = _sec.token_urlsafe(32)
        print(
            f"[seed_sysadmin] ASA_SYSADMIN_PASSWORD not set — generated random password for {sysadmin_username}. "
            "Set ASA_SYSADMIN_PASSWORD env var to persist it across restarts.",
            flush=True,
        )

    with _connect() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE username=? AND is_admin=1", (sysadmin_username,)
        ).fetchone()
        if not row:
            pw_hash = hash_password(sysadmin_password)
            api_key = generate_api_key()
            conn.execute(
                "INSERT OR IGNORE INTO users (username, password_hash, api_key, is_admin, is_active, is_dev) "
                "VALUES (?,?,?,1,1,1)",
                (sysadmin_username, pw_hash, api_key),
            )
            new_row = conn.execute("SELECT api_key FROM users WHERE username=?", (sysadmin_username,)).fetchone()
            if new_row:
                print(f"[seed_sysadmin] Created {sysadmin_username} sysadmin account.", flush=True)
                print(f"[seed_sysadmin] {sysadmin_username} API key (save this): {new_row[0]}", flush=True)

        # Demote ASA_ADMIN_USERNAME account (jaia) to member + set is_dev=1
        demote_username = config.ASA_ADMIN_USERNAME or "jaia"
        demote_row = conn.execute(
            "SELECT id FROM users WHERE username=?", (demote_username,)
        ).fetchone()
        if demote_row:
            conn.execute(
                "UPDATE users SET is_admin=0, is_dev=1 WHERE username=?", (demote_username,)
            )
            print(f"[seed_sysadmin] Demoted {demote_username} to member with is_dev=1.", flush=True)


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
    import os
    from cryptography.fernet import Fernet  # type: ignore
    # Read directly from env (not config module) to avoid cached/whitespace issues.
    key = os.environ.get("ROBINHOOD_ENCRYPTION_KEY", "").strip()
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


def update_broker_credentials(
    user_id: int,
    robinhood_username: str,
    robinhood_password_plain: str,
) -> None:
    """Encrypt and store updated Robinhood credentials for user_id."""
    rh_enc = encrypt_robinhood_password(robinhood_password_plain)
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET robinhood_username=?, robinhood_password_encrypted=?, "
            "broker_type='robinhood', credentials_validated_at=?, credentials_last_error=NULL "
            "WHERE id=?",
            (robinhood_username, rh_enc, now, user_id),
        )


def set_credentials_validated(user_id: int) -> None:
    """Mark credentials as validated now."""
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET credentials_validated_at=?, credentials_last_error=NULL WHERE id=?",
            (now, user_id),
        )


def set_credentials_error(user_id: int, error_message: str) -> None:
    """Record credential error on failed run."""
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET credentials_last_error=? WHERE id=?",
            ((error_message or "")[:500], user_id),
        )


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
# 28B: User run CRUD
# ---------------------------------------------------------------------------

def create_user_run(
    user_id: int,
    run_id: str,
    core_run_id: str | None = None,
    core_run_freshness_hours: float | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO user_runs (user_id, run_id, status, started_at, core_run_id_used, core_run_freshness_hours) "
            "VALUES (?,?,?,?,?,?)",
            (user_id, run_id, "running", now, core_run_id, core_run_freshness_hours),
        )


def complete_user_run(run_id: str, positions_fetched: int, daily_opportunity_count: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            "UPDATE user_runs SET status=?, completed_at=?, positions_fetched=?, daily_opportunity_count=? "
            "WHERE run_id=?",
            ("complete", now, positions_fetched, daily_opportunity_count, run_id),
        )


def fail_user_run(run_id: str, error_message: str, timed_out: bool = False) -> None:
    now = datetime.now(timezone.utc).isoformat()
    status = "timeout" if timed_out else "failed"
    with _connect() as conn:
        conn.execute(
            "UPDATE user_runs SET status=?, completed_at=?, error_message=? WHERE run_id=?",
            (status, now, (error_message or "")[:500], run_id),
        )


def get_latest_user_run(user_id: int) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM user_runs WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def get_latest_complete_user_run(user_id: int) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM user_runs WHERE user_id=? AND status='complete' ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def save_user_positions(user_id: int, run_id: str, positions: list[dict[str, Any]]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        (
            user_id, run_id,
            str(p.get("ticker") or "").upper(),
            p.get("quantity"),
            p.get("avg_cost"),
            p.get("current_price"),
            p.get("market_value"),
            p.get("unrealized_pnl_pct"),
            p.get("account_type"),
            now,
            p.get("account_number"),
        )
        for p in positions
        if p.get("ticker")
    ]
    if not rows:
        return
    with _connect() as conn:
        conn.executemany(
            "INSERT INTO user_positions (user_id, run_id, ticker, quantity, avg_cost, current_price, "
            "market_value, unrealized_pnl_pct, account_type, fetched_at, account_number) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )


def get_user_positions(user_id: int, run_id: str | None = None) -> list[dict[str, Any]]:
    with _connect() as conn:
        if run_id:
            rows = conn.execute(
                "SELECT * FROM user_positions WHERE user_id=? AND run_id=? ORDER BY market_value DESC NULLS LAST",
                (user_id, run_id),
            ).fetchall()
        else:
            latest = get_latest_complete_user_run(user_id)
            if not latest:
                return []
            rows = conn.execute(
                "SELECT * FROM user_positions WHERE user_id=? AND run_id=? ORDER BY market_value DESC NULLS LAST",
                (user_id, latest["run_id"]),
            ).fetchall()
    return [dict(r) for r in rows]


def save_user_option_positions_to_positions(
    user_id: int, run_id: str, options_positions: list[dict[str, Any]]
) -> None:
    """
    TKT-035: Write normalized option positions to user_positions with position_type='options'.
    option_details stores strategy_type, strike, expiration, legs, etc. as JSON.
    """
    import json as _json
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for opt in options_positions:
        if not isinstance(opt, dict):
            continue
        ticker = str(opt.get("ticker") or opt.get("underlying") or "").upper().strip()
        if not ticker:
            continue
        details = {
            "strategy_type": opt.get("strategy_type"),
            "option_type": opt.get("option_type"),
            "strike": opt.get("strike"),
            "expiration": opt.get("expiration"),
            "legs": opt.get("legs") or [],
            "net_debit": opt.get("net_debit"),
            "current_value": opt.get("current_value"),
            "max_profit": opt.get("max_profit"),
            "max_loss": opt.get("max_loss"),
            "pct_of_max_profit": opt.get("pct_of_max_profit"),
            "exit_signal": opt.get("exit_signal"),
            "exit_reason": opt.get("exit_reason"),
        }
        rows.append((
            user_id, run_id, ticker,
            opt.get("quantity"),
            opt.get("net_debit"),
            opt.get("current_value"),
            opt.get("current_value"),
            opt.get("unrealized_pnl_pct"),
            opt.get("account_type") or "options",
            now,
            "options",
            _json.dumps(details),
            opt.get("account_number"),
        ))
    if not rows:
        return
    with _connect() as conn:
        conn.executemany(
            "INSERT INTO user_positions (user_id, run_id, ticker, quantity, avg_cost, current_price, "
            "market_value, unrealized_pnl_pct, account_type, fetched_at, position_type, option_details, "
            "account_number) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )


def save_user_daily_opportunity(user_id: int, run_id: str, actions: list[dict[str, Any]]) -> None:
    rows = [
        (
            user_id, run_id,
            str(a.get("ticker") or "").upper(),
            str(a.get("action") or ""),
            str(a.get("strategy") or ""),
            a.get("signal_score"),
            a.get("verdict"),
            1 if a.get("already_held") else 0,
            a.get("position_size_context"),
            a.get("debit_sizing_context"),
            a.get("notes"),
        )
        for a in actions
        if a.get("ticker") and a.get("action")
    ]
    if not rows:
        return
    with _connect() as conn:
        conn.executemany(
            "INSERT INTO user_daily_opportunity "
            "(user_id, run_id, ticker, action, strategy, signal_score, verdict, "
            "already_held, position_size_context, debit_sizing_context, notes) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )


def get_user_daily_opportunity(user_id: int, run_id: str | None = None) -> list[dict[str, Any]]:
    with _connect() as conn:
        if run_id:
            rows = conn.execute(
                "SELECT * FROM user_daily_opportunity WHERE user_id=? AND run_id=? ORDER BY signal_score DESC NULLS LAST",
                (user_id, run_id),
            ).fetchall()
        else:
            latest = get_latest_complete_user_run(user_id)
            if not latest:
                return []
            rows = conn.execute(
                "SELECT * FROM user_daily_opportunity WHERE user_id=? AND run_id=? ORDER BY signal_score DESC NULLS LAST",
                (user_id, latest["run_id"]),
            ).fetchall()
    return [dict(r) for r in rows]


def get_user_run_history(user_id: int, limit: int = 10) -> list[dict[str, Any]]:
    """Return last `limit` runs for a user, newest first."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM user_runs WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def count_user_runs(user_id: int) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM user_runs WHERE user_id=?", (user_id,)
        ).fetchone()
    return row[0] if row else 0


def get_active_user_run(user_id: int, stale_seconds: int = 180) -> dict[str, Any] | None:
    """
    Return a currently-running run if one exists and is not stale.
    Stale = started more than `stale_seconds` ago with no completion.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)
    ).isoformat()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM user_runs WHERE user_id=? AND status='running' AND started_at > ? "
            "ORDER BY id DESC LIMIT 1",
            (user_id, cutoff),
        ).fetchone()
    return dict(row) if row else None


def get_all_users_with_run_status() -> list[dict[str, Any]]:
    """
    Return all users joined with their latest run record.
    NEVER returns api_key, password_hash, or robinhood_password_encrypted.
    """
    with _connect() as conn:
        rows = conn.execute("""
            SELECT
                u.id AS user_id,
                u.username,
                u.is_active,
                u.is_admin,
                u.is_dev,
                u.broker_type,
                u.credentials_validated_at,
                u.credentials_last_error,
                u.created_at,
                u.last_login_at,
                r.status     AS last_run_status,
                r.completed_at AS last_run_at,
                r.positions_fetched AS last_run_positions_fetched
            FROM users u
            LEFT JOIN user_runs r ON r.id = (
                SELECT id FROM user_runs
                WHERE user_id = u.id
                ORDER BY id DESC LIMIT 1
            )
            ORDER BY u.id
        """).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Admin seed
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 28E: Deactivate / reactivate
# ---------------------------------------------------------------------------

def deactivate_user(user_id: int) -> int:
    """
    Set is_active=0. Delete all sessions for user. Returns session count deleted.
    Caller must check self-deactivation and last-admin constraints.
    """
    with _connect() as conn:
        cur = conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        sessions_deleted = cur.rowcount
        conn.execute("UPDATE users SET is_active=0 WHERE id=?", (user_id,))
    return sessions_deleted


def reactivate_user(user_id: int) -> None:
    """Set is_active=1. Sessions are NOT restored — user must log in fresh."""
    with _connect() as conn:
        conn.execute("UPDATE users SET is_active=1 WHERE id=?", (user_id,))


def count_active_admins() -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM users WHERE is_admin=1 AND is_active=1"
        ).fetchone()
    return row[0] if row else 0


# ---------------------------------------------------------------------------
# 28E: Invite list + revocation
# ---------------------------------------------------------------------------

def get_invites() -> list[dict[str, Any]]:
    """Return all invite codes joined with username of consuming user."""
    with _connect() as conn:
        rows = conn.execute("""
            SELECT
                ic.code,
                ic.is_used,
                ic.used_at,
                ic.created_at,
                u.username AS used_by_username
            FROM invite_codes ic
            LEFT JOIN users u ON u.id = ic.used_by_user_id
            ORDER BY ic.id DESC
        """).fetchall()
    return [dict(r) for r in rows]


def revoke_invite(code: str) -> bool:
    """
    Mark an UNUSED invite code as used (consumed) so it cannot be used for signup.
    Returns True if revoked, False if already used or not found.
    """
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE invite_codes SET is_used=1, used_at=? WHERE code=? AND is_used=0",
            (now, code),
        )
    return cur.rowcount == 1


# ---------------------------------------------------------------------------
# 28E: Rate limiting
# ---------------------------------------------------------------------------

def get_runs_in_last_hour(user_id: int) -> list[dict[str, Any]]:
    """Return user_runs rows started within the last 60 minutes, excluding rate_limited."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=60)
    ).isoformat()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM user_runs WHERE user_id=? AND started_at > ? AND status != 'rate_limited' "
            "ORDER BY started_at ASC",
            (user_id, cutoff),
        ).fetchall()
    return [dict(r) for r in rows]


def record_rate_limited_run(user_id: int, run_id: str) -> None:
    """Record a rate-limited run attempt for audit trail."""
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO user_runs (user_id, run_id, status, started_at, completed_at) VALUES (?,?,?,?,?)",
            (user_id, run_id, "rate_limited", now, now),
        )


# ---------------------------------------------------------------------------
# 28E: Admin summary
# ---------------------------------------------------------------------------

def admin_summary_stats() -> dict[str, Any]:
    """Return aggregate stats for GET /api/admin/summary."""
    cutoff_24h = (
        datetime.now(timezone.utc) - timedelta(hours=24)
    ).isoformat()
    with _connect() as conn:
        users_total = (conn.execute("SELECT COUNT(*) FROM users").fetchone() or [0])[0]
        users_active = (conn.execute("SELECT COUNT(*) FROM users WHERE is_active=1").fetchone() or [0])[0]
        users_cred_validated = (
            conn.execute("SELECT COUNT(*) FROM users WHERE credentials_validated_at IS NOT NULL").fetchone() or [0]
        )[0]
        users_with_run = (
            conn.execute("SELECT COUNT(DISTINCT user_id) FROM user_runs WHERE status='complete'").fetchone() or [0]
        )[0]

        invites_total = (conn.execute("SELECT COUNT(*) FROM invite_codes").fetchone() or [0])[0]
        invites_unused = (conn.execute("SELECT COUNT(*) FROM invite_codes WHERE is_used=0").fetchone() or [0])[0]

        runs_total = (conn.execute("SELECT COUNT(*) FROM user_runs").fetchone() or [0])[0]
        runs_24h = (
            conn.execute(
                "SELECT COUNT(*) FROM user_runs WHERE started_at > ?", (cutoff_24h,)
            ).fetchone() or [0]
        )[0]
        failed_24h = (
            conn.execute(
                "SELECT COUNT(*) FROM user_runs WHERE started_at > ? AND status IN ('failed','timeout')",
                (cutoff_24h,),
            ).fetchone() or [0]
        )[0]
        rate_limited_24h = (
            conn.execute(
                "SELECT COUNT(*) FROM user_runs WHERE started_at > ? AND status='rate_limited'",
                (cutoff_24h,),
            ).fetchone() or [0]
        )[0]

    return {
        "users": {
            "total": users_total,
            "active": users_active,
            "inactive": users_total - users_active,
            "credentials_validated": users_cred_validated,
            "with_completed_run": users_with_run,
        },
        "invites": {
            "total_generated": invites_total,
            "unused": invites_unused,
            "used": invites_total - invites_unused,
        },
        "runs": {
            "total_all_users": runs_total,
            "last_24h": runs_24h,
            "failed_last_24h": failed_24h,
            "rate_limited_last_24h": rate_limited_24h,
        },
    }


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


# ---------------------------------------------------------------------------
# 29A: User option positions (TKT-035)
# ---------------------------------------------------------------------------

def save_user_option_positions(user_id: int, run_id: str, calendars: list[dict[str, Any]]) -> None:
    """Persist detected calendar spreads from a personalization run."""
    import json as _json
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for cal in calendars:
        if not isinstance(cal, dict):
            continue
        underlying = str(cal.get("underlying") or cal.get("ticker") or "").upper().strip()
        if not underlying:
            continue
        rows.append((
            user_id, run_id, underlying,
            str(cal.get("strategy") or "Long Calendar Spread"),
            str(cal.get("option_type") or ""),
            cal.get("strike"),
            cal.get("front_expiration"),
            cal.get("back_expiration"),
            cal.get("front_dte"),
            cal.get("back_dte"),
            cal.get("quantity"),
            1 if cal.get("side_inferred") else 0,
            _exit_signal_for_calendar(cal),
            str(cal.get("action") or "MONITOR"),
            _json.dumps(cal),
            now,
        ))
    if not rows:
        return
    with _connect() as conn:
        conn.executemany(
            "INSERT INTO user_option_positions "
            "(user_id, run_id, underlying, strategy, option_type, strike, "
            "front_expiration, back_expiration, front_dte, back_dte, quantity, "
            "side_inferred, exit_signal, action, calendar_json, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )


def _exit_signal_for_calendar(cal: dict[str, Any]) -> str | None:
    front_dte = cal.get("front_dte")
    if isinstance(front_dte, int):
        if front_dte <= 1:
            return "immediate"
        if front_dte <= 3:
            return "assignment_risk"
        if front_dte <= 7:
            return "check_exit"
    return None


def get_user_option_positions(user_id: int, run_id: str | None = None) -> list[dict[str, Any]]:
    """Return stored calendar spreads for the latest (or specified) completed run."""
    with _connect() as conn:
        if run_id:
            rows = conn.execute(
                "SELECT * FROM user_option_positions WHERE user_id=? AND run_id=? ORDER BY underlying",
                (user_id, run_id),
            ).fetchall()
        else:
            latest = get_latest_complete_user_run(user_id)
            if not latest:
                return []
            rows = conn.execute(
                "SELECT * FROM user_option_positions WHERE user_id=? AND run_id=? ORDER BY underlying",
                (user_id, latest["run_id"]),
            ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# TKT-043: Broker account discovery persistence
# ---------------------------------------------------------------------------

def save_user_broker_accounts(user_id: int, accounts: list[dict[str, Any]]) -> None:
    """Upsert discovered broker accounts for a user. Replaces stale entries."""
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute("DELETE FROM user_broker_accounts WHERE user_id=?", (user_id,))
        for acct in accounts:
            acct_num = str(acct.get("account_number") or "").strip()
            if not acct_num:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO user_broker_accounts "
                "(user_id, account_number, account_type, broker_type, discovered_at) "
                "VALUES (?,?,?,?,?)",
                (
                    user_id,
                    acct_num,
                    acct.get("account_type") or "Unknown",
                    acct.get("broker_type") or "robinhood",
                    now,
                ),
            )


def get_user_broker_accounts(user_id: int) -> list[dict[str, Any]]:
    """Return discovered broker accounts for a user."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM user_broker_accounts WHERE user_id=? ORDER BY account_number",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def set_account_nickname(user_id: int, account_number: str, nickname: str | None) -> bool:
    """Set or clear nickname for a broker account. Returns True if row existed."""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE user_broker_accounts SET nickname=? WHERE user_id=? AND account_number=?",
            (nickname, user_id, account_number),
        )
    return cur.rowcount > 0


def get_account_nickname(user_id: int, account_number: str) -> str | None:
    """Return nickname for a specific broker account, or None."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT nickname FROM user_broker_accounts WHERE user_id=? AND account_number=?",
            (user_id, account_number),
        ).fetchone()
    return row["nickname"] if row else None
