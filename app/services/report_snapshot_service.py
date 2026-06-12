"""Persistent completed report snapshots. Failed runs never replace success."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import config


class ReportSnapshotRepository:
    SCHEMA_VERSION = 1

    def __init__(self, db_path: str | None = None):
        self.db_path = str(db_path or config.REPORT_SNAPSHOT_DB_PATH)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS report_snapshots (
                run_id TEXT PRIMARY KEY, mode TEXT, status TEXT, started_at TEXT, completed_at TEXT,
                payload_json TEXT, summary_json TEXT, data_coverage_json TEXT, provider_status_json TEXT,
                schema_version INTEGER, created_at TEXT)""")

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def save_success(self, run_id: str, mode: str, payload: str, summary: dict[str, Any], coverage: dict[str, Any], provider_status: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO report_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (run_id, mode, "complete", now, now, json.dumps(payload), json.dumps(summary, default=str),
                          json.dumps(coverage, default=str), json.dumps(provider_status, default=str), self.SCHEMA_VERSION, now))

    def latest_success(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM report_snapshots WHERE status='complete' ORDER BY completed_at DESC LIMIT 1").fetchone()
        return dict(row) if row else None
