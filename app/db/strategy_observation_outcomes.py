"""30C — Outcome tracking foundation for strategy observations.

Provides the schema and read/write path for attaching future outcomes
to existing strategy_observations rows. No outcome computation is
performed here — 30D will handle that.

Shares the same SQLite file as strategy_observations (configurable).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app import config

OUTCOME_SCHEMA_VERSION = "30C.v1"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS strategy_observation_outcomes (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id                  INTEGER,
    observation_key                 TEXT    NOT NULL,
    strategy_id                     TEXT    NOT NULL,
    ticker                          TEXT    NOT NULL,
    run_id                          TEXT,
    outcome_type                    TEXT    NOT NULL DEFAULT 'not_available',
    outcome_horizon_days            INTEGER,
    outcome_status                  TEXT,
    baseline_price                  REAL,
    outcome_price                   REAL,
    price_return_pct                REAL,
    max_favorable_excursion_pct     REAL,
    max_adverse_excursion_pct       REAL,
    option_mid_at_observation       REAL,
    option_mid_at_outcome           REAL,
    option_return_pct               REAL,
    notes                           TEXT,
    created_at                      TEXT    DEFAULT (datetime('now')),
    updated_at                      TEXT    DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_outcomes_obs_key
    ON strategy_observation_outcomes (observation_key, strategy_id);
CREATE INDEX IF NOT EXISTS idx_outcomes_ticker
    ON strategy_observation_outcomes (ticker, strategy_id);
CREATE INDEX IF NOT EXISTS idx_outcomes_run
    ON strategy_observation_outcomes (run_id, strategy_id);
"""

# Valid outcome_type values (informational — not enforced at DB level).
OUTCOME_TYPES = frozenset({
    "stock_forward_return",
    "option_structure_mid_return",
    "calendar_lifecycle_return",
    "manual_review",
    "not_available",
})


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


def ensure_outcome_schema(db_path: str | None = None) -> None:
    """Create the outcomes table if it does not exist. Safe to call repeatedly."""
    path = db_path or config.STRATEGY_OBSERVATION_DB_PATH
    with _connect(path) as conn:
        conn.executescript(_SCHEMA)


def write_outcome(outcome: dict[str, Any], db_path: str | None = None) -> int:
    """Insert one outcome record. Returns 1 on success, 0 on any error."""
    path = db_path or config.STRATEGY_OBSERVATION_DB_PATH
    _COLS = (
        "observation_id", "observation_key", "strategy_id", "ticker", "run_id",
        "outcome_type", "outcome_horizon_days", "outcome_status",
        "baseline_price", "outcome_price", "price_return_pct",
        "max_favorable_excursion_pct", "max_adverse_excursion_pct",
        "option_mid_at_observation", "option_mid_at_outcome", "option_return_pct",
        "notes",
    )
    try:
        ensure_outcome_schema(path)
        row = {c: outcome.get(c) for c in _COLS}
        if not row.get("observation_key") or not row.get("strategy_id") or not row.get("ticker"):
            return 0
        cols = ", ".join(_COLS)
        placeholders = ", ".join(f":{c}" for c in _COLS)
        with _connect(path) as conn:
            conn.execute(
                f"INSERT INTO strategy_observation_outcomes ({cols}) VALUES ({placeholders})",
                row,
            )
        return 1
    except Exception:
        return 0


def read_outcomes(
    *,
    observation_key: str | None = None,
    strategy_id: str | None = None,
    ticker: str | None = None,
    run_id: str | None = None,
    limit: int = 100,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Read outcome records. Returns [] if table does not yet exist."""
    path = db_path or config.STRATEGY_OBSERVATION_DB_PATH
    try:
        if not Path(path).exists():
            return []
        clauses: list[str] = []
        params: list[Any] = []
        if observation_key:
            clauses.append("observation_key = ?")
            params.append(observation_key)
        if strategy_id:
            clauses.append("strategy_id = ?")
            params.append(strategy_id)
        if ticker:
            clauses.append("ticker = ?")
            params.append(ticker.upper().strip())
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(min(int(limit or 100), 500))
        with _connect(path) as conn:
            # Table may not exist yet — return [] gracefully.
            try:
                rows = conn.execute(
                    f"SELECT * FROM strategy_observation_outcomes {where}"
                    f" ORDER BY created_at DESC LIMIT ?",
                    params,
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        return [dict(r) for r in rows]
    except Exception:
        return []


def outcome_schema_exists(db_path: str | None = None) -> bool:
    """Return True if the outcomes table exists in the DB."""
    path = db_path or config.STRATEGY_OBSERVATION_DB_PATH
    try:
        if not Path(path).exists():
            return False
        with _connect(path) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
                " AND name='strategy_observation_outcomes'"
            ).fetchone()
        return row is not None
    except Exception:
        return False
