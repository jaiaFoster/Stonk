"""
app/services/ff_graduation_analysis_service.py — MP2 Item 3.

Read-only, provider-free. Reads ff_journal data and aggregates stats
for FF graduation decision-making. Does NOT change FF_DRY_RUN or any gate.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from app import config


def build_ff_graduation_analysis() -> dict[str, Any]:
    """Aggregate FF journal data for graduation readiness assessment."""
    path = str(config.FF_JOURNAL_DB_PATH)

    if not Path(path).exists():
        return {
            "status": "no_data",
            "provider_calls_triggered": False,
            "read_only": True,
            "message": "FF journal database not found. No observations recorded yet.",
            "ff_dry_run": True,
        }

    try:
        conn = sqlite3.connect(path, timeout=5)
        conn.row_factory = sqlite3.Row
        stats = _aggregate_stats(conn)
        closest = _closest_to_pass(conn)
        gate_breakdown = _gate_breakdown(conn)
        recent_runs = _recent_runs(conn, limit=10)
        conn.close()
    except Exception as exc:
        return {
            "status": "error",
            "provider_calls_triggered": False,
            "read_only": True,
            "message": f"Failed to read FF journal: {type(exc).__name__}",
        }

    pass_threshold = float(getattr(config, "FF_MIN_FORWARD_FACTOR", 0.20))
    any_crossed = stats.get("any_crossed_threshold", False)

    return {
        "status": "ok",
        "provider_calls_triggered": False,
        "read_only": True,
        "ff_dry_run": bool(config.FORWARD_FACTOR_DRY_RUN),
        "ff_journal_enabled": bool(config.FF_JOURNAL_ENABLED),
        "pass_threshold": pass_threshold,
        "totals": {
            "total_observations": stats["total"],
            "distinct_tickers": stats["tickers"],
            "distinct_runs": stats["runs"],
            "latest_run_date": stats["latest_date"],
        },
        "gate_stats": {
            "structure_built_count": gate_breakdown["structure_built"],
            "diagnostic_model_count": gate_breakdown["diagnostic_model"],
            "source_qualified_count": gate_breakdown["source_qualified"],
            "chain_approved_count": gate_breakdown["chain_approved"],
            "cheap_eligible_count": gate_breakdown["cheap_eligible"],
        },
        "graduation_signals": {
            "any_candidate_crossed_threshold": any_crossed,
            "dry_run_pass_count": stats["dry_run_pass_count"],
            "closest_to_threshold": closest,
        },
        "verdict_distribution": stats["verdict_distribution"],
        "recent_runs": recent_runs,
    }


def _aggregate_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        "SELECT COUNT(*) AS total, COUNT(DISTINCT ticker) AS tickers, "
        "COUNT(DISTINCT run_id) AS runs, MAX(run_date) AS latest_date "
        "FROM ff_journal"
    ).fetchone()

    pass_count = conn.execute(
        "SELECT COUNT(*) AS c FROM ff_journal WHERE verdict LIKE '%DRY RUN PASS%'"
    ).fetchone()

    any_crossed = conn.execute(
        "SELECT COUNT(*) AS c FROM ff_journal WHERE signal_score >= ?",
        (float(getattr(config, "FF_MIN_FORWARD_FACTOR", 0.20)),),
    ).fetchone()

    verdicts = conn.execute(
        "SELECT verdict, COUNT(*) AS cnt FROM ff_journal GROUP BY verdict ORDER BY cnt DESC"
    ).fetchall()

    return {
        "total": (row["total"] or 0) if row else 0,
        "tickers": (row["tickers"] or 0) if row else 0,
        "runs": (row["runs"] or 0) if row else 0,
        "latest_date": row["latest_date"] if row else None,
        "dry_run_pass_count": (pass_count["c"] or 0) if pass_count else 0,
        "any_crossed_threshold": bool(any_crossed and any_crossed["c"] > 0),
        "verdict_distribution": {v["verdict"]: v["cnt"] for v in verdicts},
    }


def _gate_breakdown(conn: sqlite3.Connection) -> dict[str, int]:
    row = conn.execute(
        "SELECT "
        "SUM(CASE WHEN structure_built=1 THEN 1 ELSE 0 END) AS sb, "
        "SUM(CASE WHEN diagnostic_model=1 THEN 1 ELSE 0 END) AS dm, "
        "SUM(CASE WHEN source_qualified=1 THEN 1 ELSE 0 END) AS sq, "
        "SUM(CASE WHEN chain_approved=1 THEN 1 ELSE 0 END) AS ca, "
        "SUM(CASE WHEN cheap_eligible=1 THEN 1 ELSE 0 END) AS ce "
        "FROM ff_journal"
    ).fetchone()
    return {
        "structure_built": (row["sb"] or 0) if row else 0,
        "diagnostic_model": (row["dm"] or 0) if row else 0,
        "source_qualified": (row["sq"] or 0) if row else 0,
        "chain_approved": (row["ca"] or 0) if row else 0,
        "cheap_eligible": (row["ce"] or 0) if row else 0,
    }


def _closest_to_pass(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Find the observation with the highest signal_score — closest to graduation."""
    row = conn.execute(
        "SELECT ticker, run_date, signal_score, verdict, structure_built, "
        "diagnostic_model, source_qualified "
        "FROM ff_journal WHERE signal_score IS NOT NULL "
        "ORDER BY signal_score DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    return {
        "ticker": row["ticker"],
        "run_date": row["run_date"],
        "signal_score": row["signal_score"],
        "verdict": row["verdict"],
        "structure_built": bool(row["structure_built"]),
        "diagnostic_model": bool(row["diagnostic_model"]),
        "source_qualified": bool(row["source_qualified"]),
    }


def _recent_runs(conn: sqlite3.Connection, limit: int = 10) -> list[dict[str, Any]]:
    """Summary per recent run."""
    rows = conn.execute(
        "SELECT run_id, run_date, COUNT(*) AS candidates, "
        "SUM(CASE WHEN structure_built=1 THEN 1 ELSE 0 END) AS built, "
        "SUM(CASE WHEN verdict LIKE '%DRY RUN PASS%' THEN 1 ELSE 0 END) AS passes, "
        "MAX(signal_score) AS best_score "
        "FROM ff_journal GROUP BY run_id ORDER BY run_date DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        {
            "run_id": r["run_id"],
            "run_date": r["run_date"],
            "candidates": r["candidates"],
            "structures_built": r["built"],
            "dry_run_passes": r["passes"],
            "best_signal_score": r["best_score"],
        }
        for r in rows
    ]
