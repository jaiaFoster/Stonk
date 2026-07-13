"""
ASA Patch 32A — Data Provenance Repository

Persists FieldProvenanceRecord instances to a dedicated SQLite table so that
provenance for any run, strategy, row, or field can be retrieved without
re-running the selection logic.

Table: data_provenance
Indexes:
  - idx_dp_run_id          on run_id
  - idx_dp_strategy_row    on (strategy_id, row_id)
  - idx_dp_ticker_field    on (ticker, field_id)
  - idx_dp_run_field       on (run_id, field_id)

All functions swallow errors and return empty / zero results — provenance
persistence is observability, not a correctness requirement.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS data_provenance (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT    NOT NULL,
    strategy_id      TEXT    NOT NULL,
    row_id           TEXT    NOT NULL,
    ticker           TEXT,
    field_id         TEXT    NOT NULL,
    selected_value   TEXT,
    selected_provider TEXT,
    confidence_level TEXT,
    provenance_json  TEXT,
    created_at       TEXT    DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_dp_run_id
    ON data_provenance (run_id);
CREATE INDEX IF NOT EXISTS idx_dp_strategy_row
    ON data_provenance (strategy_id, row_id);
CREATE INDEX IF NOT EXISTS idx_dp_ticker_field
    ON data_provenance (ticker, field_id);
CREATE INDEX IF NOT EXISTS idx_dp_run_field
    ON data_provenance (run_id, field_id);
"""

_DEFAULT_DB_PATH_ATTR = "DATA_PROVENANCE_DB_PATH"


@contextmanager
def _connect(db_path: str):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_mode=WAL")
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_schema(db_path: str) -> None:
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA)


def _db_path() -> str:
    return getattr(config, _DEFAULT_DB_PATH_ATTR, None) or str(
        Path(getattr(config, "STRATEGY_OBSERVATION_DB_PATH", "/tmp/asa.db")).parent
        / "data_provenance.db"
    )


def write_provenance(
    *,
    run_id: str,
    strategy_id: str,
    row_id: str,
    ticker: str | None,
    field_id: str,
    provenance_record: Any,  # FieldProvenanceRecord or dict
    db_path: str | None = None,
) -> bool:
    """Persist one provenance record. Returns True on success."""
    if not getattr(config, "DATA_CONFIDENCE_ENABLED", True):
        return False
    path = db_path or _db_path()
    try:
        _ensure_schema(path)
        if hasattr(provenance_record, "to_dict"):
            prov_dict = provenance_record.to_dict()
        else:
            prov_dict = dict(provenance_record or {})
        selected_value = prov_dict.get("selected_value")
        selected_provider = prov_dict.get("selected_provider")
        confidence_level = prov_dict.get("confidence_level")
        with _connect(path) as conn:
            conn.execute(
                """
                INSERT INTO data_provenance
                    (run_id, strategy_id, row_id, ticker, field_id,
                     selected_value, selected_provider, confidence_level, provenance_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, strategy_id, row_id,
                    str(ticker) if ticker else None,
                    field_id,
                    str(selected_value) if selected_value is not None else None,
                    str(selected_provider) if selected_provider else None,
                    str(confidence_level) if confidence_level else None,
                    json.dumps(prov_dict, default=str),
                ),
            )
        return True
    except Exception:
        return False


def write_provenance_batch(
    *,
    run_id: str,
    strategy_id: str,
    row_id: str,
    ticker: str | None,
    provenance_map: dict[str, Any],  # {field_id: FieldProvenanceRecord | dict}
    db_path: str | None = None,
) -> int:
    """Persist multiple provenance records for one row. Returns count written."""
    if not getattr(config, "DATA_CONFIDENCE_ENABLED", True):
        return 0
    written = 0
    for field_id, record in (provenance_map or {}).items():
        ok = write_provenance(
            run_id=run_id,
            strategy_id=strategy_id,
            row_id=row_id,
            ticker=ticker,
            field_id=field_id,
            provenance_record=record,
            db_path=db_path,
        )
        if ok:
            written += 1
    return written


def get_field_provenance(
    *,
    run_id: str | None = None,
    strategy_id: str | None = None,
    row_id: str | None = None,
    field_id: str | None = None,
    ticker: str | None = None,
    limit: int = 10,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Query provenance records. All filters are optional. Safe on any error."""
    path = db_path or _db_path()
    try:
        if not Path(path).exists():
            return []
        clauses: list[str] = []
        params: list[Any] = []
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if strategy_id:
            clauses.append("strategy_id = ?")
            params.append(strategy_id)
        if row_id:
            clauses.append("row_id = ?")
            params.append(row_id)
        if field_id:
            clauses.append("field_id = ?")
            params.append(field_id)
        if ticker:
            clauses.append("ticker = ?")
            params.append(ticker.upper().strip())
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        limit = min(int(limit or 10), 200)
        params.append(limit)
        with _connect(path) as conn:
            rows = conn.execute(
                f"""
                SELECT id, run_id, strategy_id, row_id, ticker, field_id,
                       selected_value, selected_provider, confidence_level,
                       provenance_json, created_at
                FROM data_provenance {where}
                ORDER BY created_at DESC LIMIT ?
                """,
                params,
            ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            try:
                d["provenance"] = json.loads(d.pop("provenance_json") or "{}")
            except Exception:
                d["provenance"] = {}
            results.append(d)
        return results
    except Exception:
        return []


def get_latest_field_provenance(
    *,
    run_id: str | None = None,
    strategy_id: str | None = None,
    row_id: str | None = None,
    field_id: str,
    db_path: str | None = None,
) -> dict[str, Any] | None:
    """Return most-recent provenance for one field. None if not found."""
    rows = get_field_provenance(
        run_id=run_id,
        strategy_id=strategy_id,
        row_id=row_id,
        field_id=field_id,
        db_path=db_path,
        limit=1,
    )
    return rows[0] if rows else None


def provenance_exists(
    run_id: str,
    strategy_id: str,
    row_id: str,
    db_path: str | None = None,
) -> bool:
    """Return True when at least one provenance record exists for this row."""
    path = db_path or _db_path()
    try:
        if not Path(path).exists():
            return False
        with _connect(path) as conn:
            row = conn.execute(
                "SELECT 1 FROM data_provenance WHERE run_id=? AND strategy_id=? AND row_id=? LIMIT 1",
                (run_id, strategy_id, row_id),
            ).fetchone()
        return row is not None
    except Exception:
        return False


def cleanup_old_provenance(retention_days: int, db_path: str | None = None) -> int:
    """Delete provenance records older than retention_days. Safe on any error."""
    path = db_path or _db_path()
    try:
        if not Path(path).exists():
            return 0
        with _connect(path) as conn:
            result = conn.execute(
                "DELETE FROM data_provenance WHERE created_at < datetime('now', ?)",
                (f"-{retention_days} days",),
            )
            return result.rowcount or 0
    except Exception:
        return 0


def write_provenance_batch_list(
    rows: list[dict[str, Any]],
    db_path: str | None = None,
) -> int:
    """Persist a flat list of provenance record dicts. Returns count written.

    Each dict must have: run_id, strategy_id, row_id, field_id.
    Optional: ticker, selected_value, selected_provider, confidence_level, provenance_json.
    """
    if not getattr(config, "DATA_CONFIDENCE_ENABLED", True):
        return 0
    if not rows:
        return 0
    path = db_path or _db_path()
    try:
        _ensure_schema(path)
        with _connect(path) as conn:
            conn.executemany(
                """
                INSERT INTO data_provenance
                    (run_id, strategy_id, row_id, ticker, field_id,
                     selected_value, selected_provider, confidence_level, provenance_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(r.get("run_id") or ""),
                        str(r.get("strategy_id") or ""),
                        str(r.get("row_id") or ""),
                        str(r["ticker"]).upper().strip() if r.get("ticker") else None,
                        str(r.get("field_id") or ""),
                        str(r["selected_value"]) if r.get("selected_value") is not None else None,
                        str(r["selected_provider"]) if r.get("selected_provider") else None,
                        str(r["confidence_level"]) if r.get("confidence_level") else None,
                        str(r["provenance_json"]) if r.get("provenance_json") else None,
                    )
                    for r in rows
                ],
            )
        return len(rows)
    except Exception:
        return 0


def get_field_provenance_batch(
    *,
    run_id: str | None = None,
    strategy_id: str | None = None,
    field_ids: list[str] | None = None,
    limit: int = 51,
    cursor: int | None = None,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Query multiple provenance records with optional cursor-based pagination.

    field_ids: list of field_id values to filter on (OR semantics).
    cursor: integer row ID; returns rows with id > cursor.
    """
    path = db_path or _db_path()
    try:
        if not Path(path).exists():
            return []
        clauses: list[str] = []
        params: list[Any] = []

        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if strategy_id:
            clauses.append("strategy_id = ?")
            params.append(strategy_id)
        if field_ids:
            placeholders = ", ".join("?" for _ in field_ids)
            clauses.append(f"field_id IN ({placeholders})")
            params.extend(field_ids)
        if cursor:
            clauses.append("id > ?")
            params.append(int(cursor))

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        clamped = max(1, min(int(limit or 51), 101))
        params.append(clamped)

        with _connect(path) as conn:
            db_rows = conn.execute(
                f"""
                SELECT id, run_id, strategy_id, row_id, ticker, field_id,
                       selected_value, selected_provider, confidence_level,
                       provenance_json, created_at
                FROM data_provenance {where}
                ORDER BY id ASC LIMIT ?
                """,
                params,
            ).fetchall()

        results = []
        for row in db_rows:
            d = dict(row)
            try:
                d["provenance"] = json.loads(d.pop("provenance_json") or "{}")
            except Exception:
                d["provenance"] = {}
            results.append(d)
        return results
    except Exception:
        return []
