"""
app/db/options_provider_comparison_repository.py — Comparison persistence.

Patch 33B: Stores summary-level provider comparison records in SQLite.
Does NOT store full contract data. Does NOT store API keys, auth headers,
or secret URLs. Stores the top material divergences only (up to 10 rows).
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app import config

_DB_PATH = config.MARKET_DATA_DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS options_provider_comparisons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
    ticker TEXT NOT NULL,
    primary_provider TEXT NOT NULL,
    shadow_provider TEXT NOT NULL,
    selection_outcome TEXT NOT NULL,
    classification TEXT NOT NULL,
    primary_contract_count INTEGER,
    shadow_contract_count INTEGER,
    matched_contract_count INTEGER,
    coverage_pct REAL,
    mid_median_diff_pct REAL,
    mid_max_diff_abs REAL,
    iv_median_diff_abs REAL,
    delta_median_diff_abs REAL,
    underlying_diff_pct REAL,
    underlying_classification TEXT,
    material_divergence_count INTEGER,
    material_divergences_json TEXT,
    shadow_skip_reason TEXT,
    notes_json TEXT,
    run_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_opc_ticker_recorded ON options_provider_comparisons(ticker, recorded_at);
CREATE INDEX IF NOT EXISTS idx_opc_classification ON options_provider_comparisons(classification);
"""


@contextmanager
def _db(path: str = _DB_PATH):
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
        yield conn
    finally:
        conn.close()


def store_comparison(
    comparison: Any,  # ChainComparisonResult from comparison service
    run_id: str | None = None,
) -> int | None:
    """Persist a comparison summary. Returns inserted row id or None on failure."""
    try:
        from app.services.options_provider_comparison_service import ChainComparisonResult
        if not isinstance(comparison, ChainComparisonResult):
            return None

        with _db() as conn:
            cur = conn.execute(
                """
                INSERT INTO options_provider_comparisons (
                    ticker, primary_provider, shadow_provider, selection_outcome,
                    classification, primary_contract_count, shadow_contract_count,
                    matched_contract_count, coverage_pct, mid_median_diff_pct,
                    mid_max_diff_abs, iv_median_diff_abs, delta_median_diff_abs,
                    underlying_diff_pct, underlying_classification,
                    material_divergence_count, material_divergences_json,
                    shadow_skip_reason, notes_json, run_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    comparison.ticker,
                    comparison.primary_provider,
                    comparison.shadow_provider,
                    comparison.selection_outcome,
                    comparison.classification,
                    comparison.primary_contract_count,
                    comparison.shadow_contract_count,
                    comparison.matched_contract_count,
                    round(comparison.coverage_pct, 4),
                    round(comparison.mid_median_diff_pct, 6) if comparison.mid_median_diff_pct is not None else None,
                    round(comparison.mid_max_diff_abs, 4) if comparison.mid_max_diff_abs is not None else None,
                    round(comparison.iv_median_diff_abs, 6) if comparison.iv_median_diff_abs is not None else None,
                    round(comparison.delta_median_diff_abs, 6) if comparison.delta_median_diff_abs is not None else None,
                    round(comparison.underlying_diff_pct, 6) if comparison.underlying_diff_pct is not None else None,
                    comparison.underlying_classification,
                    len(comparison.material_divergences),
                    json.dumps(comparison.material_divergences),
                    comparison.shadow_skip_reason,
                    json.dumps(comparison.notes),
                    run_id,
                ),
            )
            conn.commit()
            return cur.lastrowid
    except Exception:
        return None


def get_recent_comparisons(
    limit: int = 50,
    ticker: str | None = None,
    classification: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch recent comparison records for the read-only dev endpoint."""
    try:
        with _db() as conn:
            clauses: list[str] = []
            params: list[Any] = []
            if ticker:
                clauses.append("ticker = ?")
                params.append(ticker.upper())
            if classification:
                clauses.append("classification = ?")
                params.append(classification)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            params.append(limit)
            rows = conn.execute(
                f"""
                SELECT id, recorded_at, ticker, primary_provider, shadow_provider,
                       selection_outcome, classification, primary_contract_count,
                       shadow_contract_count, matched_contract_count, coverage_pct,
                       mid_median_diff_pct, mid_max_diff_abs, iv_median_diff_abs,
                       delta_median_diff_abs, underlying_diff_pct, underlying_classification,
                       material_divergence_count, material_divergences_json,
                       shadow_skip_reason, notes_json, run_id
                FROM options_provider_comparisons
                {where}
                ORDER BY recorded_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            result = []
            for row in rows:
                d = dict(row)
                try:
                    d["material_divergences"] = json.loads(d.pop("material_divergences_json") or "[]")
                except Exception:
                    d["material_divergences"] = []
                try:
                    d["notes"] = json.loads(d.pop("notes_json") or "[]")
                except Exception:
                    d["notes"] = []
                result.append(d)
            return result
    except Exception:
        return []


def get_comparison_stats() -> dict[str, Any]:
    """Aggregate counts by classification for the dev endpoint summary."""
    try:
        with _db() as conn:
            rows = conn.execute(
                """
                SELECT classification, COUNT(*) as cnt
                FROM options_provider_comparisons
                GROUP BY classification
                ORDER BY cnt DESC
                """
            ).fetchall()
            total = conn.execute(
                "SELECT COUNT(*) FROM options_provider_comparisons"
            ).fetchone()[0]
            return {
                "total": total,
                "by_classification": {row["classification"]: row["cnt"] for row in rows},
            }
    except Exception:
        return {"total": 0, "by_classification": {}}
