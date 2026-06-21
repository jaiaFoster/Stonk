"""
app/services/skew_threshold_analysis_service.py — TKT-045 MP2 Item 2.

Read-only, provider-free. Reads skew diagnostic data from stored report
snapshots and aggregates stats for threshold decision-making.
"""

from __future__ import annotations

import json
import sqlite3
import zlib
from datetime import datetime, timezone
from typing import Any

from app import config


def build_skew_threshold_analysis(max_runs: int = 10) -> dict[str, Any]:
    """Aggregate skew candidate data across recent completed runs."""
    from app.services.report_snapshot_service import ReportSnapshotRepository

    repo = ReportSnapshotRepository(log_print=lambda _: None)
    snapshots = _load_recent_snapshots(repo, max_runs)

    if not snapshots:
        return {
            "status": "no_data",
            "provider_calls_triggered": False,
            "read_only": True,
            "message": "No completed report snapshots found.",
        }

    current_threshold = float(getattr(config, "SKEW_RICHNESS_THRESHOLD", 12.5))
    all_candidates: list[dict[str, Any]] = []
    runs_analyzed = 0

    for snap in snapshots:
        run_id = snap.get("run_id", "unknown")
        completed_at = snap.get("completed_at", "")
        rows = _extract_skew_rows(snap)
        if rows is None:
            continue
        runs_analyzed += 1
        for row in rows:
            verdict = str(row.get("verdict") or "")
            if not verdict.startswith("WATCH") and not verdict.startswith("FAIL"):
                continue
            all_candidates.append({
                "run_id": run_id,
                "completed_at": completed_at,
                "ticker": row.get("ticker"),
                "verdict": verdict,
                "raw_skew_score": row.get("raw_skew_score"),
                "adjusted_skew_score": row.get("adjusted_skew_score"),
                "skew_gap_to_pass": row.get("skew_gap_to_pass"),
                "would_pass_at_threshold": row.get("would_pass_at_threshold"),
                "skew_pass": row.get("skew_pass"),
                "momentum_confirmed": row.get("momentum_confirmed"),
                "liquidity_pass": row.get("liquidity_pass"),
            })

    ticker_history = _build_ticker_history(all_candidates)
    what_if = _what_if_analysis(all_candidates, current_threshold)

    adj_scores = [c["adjusted_skew_score"] for c in all_candidates if c.get("adjusted_skew_score") is not None]
    gaps = [c["skew_gap_to_pass"] for c in all_candidates if c.get("skew_gap_to_pass") is not None]

    return {
        "status": "ok",
        "provider_calls_triggered": False,
        "read_only": True,
        "current_threshold": current_threshold,
        "runs_analyzed": runs_analyzed,
        "total_watch_fail_candidates": len(all_candidates),
        "stats": {
            "avg_adjusted_skew_score": round(sum(adj_scores) / len(adj_scores), 2) if adj_scores else None,
            "min_adjusted_skew_score": round(min(adj_scores), 2) if adj_scores else None,
            "max_adjusted_skew_score": round(max(adj_scores), 2) if adj_scores else None,
            "avg_gap_to_pass": round(sum(gaps) / len(gaps), 2) if gaps else None,
            "min_gap_to_pass": round(min(gaps), 2) if gaps else None,
        },
        "what_if": what_if,
        "ticker_history": ticker_history,
        "candidates": all_candidates,
    }


def _load_recent_snapshots(repo, limit: int) -> list[dict[str, Any]]:
    """Load last N completed snapshots with full data."""
    db_path = repo.db_path
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM report_snapshots WHERE status='complete' AND schema_version=? "
            "ORDER BY completed_at DESC LIMIT ?",
            (repo.SCHEMA_VERSION, limit),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _extract_skew_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Extract skew strategy rows from a snapshot's stored data."""
    tradier = _get_tradier(snapshot)
    if not tradier:
        return None
    strategies = tradier.get("_strategy_results", {}) or {}
    skew = strategies.get("skew_momentum_vertical", {}) or {}
    rows = skew.get("rows") or skew.get("items") or []
    if not rows:
        skew_direct = tradier.get("_skew_momentum_vertical_strategy", {}) or {}
        rows = skew_direct.get("rows") or skew_direct.get("items") or []
    return rows if rows else None


def _get_tradier(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Decompress and extract tradier data from snapshot."""
    raw_blob = snapshot.get("raw_provider_blob")
    if raw_blob:
        try:
            data = json.loads(zlib.decompress(raw_blob).decode("utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    full_blob = snapshot.get("full_summary_blob")
    if full_blob:
        try:
            summary = json.loads(zlib.decompress(full_blob).decode("utf-8"))
            return ((summary or {}).get("report_data") or {}).get("tradier_snapshot") or {}
        except Exception:
            pass
    summary_json = snapshot.get("summary_json")
    if summary_json:
        try:
            summary = json.loads(summary_json)
            return ((summary or {}).get("report_data") or {}).get("tradier_snapshot") or {}
        except Exception:
            pass
    return {}


def _build_ticker_history(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group candidates by ticker, count consecutive WATCH appearances."""
    by_ticker: dict[str, list[dict[str, Any]]] = {}
    for c in candidates:
        t = c.get("ticker")
        if t:
            by_ticker.setdefault(t, []).append(c)

    result = []
    for ticker, entries in sorted(by_ticker.items()):
        watch_count = sum(1 for e in entries if str(e.get("verdict", "")).startswith("WATCH"))
        fail_count = sum(1 for e in entries if str(e.get("verdict", "")).startswith("FAIL"))
        adj_scores = [e["adjusted_skew_score"] for e in entries if e.get("adjusted_skew_score") is not None]
        result.append({
            "ticker": ticker,
            "appearances": len(entries),
            "watch_count": watch_count,
            "fail_count": fail_count,
            "avg_adjusted_skew_score": round(sum(adj_scores) / len(adj_scores), 2) if adj_scores else None,
            "best_adjusted_skew_score": round(max(adj_scores), 2) if adj_scores else None,
            "closest_to_pass": round(min(e.get("skew_gap_to_pass") or 999 for e in entries), 2),
        })
    result.sort(key=lambda r: r.get("closest_to_pass") or 999)
    return result


def _what_if_analysis(candidates: list[dict[str, Any]], current_threshold: float) -> list[dict[str, Any]]:
    """Show how many more candidates would pass at various lower thresholds."""
    reductions = [1.0, 2.0, 3.0, 5.0]
    results = []
    for reduction in reductions:
        new_threshold = current_threshold - reduction
        if new_threshold < 0:
            continue
        would_pass = [
            c for c in candidates
            if c.get("adjusted_skew_score") is not None
            and c["adjusted_skew_score"] >= new_threshold
        ]
        unique_tickers = set(c.get("ticker") for c in would_pass)
        results.append({
            "threshold": round(new_threshold, 1),
            "reduction": reduction,
            "additional_passes": len(would_pass),
            "unique_tickers": len(unique_tickers),
            "tickers": sorted(unique_tickers - {None}),
        })
    return results
