"""SQLite-backed vault — persists advisor snapshot payloads across runs.

Enabled only when VAULT_ENABLED=true. All writes are fire-and-forget and must
never raise into the caller. All reads return safe defaults on any failure.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS vault_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL UNIQUE,
    run_date TEXT NOT NULL,
    run_quality TEXT,
    schema_version INTEGER DEFAULT 1,
    payload_json TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS vault_metadata (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);
"""


@contextmanager
def _connect(db_path: str):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SCHEMA)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _db_path() -> str:
    return str(getattr(config, "VAULT_DB_PATH", "data/vault.db") or "data/vault.db")


def _max_entries() -> int:
    return int(getattr(config, "VAULT_MAX_ENTRIES", 30) or 30)


def _schema_version() -> int:
    return int(getattr(config, "VAULT_SCHEMA_VERSION", 1) or 1)


def write_snapshot(run_id: str, run_date: str, run_quality: str | None, payload: dict[str, Any]) -> bool:
    """Persist a snapshot payload. Returns True on success, False on any error."""
    if not getattr(config, "VAULT_ENABLED", False):
        return False
    try:
        with _connect(_db_path()) as conn:
            conn.execute(
                """
                INSERT INTO vault_snapshots (run_id, run_date, run_quality, schema_version, payload_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    run_quality = excluded.run_quality,
                    payload_json = excluded.payload_json,
                    created_at = datetime('now')
                """,
                (run_id, run_date, run_quality, _schema_version(), json.dumps(payload)),
            )
            _prune(conn)
        return True
    except Exception:
        return False


def _prune(conn: sqlite3.Connection) -> None:
    max_entries = _max_entries()
    conn.execute(
        """
        DELETE FROM vault_snapshots
        WHERE id NOT IN (
            SELECT id FROM vault_snapshots ORDER BY created_at DESC LIMIT ?
        )
        """,
        (max_entries,),
    )


def latest_snapshot() -> dict[str, Any] | None:
    """Return the most recent vault snapshot payload, or None."""
    if not getattr(config, "VAULT_ENABLED", False):
        return None
    try:
        with _connect(_db_path()) as conn:
            row = conn.execute(
                "SELECT * FROM vault_snapshots ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            return {
                "run_id": row["run_id"],
                "run_date": row["run_date"],
                "run_quality": row["run_quality"],
                "schema_version": row["schema_version"],
                "created_at": row["created_at"],
                "payload": json.loads(row["payload_json"]),
            }
    except Exception:
        return None


def vault_status() -> dict[str, Any]:
    """Return a status summary for the /vault/status endpoint."""
    enabled = bool(getattr(config, "VAULT_ENABLED", False))
    if not enabled:
        return {
            "enabled": False,
            "entry_count": 0,
            "latest_run_id": None,
            "latest_run_date": None,
            "latest_created_at": None,
            "max_entries": _max_entries(),
            "schema_version": _schema_version(),
            "db_path": _db_path(),
        }
    try:
        with _connect(_db_path()) as conn:
            count_row = conn.execute("SELECT COUNT(*) AS cnt FROM vault_snapshots").fetchone()
            latest_row = conn.execute(
                "SELECT run_id, run_date, created_at FROM vault_snapshots ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            return {
                "enabled": True,
                "entry_count": count_row["cnt"] if count_row else 0,
                "latest_run_id": latest_row["run_id"] if latest_row else None,
                "latest_run_date": latest_row["run_date"] if latest_row else None,
                "latest_created_at": latest_row["created_at"] if latest_row else None,
                "max_entries": _max_entries(),
                "schema_version": _schema_version(),
                "db_path": _db_path(),
            }
    except Exception as exc:
        return {
            "enabled": True,
            "entry_count": None,
            "latest_run_id": None,
            "latest_run_date": None,
            "latest_created_at": None,
            "max_entries": _max_entries(),
            "schema_version": _schema_version(),
            "db_path": _db_path(),
            "error": str(exc),
        }
