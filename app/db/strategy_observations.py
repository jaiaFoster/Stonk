"""Universal strategy observation journal — SQLite persistence (ASA 30B).

One row per normalized strategy candidate per run, across all four strategies.
Follows the same pattern as app/db/ff_journal.py (connect, ensure_schema,
write_run, read functions). The legacy FF journal is preserved separately.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app import config

OBSERVATION_SCHEMA_VERSION = "30B.v1"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS strategy_observations (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                      TEXT    NOT NULL,
    observed_at                 TEXT    DEFAULT (datetime('now')),
    run_date                    TEXT    NOT NULL,
    strategy_id                 TEXT    NOT NULL,
    strategy_name               TEXT,
    strategy_family             TEXT,
    strategy_row_schema_version TEXT,
    observation_schema_version  TEXT    DEFAULT '30B.v1',
    ticker                      TEXT    NOT NULL,
    underlying_symbol           TEXT,
    candidate_type              TEXT,
    structure_type              TEXT,
    timeframe                   TEXT,
    verdict                     TEXT,
    friendly_verdict            TEXT,
    primary_reason              TEXT,
    status_bucket               TEXT,
    daily_opportunity_eligible  INTEGER,
    can_trade_live              INTEGER,
    dry_run                     INTEGER,
    journal_eligible            INTEGER,
    data_quality_status         TEXT,
    gate_pass_count             INTEGER DEFAULT 0,
    gate_watch_count            INTEGER DEFAULT 0,
    gate_fail_count             INTEGER DEFAULT 0,
    gate_unknown_count          INTEGER DEFAULT 0,
    gate_skipped_count          INTEGER DEFAULT 0,
    blocking_gate_count         INTEGER DEFAULT 0,
    score                       REAL,
    actionability_score         REAL,
    observation_key             TEXT,
    row_hash                    TEXT,
    metrics_json                TEXT,
    gates_json                  TEXT,
    risk_flags_json             TEXT,
    reasons_json                TEXT,
    structure_json              TEXT,
    data_quality_json           TEXT,
    observation_refs_json       TEXT,
    source_summary_json         TEXT,
    created_at                  TEXT    DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_strategy_obs_dedup
    ON strategy_observations (run_id, strategy_id, observation_key, row_hash);
CREATE INDEX IF NOT EXISTS idx_strategy_obs_run
    ON strategy_observations (run_id, strategy_id);
CREATE INDEX IF NOT EXISTS idx_strategy_obs_ticker_date
    ON strategy_observations (ticker, run_date);
CREATE INDEX IF NOT EXISTS idx_strategy_obs_bucket_date
    ON strategy_observations (status_bucket, run_date);
"""

_EXTRA_COLUMNS: dict[str, str] = {}


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
        if _EXTRA_COLUMNS:
            columns = {row["name"] for row in conn.execute(
                "PRAGMA table_info(strategy_observations)"
            ).fetchall()}
            for name, sql_type in _EXTRA_COLUMNS.items():
                if name not in columns:
                    conn.execute(
                        f"ALTER TABLE strategy_observations ADD COLUMN {name} {sql_type}"
                    )


def write_run(
    run_id: str,
    run_date: str,
    observations: list[dict[str, Any]],
    db_path: str | None = None,
) -> int:
    """Persist observations. Returns rows written. Swallows all errors."""
    if not config.STRATEGY_OBSERVATION_JOURNAL_ENABLED:
        return 0
    path = db_path or config.STRATEGY_OBSERVATION_DB_PATH
    if not observations:
        return 0
    try:
        _ensure_schema(path)
        cap = config.STRATEGY_OBSERVATION_MAX_ROWS_PER_RUN
        batch = observations[:cap]
        with _connect(path) as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO strategy_observations (
                    run_id, observed_at, run_date,
                    strategy_id, strategy_name, strategy_family,
                    strategy_row_schema_version, observation_schema_version,
                    ticker, underlying_symbol, candidate_type, structure_type, timeframe,
                    verdict, friendly_verdict, primary_reason, status_bucket,
                    daily_opportunity_eligible, can_trade_live, dry_run, journal_eligible,
                    data_quality_status,
                    gate_pass_count, gate_watch_count, gate_fail_count,
                    gate_unknown_count, gate_skipped_count, blocking_gate_count,
                    score, actionability_score,
                    observation_key, row_hash,
                    metrics_json, gates_json, risk_flags_json, reasons_json,
                    structure_json, data_quality_json, observation_refs_json, source_summary_json
                ) VALUES (
                    :run_id, :observed_at, :run_date,
                    :strategy_id, :strategy_name, :strategy_family,
                    :strategy_row_schema_version, :observation_schema_version,
                    :ticker, :underlying_symbol, :candidate_type, :structure_type, :timeframe,
                    :verdict, :friendly_verdict, :primary_reason, :status_bucket,
                    :daily_opportunity_eligible, :can_trade_live, :dry_run, :journal_eligible,
                    :data_quality_status,
                    :gate_pass_count, :gate_watch_count, :gate_fail_count,
                    :gate_unknown_count, :gate_skipped_count, :blocking_gate_count,
                    :score, :actionability_score,
                    :observation_key, :row_hash,
                    :metrics_json, :gates_json, :risk_flags_json, :reasons_json,
                    :structure_json, :data_quality_json, :observation_refs_json, :source_summary_json
                )
                """,
                batch,
            )
        return len(batch)
    except Exception:
        return 0


def read_observations(
    *,
    run_id: str | None = None,
    strategy_id: str | None = None,
    ticker: str | None = None,
    status_bucket: str | None = None,
    verdict: str | None = None,
    days: int | None = None,
    limit: int = 100,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Return compact observation rows matching filters. Safe on any error."""
    path = db_path or config.STRATEGY_OBSERVATION_DB_PATH
    if not config.STRATEGY_OBSERVATION_JOURNAL_ENABLED:
        return []
    try:
        if not Path(path).exists():
            return []
        limit = min(int(limit or 100), 500)
        clauses: list[str] = []
        params: list[Any] = []
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if strategy_id:
            clauses.append("strategy_id = ?")
            params.append(strategy_id)
        if ticker:
            clauses.append("ticker = ?")
            params.append(ticker.upper().strip())
        if status_bucket:
            clauses.append("status_bucket = ?")
            params.append(status_bucket)
        if verdict:
            clauses.append("verdict LIKE ?")
            params.append(f"%{verdict}%")
        if days:
            clauses.append("run_date >= date('now', ?)")
            params.append(f"-{days} days")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with _connect(path) as conn:
            rows = conn.execute(
                f"""
                SELECT id, run_id, run_date, strategy_id, strategy_name, strategy_family,
                       ticker, verdict, friendly_verdict, primary_reason, status_bucket,
                       daily_opportunity_eligible, can_trade_live, dry_run,
                       data_quality_status, gate_pass_count, gate_fail_count, blocking_gate_count,
                       score, observation_key, row_hash, created_at
                FROM strategy_observations {where}
                ORDER BY created_at DESC LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def read_observations_full(
    *,
    run_id: str | None = None,
    strategy_id: str | None = None,
    limit: int = 50,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Return full observation rows including JSON columns."""
    path = db_path or config.STRATEGY_OBSERVATION_DB_PATH
    if not config.STRATEGY_OBSERVATION_JOURNAL_ENABLED:
        return []
    try:
        if not Path(path).exists():
            return []
        limit = min(int(limit or 50), 200)
        clauses: list[str] = []
        params: list[Any] = []
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if strategy_id:
            clauses.append("strategy_id = ?")
            params.append(strategy_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with _connect(path) as conn:
            rows = conn.execute(
                f"SELECT * FROM strategy_observations {where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def run_summary(run_id: str, db_path: str | None = None) -> dict[str, Any]:
    """Compact counts for a single run. Safe on any error."""
    path = db_path or config.STRATEGY_OBSERVATION_DB_PATH
    base: dict[str, Any] = {
        "run_id": run_id,
        "total_observations": 0,
        "by_strategy": {},
        "by_status_bucket": {},
        "daily_opportunity_eligible_count": 0,
        "can_trade_live_count": 0,
        "dry_run_count": 0,
        "blocking_gate_count": 0,
    }
    if not config.STRATEGY_OBSERVATION_JOURNAL_ENABLED:
        return base
    try:
        if not Path(path).exists():
            return base
        with _connect(path) as conn:
            rows = conn.execute(
                """
                SELECT strategy_id, status_bucket,
                       COUNT(*) AS cnt,
                       SUM(daily_opportunity_eligible) AS do_cnt,
                       SUM(can_trade_live) AS live_cnt,
                       SUM(dry_run) AS dr_cnt,
                       SUM(blocking_gate_count) AS block_cnt
                FROM strategy_observations WHERE run_id=?
                GROUP BY strategy_id, status_bucket
                """,
                (run_id,),
            ).fetchall()
        total = 0
        by_strat: dict[str, dict[str, Any]] = {}
        by_bucket: dict[str, int] = {}
        do_total = can_live = dry = block = 0
        for row in rows:
            sid = row["strategy_id"]
            bucket = row["status_bucket"] or "unknown"
            cnt = row["cnt"] or 0
            total += cnt
            by_bucket[bucket] = by_bucket.get(bucket, 0) + cnt
            if sid not in by_strat:
                by_strat[sid] = {"total": 0}
            by_strat[sid]["total"] = by_strat[sid].get("total", 0) + cnt
            by_strat[sid][bucket] = by_strat[sid].get(bucket, 0) + cnt
            do_total += row["do_cnt"] or 0
            can_live += row["live_cnt"] or 0
            dry += row["dr_cnt"] or 0
            block += row["block_cnt"] or 0
        base.update({
            "total_observations": total,
            "by_strategy": by_strat,
            "by_status_bucket": by_bucket,
            "daily_opportunity_eligible_count": do_total,
            "can_trade_live_count": can_live,
            "dry_run_count": dry,
            "blocking_gate_count": block,
        })
        return base
    except Exception:
        return base


def global_summary(days: int = 7, db_path: str | None = None) -> dict[str, Any]:
    """Rolling summary across the last N days. Safe on any error."""
    path = db_path or config.STRATEGY_OBSERVATION_DB_PATH
    base: dict[str, Any] = {
        "enabled": bool(config.STRATEGY_OBSERVATION_JOURNAL_ENABLED),
        "days": days,
        "total_observations": 0,
        "runs_recorded": 0,
        "tickers_observed": 0,
        "by_strategy": {},
        "by_status_bucket": {},
        "daily_opportunity_eligible_count": 0,
        "can_trade_live_count": 0,
        "dry_run_count": 0,
    }
    if not config.STRATEGY_OBSERVATION_JOURNAL_ENABLED:
        return base
    try:
        if not Path(path).exists():
            return base
        with _connect(path) as conn:
            totals = conn.execute(
                """
                SELECT COUNT(*) AS total,
                       COUNT(DISTINCT run_id) AS runs,
                       COUNT(DISTINCT ticker) AS tickers,
                       SUM(daily_opportunity_eligible) AS do_cnt,
                       SUM(can_trade_live) AS live_cnt,
                       SUM(dry_run) AS dry_cnt
                FROM strategy_observations
                WHERE run_date >= date('now', ?)
                """,
                (f"-{days} days",),
            ).fetchone()
            if totals:
                base.update({
                    "total_observations": totals["total"] or 0,
                    "runs_recorded": totals["runs"] or 0,
                    "tickers_observed": totals["tickers"] or 0,
                    "daily_opportunity_eligible_count": totals["do_cnt"] or 0,
                    "can_trade_live_count": totals["live_cnt"] or 0,
                    "dry_run_count": totals["dry_cnt"] or 0,
                })
            strat_rows = conn.execute(
                """
                SELECT strategy_id, status_bucket, COUNT(*) AS cnt
                FROM strategy_observations
                WHERE run_date >= date('now', ?)
                GROUP BY strategy_id, status_bucket
                """,
                (f"-{days} days",),
            ).fetchall()
            by_strat: dict[str, dict[str, Any]] = {}
            by_bucket: dict[str, int] = {}
            for row in strat_rows:
                sid = row["strategy_id"]
                bucket = row["status_bucket"] or "unknown"
                cnt = row["cnt"] or 0
                by_bucket[bucket] = by_bucket.get(bucket, 0) + cnt
                if sid not in by_strat:
                    by_strat[sid] = {"total": 0}
                by_strat[sid]["total"] = by_strat[sid].get("total", 0) + cnt
                by_strat[sid][bucket] = by_strat[sid].get(bucket, 0) + cnt
            base["by_strategy"] = by_strat
            base["by_status_bucket"] = by_bucket
        return base
    except Exception:
        return base


def cleanup_old_observations(retention_days: int, db_path: str | None = None) -> int:
    """Delete observations older than retention_days. Returns rows deleted. Safe on any error."""
    path = db_path or config.STRATEGY_OBSERVATION_DB_PATH
    if not config.STRATEGY_OBSERVATION_JOURNAL_ENABLED:
        return 0
    try:
        if not Path(path).exists():
            return 0
        with _connect(path) as conn:
            result = conn.execute(
                "DELETE FROM strategy_observations WHERE run_date < date('now', ?)",
                (f"-{retention_days} days",),
            )
            return result.rowcount or 0
    except Exception:
        return 0


# ─── 30C review query helpers ──────────────────────────────────────────────────


def query_for_review(
    *,
    days: int | None = None,
    strategy_id: str | None = None,
    run_id: str | None = None,
    blocking_only: bool = False,
    limit: int = 200,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Return recent observations including gates_json for review analysis."""
    path = db_path or config.STRATEGY_OBSERVATION_DB_PATH
    if not config.STRATEGY_OBSERVATION_JOURNAL_ENABLED:
        return []
    try:
        if not Path(path).exists():
            return []
        limit = min(int(limit or 200), 500)
        clauses: list[str] = []
        params: list[Any] = []
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if strategy_id:
            clauses.append("strategy_id = ?")
            params.append(strategy_id)
        if days:
            clauses.append("run_date >= date('now', ?)")
            params.append(f"-{days} days")
        if blocking_only:
            clauses.append("blocking_gate_count > 0")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with _connect(path) as conn:
            rows = conn.execute(
                f"""
                SELECT id, run_id, run_date, strategy_id, strategy_name,
                       ticker, verdict, friendly_verdict, primary_reason,
                       status_bucket, daily_opportunity_eligible, can_trade_live, dry_run,
                       data_quality_status, gate_pass_count, gate_fail_count,
                       gate_watch_count, gate_unknown_count, gate_skipped_count,
                       blocking_gate_count, score, observation_key, row_hash,
                       gates_json, created_at
                FROM strategy_observations {where}
                ORDER BY created_at DESC LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def query_ticker_stats(
    days: int = 7,
    limit: int = 50,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """SQL GROUP BY ticker across last N days for recurrence analysis."""
    path = db_path or config.STRATEGY_OBSERVATION_DB_PATH
    if not config.STRATEGY_OBSERVATION_JOURNAL_ENABLED:
        return []
    try:
        if not Path(path).exists():
            return []
        limit = min(int(limit or 50), 250)
        with _connect(path) as conn:
            rows = conn.execute(
                """
                SELECT ticker,
                       COUNT(*) AS obs_count,
                       COUNT(DISTINCT run_id) AS run_count,
                       COUNT(DISTINCT strategy_id) AS strategy_count,
                       SUM(CASE WHEN status_bucket='pass' THEN 1 ELSE 0 END) AS pass_count,
                       SUM(CASE WHEN status_bucket='watch' THEN 1 ELSE 0 END) AS watch_count,
                       SUM(CASE WHEN status_bucket='fail' THEN 1 ELSE 0 END) AS fail_count,
                       SUM(CASE WHEN status_bucket='skipped' THEN 1 ELSE 0 END) AS skipped_count,
                       SUM(CASE WHEN status_bucket='dry_run' THEN 1 ELSE 0 END) AS dry_run_count,
                       GROUP_CONCAT(DISTINCT strategy_id) AS strategy_ids_csv,
                       GROUP_CONCAT(DISTINCT status_bucket) AS buckets_csv,
                       MIN(run_date) AS first_seen,
                       MAX(run_date) AS latest_seen,
                       MAX(primary_reason) AS sample_primary_reason
                FROM strategy_observations
                WHERE run_date >= date('now', ?)
                GROUP BY ticker
                ORDER BY obs_count DESC, pass_count DESC
                LIMIT ?
                """,
                (f"-{days} days", limit),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def query_two_latest_runs(db_path: str | None = None) -> list[str]:
    """Return up to 2 most recent distinct run_ids ordered newest-first."""
    path = db_path or config.STRATEGY_OBSERVATION_DB_PATH
    if not config.STRATEGY_OBSERVATION_JOURNAL_ENABLED:
        return []
    try:
        if not Path(path).exists():
            return []
        with _connect(path) as conn:
            rows = conn.execute(
                "SELECT DISTINCT run_id FROM strategy_observations"
                " ORDER BY created_at DESC LIMIT 2"
            ).fetchall()
        return [row["run_id"] for row in rows]
    except Exception:
        return []


def query_primary_reason_stats(
    days: int = 7,
    strategy_id: str | None = None,
    limit: int = 100,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Return (strategy_id, primary_reason, cnt, pass_cnt, issue_cnt) rows."""
    path = db_path or config.STRATEGY_OBSERVATION_DB_PATH
    if not config.STRATEGY_OBSERVATION_JOURNAL_ENABLED:
        return []
    try:
        if not Path(path).exists():
            return []
        clauses = [
            "run_date >= date('now', ?)",
            "primary_reason IS NOT NULL",
            "primary_reason != ''",
        ]
        params: list[Any] = [f"-{days} days"]
        if strategy_id:
            clauses.append("strategy_id = ?")
            params.append(strategy_id)
        where = "WHERE " + " AND ".join(clauses)
        params.append(min(int(limit or 100), 200))
        with _connect(path) as conn:
            rows = conn.execute(
                f"""
                SELECT strategy_id, primary_reason,
                       COUNT(*) AS cnt,
                       SUM(CASE WHEN status_bucket='pass' THEN 1 ELSE 0 END) AS pass_cnt,
                       SUM(CASE WHEN status_bucket IN ('fail','watch') THEN 1 ELSE 0 END) AS issue_cnt
                FROM strategy_observations {where}
                GROUP BY strategy_id, primary_reason
                ORDER BY cnt DESC LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
