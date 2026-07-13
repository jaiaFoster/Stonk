"""
ASA Patch 32B — Data Confidence Run Reports Repository

Persists the automated data validation suite results (from
run_validation_suite) once per run to SQLite so that historical
confidence trends can be queried without re-running the pipeline.

Table: data_confidence_run_reports
Schema version: 32B.v1

All functions swallow errors and return safe defaults — this table
is observability, not a correctness dependency.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS data_confidence_run_reports (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT    NOT NULL UNIQUE,
    strategy_id      TEXT    NOT NULL,
    total_reports    INTEGER DEFAULT 0,
    passed_reports   INTEGER DEFAULT 0,
    failed_reports   INTEGER DEFAULT 0,
    total_errors     INTEGER DEFAULT 0,
    total_warnings   INTEGER DEFAULT 0,
    validation_passed INTEGER DEFAULT 0,
    report_json      TEXT,
    schema_version   TEXT    DEFAULT '32B.v1',
    created_at       TEXT    DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_dcr_run_id
    ON data_confidence_run_reports (run_id);
CREATE INDEX IF NOT EXISTS idx_dcr_created_at
    ON data_confidence_run_reports (created_at);
"""

_DEFAULT_DB_ATTR = "DATA_PROVENANCE_DB_PATH"


def _db_path() -> str:
    path = getattr(config, _DEFAULT_DB_ATTR, None) or ""
    if path:
        # Same DB as provenance; derive sibling path
        p = Path(path)
        return str(p.parent / "data_confidence_run_reports.db")
    # Fallback: derive from strategy row DB
    base = getattr(config, "STRATEGY_ROW_DB_PATH", None) or ""
    if base:
        return str(Path(base).parent / "data_confidence_run_reports.db")
    return "/tmp/data_confidence_run_reports.db"


@contextmanager
def _connect():
    db_path = _db_path()
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


def write_run_report(
    run_id: str,
    strategy_id: str,
    suite_result: dict[str, Any],
) -> bool:
    """Persist a validation suite result for one run. Returns True on success."""
    try:
        with _connect() as conn:
            report_json = json.dumps(suite_result, default=str)
            conn.execute(
                """
                INSERT OR REPLACE INTO data_confidence_run_reports
                  (run_id, strategy_id, total_reports, passed_reports, failed_reports,
                   total_errors, total_warnings, validation_passed, report_json, schema_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '32B.v1')
                """,
                (
                    str(run_id),
                    str(strategy_id),
                    int(suite_result.get("total_reports") or 0),
                    int(suite_result.get("passed_reports") or 0),
                    int(suite_result.get("failed_reports") or 0),
                    int(suite_result.get("total_errors") or 0),
                    int(suite_result.get("total_warnings") or 0),
                    1 if suite_result.get("validation_passed") else 0,
                    report_json,
                ),
            )
            conn.commit()
            return True
    except Exception:
        return False


def get_run_report(run_id: str) -> dict[str, Any] | None:
    """Retrieve the validation suite result for a specific run_id."""
    try:
        with _connect() as conn:
            row = conn.execute(
                """
                SELECT run_id, strategy_id, total_reports, passed_reports, failed_reports,
                       total_errors, total_warnings, validation_passed, report_json,
                       schema_version, created_at
                FROM data_confidence_run_reports
                WHERE run_id = ?
                LIMIT 1
                """,
                (str(run_id),),
            ).fetchone()
            if not row:
                return None
            return _row_to_dict(row)
    except Exception:
        return None


def get_latest_run_reports(limit: int = 10) -> list[dict[str, Any]]:
    """Return the most recent run reports, newest first."""
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT run_id, strategy_id, total_reports, passed_reports, failed_reports,
                       total_errors, total_warnings, validation_passed, report_json,
                       schema_version, created_at
                FROM data_confidence_run_reports
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (max(1, min(limit, 100)),),
            ).fetchall()
            return [_row_to_dict(r) for r in rows]
    except Exception:
        return []


def _row_to_dict(row: tuple) -> dict[str, Any]:
    (run_id, strategy_id, total_reports, passed_reports, failed_reports,
     total_errors, total_warnings, validation_passed, report_json,
     schema_version, created_at) = row
    result: dict[str, Any] = {
        "run_id": run_id,
        "strategy_id": strategy_id,
        "total_reports": total_reports,
        "passed_reports": passed_reports,
        "failed_reports": failed_reports,
        "total_errors": total_errors,
        "total_warnings": total_warnings,
        "validation_passed": bool(validation_passed),
        "schema_version": schema_version,
        "created_at": created_at,
    }
    if report_json:
        try:
            result["report"] = json.loads(report_json)
        except Exception:
            result["report"] = None
    return result
