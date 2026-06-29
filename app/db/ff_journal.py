"""FF paper observation journal — SQLite persistence for FF candidate rows per run."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ff_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    run_date TEXT NOT NULL,
    ticker TEXT NOT NULL,
    ff_candidate_stage TEXT,
    cheap_eligible INTEGER,
    chain_approved INTEGER,
    source_qualified INTEGER,
    diagnostic_model INTEGER,
    structure_built INTEGER,
    gate_fail_reason TEXT,
    verdict TEXT,
    signal_score REAL,
    put_short_expiration TEXT,
    put_long_expiration TEXT,
    call_short_expiration TEXT,
    call_long_expiration TEXT,
    put_short_delta REAL,
    put_long_delta REAL,
    call_short_delta REAL,
    call_long_delta REAL,
    front_iv REAL,
    back_iv REAL,
    underlying_price REAL,
    is_diagnostic_only INTEGER,
    source_qualification TEXT,
    earnings_contaminated INTEGER,
    contamination_reason TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ff_journal_ticker_date ON ff_journal (ticker, run_date);
"""


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


def _row_from_candidate(run_id: str, run_date: str, row: dict[str, Any]) -> dict[str, Any]:
    gates = row.get("ff_gates") or {}
    structure_built = bool(gates.get("structure_built"))
    legs = row.get("structure_legs") or {}
    put_short = legs.get("put_short") or {}
    put_long = legs.get("put_long") or {}
    call_short = legs.get("call_short") or {}
    call_long = legs.get("call_long") or {}
    return {
        "run_id": run_id,
        "run_date": run_date,
        "ticker": str(row.get("ticker") or ""),
        "ff_candidate_stage": row.get("ff_candidate_stage"),
        "cheap_eligible": int(bool(gates.get("cheap_eligible"))) if gates else None,
        "chain_approved": int(bool(gates.get("chain_approved"))) if gates else None,
        "source_qualified": int(bool(gates.get("source_qualified"))) if gates else None,
        "diagnostic_model": int(bool(gates.get("diagnostic_model"))) if gates else None,
        "structure_built": int(structure_built),
        "gate_fail_reason": gates.get("gate_fail_reason"),
        "verdict": row.get("verdict"),
        "signal_score": _float(row.get("signal_score")),
        "put_short_expiration": row.get("put_short_expiration") if structure_built else None,
        "put_long_expiration": row.get("put_long_expiration") if structure_built else None,
        "call_short_expiration": row.get("call_short_expiration") if structure_built else None,
        "call_long_expiration": row.get("call_long_expiration") if structure_built else None,
        "put_short_delta": _float(put_short.get("delta")) if structure_built else None,
        "put_long_delta": _float(put_long.get("delta")) if structure_built else None,
        "call_short_delta": _float(call_short.get("delta")) if structure_built else None,
        "call_long_delta": _float(call_long.get("delta")) if structure_built else None,
        "front_iv": _float(row.get("front_raw_iv")),
        "back_iv": _float(row.get("back_raw_iv")),
        "underlying_price": _float(row.get("underlying_price") or row.get("current_price")),
        "is_diagnostic_only": int(bool(row.get("is_diagnostic_only"))),
        "source_qualification": row.get("source_qualification"),
        "earnings_contaminated": int(bool(row.get("earnings_contaminated"))),
        "contamination_reason": row.get("earnings_contamination_reason") or row.get("contamination_reason"),
    }


def _float(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def write_run(run_id: str, run_date: str, candidates: list[dict[str, Any]], db_path: str | None = None) -> int:
    """Write one row per candidate. Returns rows written. Swallows all errors."""
    if not config.FF_JOURNAL_ENABLED:
        return 0
    path = db_path or config.FF_JOURNAL_DB_PATH
    try:
        _ensure_schema(path)
        rows = [_row_from_candidate(run_id, run_date, c) for c in candidates]
        if not rows:
            return 0
        with _connect(path) as conn:
            conn.executemany(
                """
                INSERT INTO ff_journal (
                    run_id, run_date, ticker, ff_candidate_stage,
                    cheap_eligible, chain_approved, source_qualified, diagnostic_model,
                    structure_built, gate_fail_reason, verdict, signal_score,
                    put_short_expiration, put_long_expiration, call_short_expiration, call_long_expiration,
                    put_short_delta, put_long_delta, call_short_delta, call_long_delta,
                    front_iv, back_iv, underlying_price, is_diagnostic_only,
                    source_qualification, earnings_contaminated, contamination_reason
                ) VALUES (
                    :run_id, :run_date, :ticker, :ff_candidate_stage,
                    :cheap_eligible, :chain_approved, :source_qualified, :diagnostic_model,
                    :structure_built, :gate_fail_reason, :verdict, :signal_score,
                    :put_short_expiration, :put_long_expiration, :call_short_expiration, :call_long_expiration,
                    :put_short_delta, :put_long_delta, :call_short_delta, :call_long_delta,
                    :front_iv, :back_iv, :underlying_price, :is_diagnostic_only,
                    :source_qualification, :earnings_contaminated, :contamination_reason
                )
                """,
                rows,
            )
        return len(rows)
    except Exception:
        return 0


def historical_ivs(ticker: str, db_path: str | None = None) -> list[float]:
    """Return historical front_iv values for a ticker from the journal. Safe on any error."""
    path = db_path or config.FF_JOURNAL_DB_PATH
    if not config.FF_JOURNAL_ENABLED:
        return []
    try:
        if not Path(path).exists():
            return []
        with _connect(path) as conn:
            rows = conn.execute(
                "SELECT DISTINCT front_iv FROM ff_journal WHERE ticker=? AND front_iv IS NOT NULL AND front_iv > 0 ORDER BY created_at",
                (ticker.upper().strip(),),
            ).fetchall()
            return [float(r["front_iv"]) for r in rows]
    except Exception:
        return []


def journal_summary(db_path: str | None = None) -> dict[str, Any]:
    """Summary stats for diagnostic endpoint. Provider-free. Returns safe dict on any error."""
    path = db_path or config.FF_JOURNAL_DB_PATH
    base: dict[str, Any] = {"enabled": bool(config.FF_JOURNAL_ENABLED), "total_observations": 0, "tickers_observed": 0, "runs_recorded": 0, "latest_structure_built": None, "latest_run_date": None}
    if not config.FF_JOURNAL_ENABLED:
        return base
    try:
        if not Path(path).exists():
            return base
        with _connect(path) as conn:
            row = conn.execute("SELECT COUNT(*) AS total, COUNT(DISTINCT ticker) AS tickers, COUNT(DISTINCT run_id) AS runs, MAX(run_date) AS latest_date FROM ff_journal").fetchone()
            if row:
                base["total_observations"] = row["total"] or 0
                base["tickers_observed"] = row["tickers"] or 0
                base["runs_recorded"] = row["runs"] or 0
                base["latest_run_date"] = row["latest_date"]
            sb = conn.execute("SELECT ticker FROM ff_journal WHERE structure_built=1 ORDER BY created_at DESC LIMIT 1").fetchone()
            base["latest_structure_built"] = sb["ticker"] if sb else None
        return base
    except Exception:
        return base
