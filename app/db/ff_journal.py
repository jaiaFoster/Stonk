"""FF paper observation journal — SQLite persistence for FF candidate rows per run."""

from __future__ import annotations

import json
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
    observed_at TEXT DEFAULT (datetime('now')),
    ticker TEXT NOT NULL,
    ff_candidate_stage TEXT,
    cheap_eligible INTEGER,
    chain_approved INTEGER,
    source_qualified INTEGER,
    diagnostic_model INTEGER,
    structure_built INTEGER,
    gate_fail_reason TEXT,
    verdict TEXT,
    signal_tier TEXT,
    is_positive_signal INTEGER,
    is_pass INTEGER,
    is_near_positive INTEGER,
    dry_run INTEGER,
    formula_version TEXT,
    source_spec_version TEXT,
    signal_score REAL,
    actionability_score REAL,
    put_short_expiration TEXT,
    put_long_expiration TEXT,
    call_short_expiration TEXT,
    call_long_expiration TEXT,
    front_expiration TEXT,
    back_expiration TEXT,
    front_dte REAL,
    back_dte REAL,
    put_short_delta REAL,
    put_long_delta REAL,
    call_short_delta REAL,
    call_long_delta REAL,
    front_iv REAL,
    back_iv REAL,
    front_ex_earnings_iv REAL,
    back_ex_earnings_iv REAL,
    forward_factor REAL,
    diagnostic_raw_iv_forward_factor REAL,
    source_forward_factor REAL,
    front_iv_derivation_method TEXT,
    back_iv_derivation_method TEXT,
    adjustment_method TEXT,
    adjustment_version TEXT,
    earnings_date TEXT,
    earnings_time TEXT,
    earnings_source TEXT,
    earnings_confidence TEXT,
    underlying_price REAL,
    is_diagnostic_only INTEGER,
    source_qualification TEXT,
    earnings_contaminated INTEGER,
    earnings_contamination_reason TEXT,
    contamination_reason TEXT,
    structure_status TEXT,
    structure_reason TEXT,
    liquidity_status TEXT,
    package_slippage_pct REAL,
    debit_at_risk REAL,
    can_enter_daily_opportunity INTEGER,
    can_trade_live INTEGER,
    primary_blocker TEXT,
    next_action TEXT,
    raw_json TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ff_journal_ticker_date ON ff_journal (ticker, run_date);
CREATE INDEX IF NOT EXISTS ff_journal_signal_tier ON ff_journal (signal_tier, run_date);
"""

_EXTRA_COLUMNS = {
    "observed_at": "TEXT",
    "signal_tier": "TEXT",
    "is_positive_signal": "INTEGER",
    "is_pass": "INTEGER",
    "is_near_positive": "INTEGER",
    "dry_run": "INTEGER",
    "formula_version": "TEXT",
    "source_spec_version": "TEXT",
    "actionability_score": "REAL",
    "front_expiration": "TEXT",
    "back_expiration": "TEXT",
    "front_dte": "REAL",
    "back_dte": "REAL",
    "front_ex_earnings_iv": "REAL",
    "back_ex_earnings_iv": "REAL",
    "forward_factor": "REAL",
    "diagnostic_raw_iv_forward_factor": "REAL",
    "source_forward_factor": "REAL",
    "front_iv_derivation_method": "TEXT",
    "back_iv_derivation_method": "TEXT",
    "adjustment_method": "TEXT",
    "adjustment_version": "TEXT",
    "earnings_date": "TEXT",
    "earnings_time": "TEXT",
    "earnings_source": "TEXT",
    "earnings_confidence": "TEXT",
    "earnings_contamination_reason": "TEXT",
    "structure_status": "TEXT",
    "structure_reason": "TEXT",
    "liquidity_status": "TEXT",
    "package_slippage_pct": "REAL",
    "debit_at_risk": "REAL",
    "can_enter_daily_opportunity": "INTEGER",
    "can_trade_live": "INTEGER",
    "primary_blocker": "TEXT",
    "next_action": "TEXT",
    "raw_json": "TEXT",
}


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
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(ff_journal)").fetchall()}
        for name, sql_type in _EXTRA_COLUMNS.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE ff_journal ADD COLUMN {name} {sql_type}")


def _row_from_candidate(run_id: str, run_date: str, row: dict[str, Any]) -> dict[str, Any]:
    gates = row.get("ff_gates") or {}
    structure_built = bool(gates.get("structure_built"))
    legs = row.get("structure_legs") or {}
    put_short = legs.get("put_short") or {}
    put_long = legs.get("put_long") or {}
    call_short = legs.get("call_short") or {}
    call_long = legs.get("call_long") or {}
    front_exp = row.get("front_expiration") or row.get("put_short_expiration") or row.get("call_short_expiration")
    back_exp = row.get("back_expiration") or row.get("put_long_expiration") or row.get("call_long_expiration")
    earnings_source = row.get("earnings_source")
    if not earnings_source:
        sources = row.get("date_sources") or row.get("sources_seen") or []
        if isinstance(sources, (list, tuple)):
            earnings_source = ",".join(str(item) for item in sources[:4])
    observed_at = row.get("observed_at") or row.get("generated_at") or row.get("created_at")
    return {
        "run_id": run_id,
        "run_date": run_date,
        "observed_at": observed_at,
        "ticker": str(row.get("ticker") or ""),
        "ff_candidate_stage": row.get("ff_candidate_stage"),
        "cheap_eligible": int(bool(gates.get("cheap_eligible"))) if gates else None,
        "chain_approved": int(bool(gates.get("chain_approved"))) if gates else None,
        "source_qualified": int(bool(gates.get("source_qualified"))) if gates else None,
        "diagnostic_model": int(bool(gates.get("diagnostic_model"))) if gates else None,
        "structure_built": int(structure_built),
        "gate_fail_reason": gates.get("gate_fail_reason"),
        "verdict": row.get("verdict"),
        "signal_tier": row.get("signal_tier"),
        "is_positive_signal": int(bool(row.get("is_positive_signal"))),
        "is_pass": int("PASS" in str(row.get("verdict") or "").upper() and not str(row.get("verdict") or "").upper().startswith("SKIPPED")),
        "is_near_positive": int(str(row.get("signal_tier") or "") == "WATCH_NEAR_POSITIVE"),
        "dry_run": int(bool(row.get("dry_run", config.FORWARD_FACTOR_DRY_RUN))),
        "formula_version": row.get("formula_version") or getattr(config, "FF_FORMULA_VERSION", None),
        "source_spec_version": str(row.get("source_spec_version") or getattr(config, "FF_SOURCE_SPEC_VERSION", "")) or None,
        "signal_score": _float(row.get("signal_score")),
        "actionability_score": _float(row.get("actionability_score")),
        "put_short_expiration": row.get("put_short_expiration") if structure_built else None,
        "put_long_expiration": row.get("put_long_expiration") if structure_built else None,
        "call_short_expiration": row.get("call_short_expiration") if structure_built else None,
        "call_long_expiration": row.get("call_long_expiration") if structure_built else None,
        "front_expiration": front_exp,
        "back_expiration": back_exp,
        "front_dte": _float(row.get("front_dte")),
        "back_dte": _float(row.get("back_dte")),
        "put_short_delta": _float(put_short.get("delta")) if structure_built else None,
        "put_long_delta": _float(put_long.get("delta")) if structure_built else None,
        "call_short_delta": _float(call_short.get("delta")) if structure_built else None,
        "call_long_delta": _float(call_long.get("delta")) if structure_built else None,
        "front_iv": _float(row.get("front_iv") if row.get("front_iv") is not None else row.get("front_raw_iv")),
        "back_iv": _float(row.get("back_iv") if row.get("back_iv") is not None else row.get("back_raw_iv")),
        "front_ex_earnings_iv": _float(row.get("front_ex_earnings_iv")),
        "back_ex_earnings_iv": _float(row.get("back_ex_earnings_iv")),
        "forward_factor": _float(row.get("forward_factor")),
        "diagnostic_raw_iv_forward_factor": _float(row.get("diagnostic_raw_iv_forward_factor")),
        "source_forward_factor": _float(row.get("source_forward_factor")),
        "front_iv_derivation_method": row.get("front_iv_derivation_method"),
        "back_iv_derivation_method": row.get("back_iv_derivation_method"),
        "adjustment_method": row.get("adjustment_method"),
        "adjustment_version": row.get("adjustment_version"),
        "earnings_date": row.get("earnings_date"),
        "earnings_time": row.get("earnings_time"),
        "earnings_source": earnings_source,
        "earnings_confidence": row.get("earnings_confidence") or row.get("date_confidence"),
        "underlying_price": _float(row.get("underlying_price") or row.get("current_price")),
        "is_diagnostic_only": int(bool(row.get("is_diagnostic_only"))),
        "source_qualification": row.get("source_qualification"),
        "earnings_contaminated": int(bool(row.get("earnings_contaminated"))),
        "earnings_contamination_reason": row.get("earnings_contamination_reason") or row.get("contamination_reason"),
        "contamination_reason": row.get("earnings_contamination_reason") or row.get("contamination_reason"),
        "structure_status": row.get("structure_status"),
        "structure_reason": row.get("structure_reason"),
        "liquidity_status": row.get("liquidity_status") or ((row.get("liquidity_result") or {}).get("status") if isinstance(row.get("liquidity_result"), dict) else None),
        "package_slippage_pct": _float(row.get("package_slippage_pct")),
        "debit_at_risk": _float(row.get("debit_at_risk") if row.get("debit_at_risk") is not None else row.get("conservative_debit")),
        "can_enter_daily_opportunity": int(bool(row.get("can_enter_daily_opportunity"))) if row.get("can_enter_daily_opportunity") is not None else None,
        "can_trade_live": int(bool(row.get("can_trade_live"))) if row.get("can_trade_live") is not None else None,
        "primary_blocker": row.get("primary_blocker"),
        "next_action": row.get("next_action"),
        "raw_json": json.dumps(row, default=str),
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
                    structure_built, gate_fail_reason, verdict, signal_tier,
                    is_positive_signal, is_pass, is_near_positive, dry_run,
                    formula_version, source_spec_version,
                    signal_score, actionability_score,
                    put_short_expiration, put_long_expiration, call_short_expiration, call_long_expiration,
                    front_expiration, back_expiration, front_dte, back_dte,
                    put_short_delta, put_long_delta, call_short_delta, call_long_delta,
                    front_iv, back_iv, front_ex_earnings_iv, back_ex_earnings_iv,
                    forward_factor, diagnostic_raw_iv_forward_factor, source_forward_factor,
                    front_iv_derivation_method, back_iv_derivation_method, adjustment_method, adjustment_version,
                    earnings_date, earnings_time, earnings_source, earnings_confidence,
                    underlying_price, is_diagnostic_only,
                    source_qualification, earnings_contaminated, earnings_contamination_reason, contamination_reason,
                    structure_status, structure_reason, liquidity_status, package_slippage_pct, debit_at_risk,
                    can_enter_daily_opportunity, can_trade_live, primary_blocker, next_action,
                    observed_at, raw_json
                ) VALUES (
                    :run_id, :run_date, :ticker, :ff_candidate_stage,
                    :cheap_eligible, :chain_approved, :source_qualified, :diagnostic_model,
                    :structure_built, :gate_fail_reason, :verdict, :signal_tier,
                    :is_positive_signal, :is_pass, :is_near_positive, :dry_run,
                    :formula_version, :source_spec_version,
                    :signal_score, :actionability_score,
                    :put_short_expiration, :put_long_expiration, :call_short_expiration, :call_long_expiration,
                    :front_expiration, :back_expiration, :front_dte, :back_dte,
                    :put_short_delta, :put_long_delta, :call_short_delta, :call_long_delta,
                    :front_iv, :back_iv, :front_ex_earnings_iv, :back_ex_earnings_iv,
                    :forward_factor, :diagnostic_raw_iv_forward_factor, :source_forward_factor,
                    :front_iv_derivation_method, :back_iv_derivation_method, :adjustment_method, :adjustment_version,
                    :earnings_date, :earnings_time, :earnings_source, :earnings_confidence,
                    :underlying_price, :is_diagnostic_only,
                    :source_qualification, :earnings_contaminated, :earnings_contamination_reason, :contamination_reason,
                    :structure_status, :structure_reason, :liquidity_status, :package_slippage_pct, :debit_at_risk,
                    :can_enter_daily_opportunity, :can_trade_live, :primary_blocker, :next_action,
                    :observed_at, :raw_json
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
