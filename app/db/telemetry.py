"""Advisor telemetry — endpoint event log + recommendation feedback journal."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS advisor_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    token_identity TEXT,
    run_id_served TEXT,
    timestamp TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS advisor_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    run_id TEXT,
    action_taken TEXT,
    outcome TEXT,
    notes TEXT,
    submitted_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS signal_engagement (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    ticker TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    verdict TEXT,
    action TEXT NOT NULL,
    broker_mode TEXT,
    session_id TEXT,
    run_id TEXT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_signal_engagement_ticker
    ON signal_engagement(ticker, strategy_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_signal_engagement_user
    ON signal_engagement(user_id, timestamp);
"""

_VALID_ACTIONS = {"bought", "watched", "ignored", "rejected"}
_VALID_OUTCOMES = {"positive", "negative", "neutral", "pending", "null"}


@contextmanager
def _connect(db_path: str):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_schema(db_path: str) -> None:
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA)


def _token_identity(token: str | None) -> str | None:
    """Never store raw token — return label if matches RUN_TOKEN, else short hash."""
    if not token:
        return None
    if config.RUN_TOKEN and token == config.RUN_TOKEN:
        # Use env var name as label, not the value
        return "run_token"
    # Unknown token — store truncated sha256 only
    return "sha256:" + hashlib.sha256(token.encode()).hexdigest()[:12]


def log_event(endpoint: str, token: str | None, run_id_served: str | None = None,
              db_path: str | None = None) -> None:
    """Write one advisor_events row. Swallows all errors — must never affect response."""
    if not config.TELEMETRY_ENABLED:
        return
    path = db_path or config.TELEMETRY_DB_PATH
    try:
        _ensure_schema(path)
        with _connect(path) as conn:
            conn.execute(
                "INSERT INTO advisor_events (event_type, endpoint, token_identity, run_id_served) "
                "VALUES (?, ?, ?, ?)",
                ("endpoint_hit", endpoint, _token_identity(token), run_id_served),
            )
    except Exception:
        pass


def record_feedback(ticker: str, run_id: str | None, action_taken: str | None,
                    outcome: str | None, notes: str | None,
                    db_path: str | None = None) -> None:
    """Write one advisor_feedback row. Swallows all errors."""
    if not config.TELEMETRY_ENABLED:
        return
    path = db_path or config.TELEMETRY_DB_PATH
    try:
        _ensure_schema(path)
        with _connect(path) as conn:
            conn.execute(
                "INSERT INTO advisor_feedback (ticker, run_id, action_taken, outcome, notes) "
                "VALUES (?, ?, ?, ?, ?)",
                (ticker, run_id, action_taken, outcome, notes),
            )
    except Exception:
        pass


_VALID_SIGNAL_ACTIONS = {"view_signal", "expand_detail", "view_ticker", "copy_structure"}


def record_signal_engagement(
    ticker: str,
    strategy_id: str,
    action: str,
    verdict: str | None = None,
    user_id: str | None = None,
    broker_mode: str | None = None,
    session_id: str | None = None,
    run_id: str | None = None,
    db_path: str | None = None,
) -> None:
    """Write one signal_engagement row. Swallows all errors — must never affect response."""
    path = db_path or config.TELEMETRY_DB_PATH
    safe_action = action if action in _VALID_SIGNAL_ACTIONS else "view_signal"
    try:
        _ensure_schema(path)
        with _connect(path) as conn:
            conn.execute(
                "INSERT INTO signal_engagement "
                "(user_id, ticker, strategy_id, verdict, action, broker_mode, session_id, run_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(user_id) if user_id is not None else None,
                    str(ticker).upper()[:20],
                    str(strategy_id)[:64],
                    str(verdict)[:20] if verdict else None,
                    safe_action,
                    str(broker_mode)[:20] if broker_mode else None,
                    str(session_id)[:64] if session_id else None,
                    str(run_id)[:64] if run_id else None,
                ),
            )
    except Exception:
        pass


def signal_engagement_summary(days: int = 7, db_path: str | None = None) -> dict[str, Any]:
    """Return aggregate signal engagement for admin telemetry. Safe on any error."""
    path = db_path or config.TELEMETRY_DB_PATH
    base: dict[str, Any] = {
        "period_days": days,
        "total_engagements": 0,
        "by_ticker": [],
        "by_strategy": [],
        "broker_optional_pct": None,
    }
    try:
        if not Path(path).exists():
            return base
        _ensure_schema(path)
        cutoff = f"datetime('now', '-{int(days)} days')"
        with _connect(path) as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM signal_engagement WHERE timestamp >= {cutoff}"
            ).fetchone()[0] or 0
            base["total_engagements"] = total

            by_ticker = conn.execute(
                f"SELECT ticker, strategy_id, COUNT(*) as count, MAX(timestamp) as last_seen "
                f"FROM signal_engagement WHERE timestamp >= {cutoff} "
                f"GROUP BY ticker, strategy_id ORDER BY count DESC LIMIT 50"
            ).fetchall()
            base["by_ticker"] = [dict(r) for r in by_ticker]

            by_strategy = conn.execute(
                f"SELECT strategy_id, COUNT(*) as count "
                f"FROM signal_engagement WHERE timestamp >= {cutoff} "
                f"GROUP BY strategy_id ORDER BY count DESC"
            ).fetchall()
            base["by_strategy"] = [dict(r) for r in by_strategy]

            if total > 0:
                optional_count = conn.execute(
                    f"SELECT COUNT(*) FROM signal_engagement "
                    f"WHERE timestamp >= {cutoff} AND broker_mode = 'signals_only'"
                ).fetchone()[0] or 0
                base["broker_optional_pct"] = round(100.0 * optional_count / total, 1)
    except Exception:
        pass
    return base


def telemetry_summary(db_path: str | None = None) -> dict[str, Any]:
    """Diagnostic summary for feature-health. Provider-free. Safe on any error."""
    path = db_path or config.TELEMETRY_DB_PATH
    base: dict[str, Any] = {
        "enabled": bool(config.TELEMETRY_ENABLED),
        "total_endpoint_hits": 0,
        "total_feedback_rows": 0,
        "last_feedback_ticker": None,
        "last_feedback_at": None,
    }
    if not config.TELEMETRY_ENABLED:
        return base
    try:
        if not Path(path).exists():
            return base
        with _connect(path) as conn:
            hits = conn.execute("SELECT COUNT(*) FROM advisor_events").fetchone()[0]
            base["total_endpoint_hits"] = hits or 0
            fb = conn.execute("SELECT COUNT(*) FROM advisor_feedback").fetchone()[0]
            base["total_feedback_rows"] = fb or 0
            last = conn.execute(
                "SELECT ticker, submitted_at FROM advisor_feedback ORDER BY submitted_at DESC LIMIT 1"
            ).fetchone()
            if last:
                base["last_feedback_ticker"] = last["ticker"]
                base["last_feedback_at"] = last["submitted_at"]
        return base
    except Exception:
        return base
