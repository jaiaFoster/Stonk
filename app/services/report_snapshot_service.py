"""Persistent completed report snapshots. Failed runs never replace success."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import config


class ReportSnapshotRepository:
    SCHEMA_VERSION = 1

    def __init__(self, db_path: str | None = None, log_print=None):
        self.db_path = str(db_path or config.REPORT_SNAPSHOT_DB_PATH)
        self.log = log_print or (lambda message: None)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS report_snapshots (
                run_id TEXT PRIMARY KEY, mode TEXT, status TEXT, started_at TEXT, completed_at TEXT,
                payload_json TEXT, summary_json TEXT, data_coverage_json TEXT, provider_status_json TEXT,
                schema_version INTEGER, created_at TEXT)""")

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=5)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def save_success(self, run_id: str, mode: str, payload: str, summary: dict[str, Any], coverage: dict[str, Any], provider_status: dict[str, Any]) -> None:
        self._save(run_id, mode, "complete", payload, summary, coverage, provider_status)
        self.log(f"ReportSnapshot: saved successful run={run_id} schema={self.SCHEMA_VERSION}")
        self.log("ReportSnapshot: canonical snapshot updated")

    def save_degraded(self, run_id: str, mode: str, payload: str, summary: dict[str, Any], coverage: dict[str, Any], provider_status: dict[str, Any]) -> None:
        self._save(run_id, mode, "degraded", payload, summary, coverage, provider_status)
        self.log(f"ReportSnapshot: saved degraded run={run_id}; canonical complete snapshot preserved")
        self.log("ReportSnapshot: canonical snapshot preserved")

    def _save(self, run_id: str, mode: str, status: str, payload: str, summary: dict[str, Any], coverage: dict[str, Any], provider_status: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO report_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (run_id, mode, status, now, now, json.dumps(payload), json.dumps(summary, default=str),
                          json.dumps(coverage, default=str), json.dumps(provider_status, default=str), self.SCHEMA_VERSION, now))
            conn.execute(
                "DELETE FROM report_snapshots WHERE run_id IN (SELECT run_id FROM report_snapshots ORDER BY created_at DESC LIMIT -1 OFFSET ?)",
                (config.REPORT_SNAPSHOT_RETENTION_LIMIT,),
            )

    def record_failure(self, run_id: str, mode: str, summary: dict[str, Any] | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        previous = self.latest_success()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO report_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, mode, "failed", now, now, json.dumps(""), json.dumps(summary or {}, default=str), "{}", "{}", self.SCHEMA_VERSION, now),
            )
        self.log(f"ReportSnapshot: failed run preserved previous snapshot={(previous or {}).get('run_id', 'none')}")

    def latest_success(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM report_snapshots WHERE status='complete' AND schema_version=? ORDER BY completed_at DESC LIMIT 1",
                (self.SCHEMA_VERSION,),
            ).fetchone()
        result = dict(row) if row else None
        if result:
            self.log(f"ReportSnapshot: loaded latest successful run={result['run_id']}")
        return result

    def latest_degraded(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM report_snapshots WHERE status='degraded' AND schema_version=? ORDER BY completed_at DESC LIMIT 1",
                (self.SCHEMA_VERSION,),
            ).fetchone()
        return dict(row) if row else None
