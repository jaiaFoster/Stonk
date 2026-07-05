"""Read-only FF graduation evidence summary from ff_journal."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import config


def build_ff_graduation_analysis(days: int = 30) -> dict[str, Any]:
    period_days = max(1, min(int(days or 30), 90))
    path = str(config.FF_JOURNAL_DB_PATH)
    base = {
        "status": "no_data",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "period_days": period_days,
        "dry_run": bool(config.FORWARD_FACTOR_DRY_RUN),
        "provider_calls_triggered": False,
        "read_only": True,
        "eligible_for_review": False,
        "readiness": {"eligible_for_review": False, "reasons": ["FF journal database not found. No observations recorded yet."]},
    }
    if not Path(path).exists():
        return base
    try:
        conn = sqlite3.connect(path, timeout=5)
        conn.row_factory = sqlite3.Row
        cutoff = f"datetime('now', '-{period_days} days')"
        columns = _table_columns(conn)
        structure_expr = "structure_built" if "structure_built" in columns else "0"
        liquidity_expr = "liquidity_status" if "liquidity_status" in columns else "NULL"
        contaminated_expr = "earnings_contaminated" if "earnings_contaminated" in columns else "0"
        source_ff_expr = "source_forward_factor" if "source_forward_factor" in columns else "NULL"
        forward_ff_expr = "forward_factor" if "forward_factor" in columns else "NULL"
        diagnostic_ff_expr = (
            "diagnostic_raw_iv_forward_factor" if "diagnostic_raw_iv_forward_factor" in columns else "NULL"
        )
        blocker_expr = _blocker_expr(columns)
        total = _count(conn, f"SELECT COUNT(*) FROM ff_journal WHERE created_at >= {cutoff}")
        pass_obs = _count(conn, f"SELECT COUNT(*) FROM ff_journal WHERE created_at >= {cutoff} AND (is_pass=1 OR verdict LIKE '%PASS%')")
        src_pos = _count(conn, f"SELECT COUNT(*) FROM ff_journal WHERE created_at >= {cutoff} AND signal_tier='SOURCE_QUALIFIED_POSITIVE'")
        diag_pos = _count(conn, f"SELECT COUNT(*) FROM ff_journal WHERE created_at >= {cutoff} AND signal_tier='DIAGNOSTIC_POSITIVE'")
        near_pos = _count(conn, f"SELECT COUNT(*) FROM ff_journal WHERE created_at >= {cutoff} AND signal_tier='WATCH_NEAR_POSITIVE'")
        structure_complete = _count(conn, f"SELECT COUNT(*) FROM ff_journal WHERE created_at >= {cutoff} AND {structure_expr}=1")
        liquidity_pass = _count(
            conn,
            f"SELECT COUNT(*) FROM ff_journal WHERE created_at >= {cutoff} AND UPPER(COALESCE({liquidity_expr},''))='PASS'",
        )
        contaminated = _count(conn, f"SELECT COUNT(*) FROM ff_journal WHERE created_at >= {cutoff} AND {contaminated_expr}=1")
        calc_complete = _count(
            conn,
            f"""SELECT COUNT(*) FROM ff_journal WHERE created_at >= {cutoff}
            AND ({forward_ff_expr} IS NOT NULL OR {diagnostic_ff_expr} IS NOT NULL OR {source_ff_expr} IS NOT NULL)""",
        )
        avg_ff = conn.execute(
            f"SELECT AVG(COALESCE({source_ff_expr}, {forward_ff_expr}, {diagnostic_ff_expr})) AS avg_ff "
            f"FROM ff_journal WHERE created_at >= {cutoff} AND COALESCE({source_ff_expr}, {forward_ff_expr}, {diagnostic_ff_expr}) IS NOT NULL"
        ).fetchone()
        top_passes = [dict(r) for r in conn.execute(
            f"""SELECT ticker, COUNT(*) AS count
                FROM ff_journal
                WHERE created_at >= {cutoff}
                  AND (signal_tier IN ('SOURCE_QUALIFIED_POSITIVE','DIAGNOSTIC_POSITIVE','WATCH_NEAR_POSITIVE') OR is_pass=1)
                GROUP BY ticker ORDER BY count DESC, ticker ASC LIMIT 10"""
        ).fetchall()]
        recent_passes = [dict(r) for r in conn.execute(
            f"""SELECT ticker, verdict, signal_tier, COALESCE({source_ff_expr}, {forward_ff_expr}, {diagnostic_ff_expr}) AS forward_factor,
                       front_expiration, back_expiration, {liquidity_expr} AS liquidity_status, {blocker_expr} AS primary_blocker, created_at
                FROM ff_journal
                WHERE created_at >= {cutoff}
                  AND (signal_tier IN ('SOURCE_QUALIFIED_POSITIVE','DIAGNOSTIC_POSITIVE','WATCH_NEAR_POSITIVE') OR is_pass=1)
                ORDER BY created_at DESC LIMIT 15"""
        ).fetchall()]
        common_blockers = [dict(r) for r in conn.execute(
            f"""SELECT {blocker_expr} AS blocker, COUNT(*) AS count
                FROM ff_journal
                WHERE created_at >= {cutoff}
                GROUP BY blocker ORDER BY count DESC LIMIT 10"""
        ).fetchall()]
        manual_reviews = 0
        readiness = _readiness(
            calc_complete=calc_complete,
            positive=src_pos + diag_pos + near_pos + pass_obs,
            source_qualified=src_pos,
            structure_complete=structure_complete,
            manual_reviews=manual_reviews,
        )
        return {
            "status": "ok",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "period_days": period_days,
            "total_observations": total,
            "pass_observations": pass_obs,
            "source_qualified_positive_count": src_pos,
            "diagnostic_positive_count": diag_pos,
            "near_positive_count": near_pos,
            "structure_complete_count": structure_complete,
            "liquidity_pass_count": liquidity_pass,
            "earnings_contaminated_count": contaminated,
            "avg_forward_factor": round(float(avg_ff["avg_ff"]), 4) if avg_ff and avg_ff["avg_ff"] is not None else None,
            "top_pass_tickers": top_passes,
            "recent_passes": recent_passes,
            "common_blockers": common_blockers,
            "dry_run": bool(config.FORWARD_FACTOR_DRY_RUN),
            "provider_calls_triggered": False,
            "read_only": True,
            "eligible_for_review": readiness["eligible_for_review"],
            "readiness": readiness,
        }
    except Exception as exc:
        return {
            "status": "error",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "period_days": period_days,
            "provider_calls_triggered": False,
            "read_only": True,
            "dry_run": bool(config.FORWARD_FACTOR_DRY_RUN),
            "eligible_for_review": False,
            "readiness": {"eligible_for_review": False, "reasons": [f"Failed to read FF journal: {type(exc).__name__}"]},
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _count(conn: sqlite3.Connection, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    return int(row[0] or 0) if row else 0


def _table_columns(conn: sqlite3.Connection) -> set[str]:
    return {str(row["name"]) for row in conn.execute("PRAGMA table_info(ff_journal)").fetchall()}


def _blocker_expr(columns: set[str]) -> str:
    parts: list[str] = []
    if "primary_blocker" in columns:
        parts.append("primary_blocker")
    if "gate_fail_reason" in columns:
        parts.append("gate_fail_reason")
    if "verdict" in columns:
        parts.append("verdict")
    return f"COALESCE({', '.join(parts)})" if parts else "NULL"


def _readiness(*, calc_complete: int, positive: int, source_qualified: int, structure_complete: int, manual_reviews: int) -> dict[str, Any]:
    reasons: list[str] = []
    if calc_complete < int(getattr(config, "FF_GRAD_MIN_CALC_COMPLETE", 20) or 20):
        reasons.append(f"Needs at least {int(getattr(config, 'FF_GRAD_MIN_CALC_COMPLETE', 20) or 20)} FF calculation-complete observations")
    if positive < int(getattr(config, "FF_GRAD_MIN_POSITIVE", 5) or 5):
        reasons.append(f"Needs at least {int(getattr(config, 'FF_GRAD_MIN_POSITIVE', 5) or 5)} PASS or near-positive observations")
    if source_qualified < int(getattr(config, "FF_GRAD_MIN_SOURCE_QUALIFIED", 3) or 3):
        reasons.append(f"Needs at least {int(getattr(config, 'FF_GRAD_MIN_SOURCE_QUALIFIED', 3) or 3)} source-qualified positive observations")
    if structure_complete < int(getattr(config, "FF_GRAD_MIN_STRUCTURE_COMPLETE", 3) or 3):
        reasons.append(f"Needs at least {int(getattr(config, 'FF_GRAD_MIN_STRUCTURE_COMPLETE', 3) or 3)} structure-complete observations")
    if manual_reviews < int(getattr(config, "FF_GRAD_MIN_MANUAL_REVIEWS", 1) or 1):
        reasons.append("Needs manual review outcome capture")
    return {"eligible_for_review": not reasons, "reasons": reasons}
