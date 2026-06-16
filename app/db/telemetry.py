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
