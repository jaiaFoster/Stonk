"""Read-only SQLite size, row-count, and dry-run pruning diagnostics."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from app import config

TABLES = (
    "market_data_records", "market_data_fetch_log", "provider_errors", "data_coverage_runs",
    "equity_quotes", "equity_daily_candles", "option_chain_snapshots", "earnings_events", "derived_metrics",
)


def build_storage_profile(db_path: str | None = None) -> dict[str, Any]:
    path = str(db_path or config.MARKET_DATA_DB_PATH)
    profile: dict[str, Any] = {"database_path": path, "database_size_bytes": os.path.getsize(path) if os.path.exists(path) else 0, "table_rows": {}, "pruning_dry_run": {}}
    if not os.path.exists(path):
        return profile
    try:
        with sqlite3.connect(path, timeout=5) as conn:
            existing = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in TABLES:
                if table in existing:
                    profile["table_rows"][table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            profile["pruning_dry_run"] = _pruning_counts(conn, existing)
    except sqlite3.DatabaseError as exc:
        profile["error"] = str(exc)
    return profile


def _pruning_counts(conn: sqlite3.Connection, existing: set[str]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    rules = {
        "market_data_fetch_log": ("created_at", config.MARKET_DATA_FETCH_LOG_RETENTION_DAYS),
        "data_coverage_runs": ("created_at", config.MARKET_DATA_COVERAGE_RETENTION_DAYS),
        "option_chain_snapshots": ("fetched_at", config.OPTION_CHAIN_SNAPSHOT_RETENTION_DAYS),
    }
    output = {"mode": "dry_run", "would_prune": {}}
    for table, (column, days) in rules.items():
        if table in existing:
            cutoff = (now - timedelta(days=days)).isoformat()
            output["would_prune"][table] = int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} < ?", (cutoff,)).fetchone()[0])
    return output
