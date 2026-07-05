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
CREATE TABLE IF NOT EXISTS public_demo_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    page TEXT NOT NULL,
    session_id TEXT,
    run_id TEXT,
    strategy_id TEXT,
    ticker TEXT,
    verdict TEXT,
    action TEXT,
    referrer_host TEXT,
    user_agent_family TEXT,
    ip_hash TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_public_demo_events_created_at
    ON public_demo_events(created_at);
CREATE INDEX IF NOT EXISTS idx_public_demo_events_session
    ON public_demo_events(session_id, created_at);
"""

_VALID_ACTIONS = {"bought", "watched", "ignored", "rejected"}
_VALID_OUTCOMES = {"positive", "negative", "neutral", "pending", "null"}
_VALID_PUBLIC_DEMO_EVENTS = {"page_view", "strategy_nav_click", "signal_card_click", "cta_click", "copy_link_click"}


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


def _salted_short_hash(value: str | None) -> str | None:
    if not value:
        return None
    salt = str(config.RUN_TOKEN or config.TELEMETRY_DB_PATH or "asa-demo")
    return hashlib.sha256(f"{salt}:{value}".encode()).hexdigest()[:16]


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


def record_public_demo_event(
    *,
    event_type: str,
    page: str,
    session_id: str | None = None,
    run_id: str | None = None,
    strategy_id: str | None = None,
    ticker: str | None = None,
    verdict: str | None = None,
    action: str | None = None,
    referrer_host: str | None = None,
    user_agent_family: str | None = None,
    ip: str | None = None,
    db_path: str | None = None,
) -> None:
    if not config.TELEMETRY_ENABLED or not getattr(config, "PUBLIC_DEMO_TELEMETRY_ENABLED", True):
        return
    safe_event = str(event_type or "").strip()
    if safe_event not in _VALID_PUBLIC_DEMO_EVENTS:
        return
    path = db_path or config.TELEMETRY_DB_PATH
    try:
        _ensure_schema(path)
        with _connect(path) as conn:
            conn.execute(
                "INSERT INTO public_demo_events "
                "(event_type, page, session_id, run_id, strategy_id, ticker, verdict, action, referrer_host, user_agent_family, ip_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    safe_event,
                    str(page or "/screener")[:64],
                    str(session_id)[:64] if session_id else None,
                    str(run_id)[:64] if run_id else None,
                    str(strategy_id)[:64] if strategy_id else None,
                    str(ticker).upper()[:20] if ticker else None,
                    str(verdict)[:64] if verdict else None,
                    str(action)[:64] if action else None,
                    str(referrer_host)[:128] if referrer_host else None,
                    str(user_agent_family)[:64] if user_agent_family else None,
                    _salted_short_hash(ip),
                ),
            )
    except Exception:
        pass


def public_demo_summary(days: int = 7, db_path: str | None = None) -> dict[str, Any]:
    path = db_path or config.TELEMETRY_DB_PATH
    base: dict[str, Any] = {
        "period_days": days,
        "total_events": 0,
        "page_views": 0,
        "unique_sessions": 0,
        "cta_clicks": 0,
        "strategy_nav_clicks": 0,
        "signal_card_clicks": 0,
        "copy_link_clicks": 0,
        "top_strategies": [],
        "top_tickers": [],
        "top_verdicts": [],
        "last_seen_at": None,
    }
    try:
        if not Path(path).exists():
            return base
        _ensure_schema(path)
        cutoff = f"datetime('now', '-{int(days)} days')"
        with _connect(path) as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS total, "
                f"SUM(CASE WHEN event_type='page_view' THEN 1 ELSE 0 END) AS page_views, "
                f"SUM(CASE WHEN event_type='cta_click' THEN 1 ELSE 0 END) AS cta_clicks, "
                f"SUM(CASE WHEN event_type='strategy_nav_click' THEN 1 ELSE 0 END) AS nav_clicks, "
                f"SUM(CASE WHEN event_type='signal_card_click' THEN 1 ELSE 0 END) AS signal_clicks, "
                f"SUM(CASE WHEN event_type='copy_link_click' THEN 1 ELSE 0 END) AS copy_clicks, "
                f"COUNT(DISTINCT session_id) AS unique_sessions, "
                f"MAX(created_at) AS last_seen_at "
                f"FROM public_demo_events WHERE created_at >= {cutoff}"
            ).fetchone()
            if row:
                base.update({
                    "total_events": row["total"] or 0,
                    "page_views": row["page_views"] or 0,
                    "unique_sessions": row["unique_sessions"] or 0,
                    "cta_clicks": row["cta_clicks"] or 0,
                    "strategy_nav_clicks": row["nav_clicks"] or 0,
                    "signal_card_clicks": row["signal_clicks"] or 0,
                    "copy_link_clicks": row["copy_clicks"] or 0,
                    "last_seen_at": row["last_seen_at"],
                })
            base["top_strategies"] = [
                dict(r) for r in conn.execute(
                    f"SELECT strategy_id, COUNT(*) AS count FROM public_demo_events "
                    f"WHERE created_at >= {cutoff} AND strategy_id IS NOT NULL "
                    f"GROUP BY strategy_id ORDER BY count DESC LIMIT 10"
                ).fetchall()
            ]
            base["top_tickers"] = [
                dict(r) for r in conn.execute(
                    f"SELECT ticker, COUNT(*) AS count FROM public_demo_events "
                    f"WHERE created_at >= {cutoff} AND ticker IS NOT NULL "
                    f"GROUP BY ticker ORDER BY count DESC LIMIT 10"
                ).fetchall()
            ]
            base["top_verdicts"] = [
                dict(r) for r in conn.execute(
                    f"SELECT verdict, COUNT(*) AS count FROM public_demo_events "
                    f"WHERE created_at >= {cutoff} AND verdict IS NOT NULL "
                    f"GROUP BY verdict ORDER BY count DESC LIMIT 10"
                ).fetchall()
            ]
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
