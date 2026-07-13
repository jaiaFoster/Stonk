"""ASA Patch 33A — Strategy Opportunity History

Cross-run time-series table that records every strategy observation so ASA
can compute how opportunities evolve across runs and days.

Design principles:
- Append-only: one row per (run_id, strategy_id, observation_key). No upserts.
- Deterministic observation_key per spec (strategy+ticker for stocks;
  strategy+ticker+option_type+strike+front_exp+back_exp for calendar structures).
- Same run cannot produce duplicate rows (UNIQUE index on run_id+strategy_id+observation_key).
- Schema is additive — new columns are added via ALTER TABLE.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from app import config

HISTORY_SCHEMA_VERSION = "33A.v1"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS strategy_opportunity_history (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_key             TEXT    NOT NULL,
    strategy_id                 TEXT    NOT NULL,
    ticker                      TEXT    NOT NULL,
    structure_key               TEXT,
    run_id                      TEXT    NOT NULL,
    observed_at                 TEXT    NOT NULL,
    trading_date                TEXT    NOT NULL,
    row_type                    TEXT,
    lifecycle_stage             TEXT,
    verdict                     TEXT,
    friendly_verdict            TEXT,
    score                       REAL,
    rank                        INTEGER,
    daily_opportunity_eligible  INTEGER DEFAULT 0,
    journal_eligible            INTEGER DEFAULT 0,
    primary_reason              TEXT,
    reason_codes_json           TEXT,
    metrics_json                TEXT,
    data_confidence             TEXT,
    conflict_count              INTEGER DEFAULT 0,
    freshness_timestamp         TEXT,
    source_row_id               TEXT,
    schema_version              TEXT    DEFAULT '33A.v1',
    recommendation_mode         TEXT,
    created_at                  TEXT    DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_soh_run_dedup
    ON strategy_opportunity_history (run_id, strategy_id, observation_key);

CREATE INDEX IF NOT EXISTS idx_soh_key_date
    ON strategy_opportunity_history (observation_key, observed_at);

CREATE INDEX IF NOT EXISTS idx_soh_strategy_ticker_date
    ON strategy_opportunity_history (strategy_id, ticker, observed_at);

CREATE INDEX IF NOT EXISTS idx_soh_trading_date
    ON strategy_opportunity_history (trading_date, strategy_id);

CREATE INDEX IF NOT EXISTS idx_soh_run_id
    ON strategy_opportunity_history (run_id);

CREATE INDEX IF NOT EXISTS idx_soh_lifecycle
    ON strategy_opportunity_history (lifecycle_stage);

CREATE INDEX IF NOT EXISTS idx_soh_verdict
    ON strategy_opportunity_history (verdict);
"""


def _db_path() -> str:
    return str(getattr(config, "OPPORTUNITY_HISTORY_DB_PATH", "data/strategy_opportunity_history.db"))


@contextmanager
def _connect(db_path: str | None = None):
    path = db_path or _db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA journal_mode=WAL")
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_schema(db_path: str | None = None) -> None:
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA)


def _observation_key(strategy_id: str, row: dict[str, Any]) -> str:
    """Deterministic identity per spec:
    - Stock strategies: strategy_id + ticker
    - Calendar with structure: strategy_id + ticker + option_type + strike + front_exp + back_exp
    - Pre-window (no structure): strategy_id + ticker + earnings_date
    """
    sid = str(strategy_id).lower().strip()
    ticker = str(row.get("ticker") or row.get("symbol") or "UNKNOWN").upper().strip()
    front = str(row.get("front_expiration") or row.get("proposed_short_expiration") or "")[:10]
    back = str(row.get("back_expiration") or row.get("proposed_long_expiration") or "")[:10]
    option_type = str(row.get("direction") or row.get("option_type") or row.get("structure_type") or "").lower().strip()
    strike = str(row.get("strike") or row.get("short_strike") or row.get("put_strike") or "")

    if front and back:
        # Calendar structure identity
        return f"{sid}:{ticker}:{option_type}:{strike}:{front}:{back}"
    elif front:
        # Pre-window — no back expiration yet
        earnings_date = str(row.get("earnings_date") or row.get("event_date") or "")[:10]
        return f"{sid}:{ticker}:pre_window:{earnings_date}:{front}"
    else:
        # Stock-style or no structure
        earnings_date = str(row.get("earnings_date") or row.get("event_date") or "")[:10]
        if earnings_date and sid in {"earnings_calendar"}:
            return f"{sid}:{ticker}:{earnings_date}"
        return f"{sid}:{ticker}"


def _lifecycle_stage(row: dict[str, Any]) -> str:
    """Extract or infer lifecycle stage from a strategy row."""
    explicit = str(row.get("lifecycle_stage") or row.get("discovery_stage") or "")
    if explicit:
        return explicit
    entry_status = str(row.get("entry_window_status") or "")
    if entry_status:
        return entry_status
    verdict = str(row.get("final_verdict") or row.get("verdict") or row.get("action") or "").upper()
    if verdict.startswith("PASS"):
        return "PASS"
    if "WATCH" in verdict:
        return "WATCH"
    if "FAIL" in verdict or "REJECT" in verdict:
        return "FAIL"
    return "UNKNOWN"


def _trading_date(run_date: str | None = None) -> str:
    if run_date:
        return str(run_date)[:10]
    return date.today().isoformat()


def write_run(
    run_id: str,
    strategy_results: dict[str, dict[str, Any]],
    run_date: str | None = None,
    db_path: str | None = None,
) -> dict[str, int]:
    """Write one history row per strategy row per run. Returns counts.

    Idempotent: duplicate (run_id, strategy_id, observation_key) is silently ignored.
    """
    _ensure_schema(db_path)
    now = datetime.now(timezone.utc).isoformat()
    trading = _trading_date(run_date)
    rows_attempted = 0
    rows_written = 0
    first_observations = 0
    stage_transitions = 0
    verdict_transitions = 0

    with _connect(db_path) as conn:
        for strategy_id, result in (strategy_results or {}).items():
            if not isinstance(result, dict):
                continue
            for row in (result.get("rows") or result.get("items") or []):
                if not isinstance(row, dict):
                    continue
                rows_attempted += 1
                obs_key = _observation_key(strategy_id, row)
                score = row.get("score")
                score_float = float(score) if score is not None else None
                verdict = str(row.get("final_verdict") or row.get("verdict") or row.get("action") or "")
                lifecycle = _lifecycle_stage(row)
                metrics = {}
                for k in ("score", "iv_edge", "front_iv", "back_iv", "forward_factor",
                          "miss_distance", "entry_window_front_dte", "days_until_entry_window",
                          "earnings_days_away", "valid_pair_count", "debit", "spread_pct"):
                    v = row.get(k)
                    if v is not None:
                        metrics[k] = v
                reason_codes = row.get("reason_codes") or row.get("reasons") or []
                if isinstance(reason_codes, str):
                    reason_codes = [reason_codes]

                # Check if this is a first observation for this key
                existing = conn.execute(
                    "SELECT COUNT(*) FROM strategy_opportunity_history WHERE observation_key=? AND strategy_id=?",
                    (obs_key, strategy_id),
                ).fetchone()[0]
                if existing == 0:
                    first_observations += 1

                # Check verdict transition (compare to most recent prior observation)
                if existing > 0:
                    prior = conn.execute(
                        "SELECT verdict, lifecycle_stage FROM strategy_opportunity_history "
                        "WHERE observation_key=? AND strategy_id=? AND run_id!=? "
                        "ORDER BY observed_at DESC LIMIT 1",
                        (obs_key, strategy_id, run_id),
                    ).fetchone()
                    if prior:
                        if prior["verdict"] != verdict:
                            verdict_transitions += 1
                        if prior["lifecycle_stage"] != lifecycle:
                            stage_transitions += 1

                try:
                    cur = conn.execute(
                        """INSERT OR IGNORE INTO strategy_opportunity_history
                        (observation_key, strategy_id, ticker, structure_key, run_id,
                         observed_at, trading_date, row_type, lifecycle_stage, verdict,
                         friendly_verdict, score, rank, daily_opportunity_eligible,
                         journal_eligible, primary_reason, reason_codes_json,
                         metrics_json, data_confidence, conflict_count, freshness_timestamp,
                         source_row_id, schema_version, recommendation_mode)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            obs_key,
                            strategy_id,
                            str(row.get("ticker") or row.get("symbol") or "UNKNOWN").upper().strip(),
                            str(row.get("structure_key") or ""),
                            run_id,
                            now,
                            trading,
                            str(row.get("row_type") or ""),
                            lifecycle,
                            verdict,
                            str(row.get("friendly_verdict") or ""),
                            score_float,
                            row.get("rank"),
                            int(bool(row.get("daily_opportunity_eligible"))),
                            int(bool(row.get("journal_eligible"))),
                            str(row.get("primary_reason") or row.get("why") or ""),
                            json.dumps(list(reason_codes), default=str),
                            json.dumps(metrics, default=str),
                            str(row.get("data_confidence") or row.get("date_confidence") or ""),
                            int(row.get("conflict_count") or 0),
                            str(row.get("freshness_timestamp") or row.get("quote_timestamp") or ""),
                            str(row.get("row_id") or ""),
                            HISTORY_SCHEMA_VERSION,
                            str(row.get("recommendation_mode") or ""),
                        ),
                    )
                    # rowcount=0 when INSERT OR IGNORE silently skipped a duplicate
                    if cur.rowcount > 0:
                        rows_written += 1
                except sqlite3.IntegrityError:
                    pass  # Duplicate in same run — skip silently

    return {
        "rows_attempted": rows_attempted,
        "rows_written": rows_written,
        "first_observations": first_observations,
        "stage_transitions": stage_transitions,
        "verdict_transitions": verdict_transitions,
    }


def get_history(
    observation_key: str,
    strategy_id: str,
    limit: int = 30,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Return ordered history for a single opportunity."""
    _ensure_schema(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM strategy_opportunity_history "
            "WHERE observation_key=? AND strategy_id=? "
            "ORDER BY observed_at DESC LIMIT ?",
            (observation_key, strategy_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_recent_history(
    strategy_id: str | None = None,
    ticker: str | None = None,
    trading_date: str | None = None,
    limit: int = 100,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Recent observations filtered by strategy/ticker/date."""
    _ensure_schema(db_path)
    where: list[str] = []
    params: list[Any] = []
    if strategy_id:
        where.append("strategy_id=?")
        params.append(strategy_id)
    if ticker:
        where.append("ticker=?")
        params.append(ticker.upper().strip())
    if trading_date:
        where.append("trading_date=?")
        params.append(trading_date[:10])
    sql = "SELECT * FROM strategy_opportunity_history"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY observed_at DESC LIMIT ?"
    params.append(min(limit, 500))
    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def compute_evolution(
    current_row: dict[str, Any],
    strategy_id: str,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Compute evolution fields by comparing to prior observations.

    Returns an `evolution` dict with score change, trend, lifecycle/verdict transitions,
    first/last seen, and a human-readable trend_summary.
    """
    _ensure_schema(db_path)
    obs_key = _observation_key(strategy_id, current_row)
    current_score = current_row.get("score")
    current_score_f = float(current_score) if current_score is not None else None
    current_trading_date = str(current_row.get("trading_date") or date.today().isoformat())[:10]

    with _connect(db_path) as conn:
        # All prior observations (not same run_id, ordered newest first)
        run_id = str(current_row.get("run_id") or "")
        all_prior = conn.execute(
            "SELECT * FROM strategy_opportunity_history "
            "WHERE observation_key=? AND strategy_id=? "
            "ORDER BY observed_at DESC LIMIT 60",
            (obs_key, strategy_id),
        ).fetchall()
        all_prior = [dict(r) for r in all_prior if r["run_id"] != run_id]

    if not all_prior:
        return {
            "first_seen_at": None,
            "last_seen_at": None,
            "observation_count": 0,
            "previous_score": None,
            "score_change_1_run": None,
            "score_change_1_day": None,
            "score_change_2_weeks": None,
            "percent_change_1_day": None,
            "percent_change_2_weeks": None,
            "rank_previous": None,
            "rank_change": None,
            "highest_score": current_score_f,
            "highest_score_at": None,
            "lowest_score": current_score_f,
            "lifecycle_previous_stage": None,
            "lifecycle_stage_changed": False,
            "verdict_previous": None,
            "verdict_changed": False,
            "trend_direction": "new",
            "trend_summary": "First observation — no prior history available.",
            "newly_discovered": True,
            "reappeared_after_absence": False,
            "improving_run_count": 0,
            "deteriorating_run_count": 0,
            "current_streak_direction": "new",
        }

    # First/last
    first_seen = min(r["observed_at"] for r in all_prior if r.get("observed_at")) or None
    last_seen = max(r["observed_at"] for r in all_prior if r.get("observed_at")) or None
    obs_count = len(all_prior)

    # Previous score (most recent prior)
    prev_row = all_prior[0]
    prev_score = prev_row.get("score")
    prev_score_f = float(prev_score) if prev_score is not None else None

    # Highest/lowest
    scores = [float(r["score"]) for r in all_prior if r.get("score") is not None]
    if current_score_f is not None:
        scores.append(current_score_f)
    highest_score = max(scores) if scores else None
    lowest_score = min(scores) if scores else None
    highest_score_at = None
    if highest_score is not None:
        for r in all_prior:
            if r.get("score") is not None and abs(float(r["score"]) - highest_score) < 0.001:
                highest_score_at = r.get("observed_at")
                break

    # Score change
    score_change_1_run = None
    if current_score_f is not None and prev_score_f is not None:
        score_change_1_run = round(current_score_f - prev_score_f, 2)

    # 1-day and 2-week changes (find closest prior by trading_date)
    score_change_1_day = _score_change_by_days(current_score_f, all_prior, current_trading_date, 1)
    score_change_2_weeks = _score_change_by_days(current_score_f, all_prior, current_trading_date, 14)

    pct_1d = None
    pct_2w = None
    if score_change_1_day is not None and prev_score_f and prev_score_f != 0:
        pct_1d = round(score_change_1_day / abs(prev_score_f) * 100, 1)
    if score_change_2_weeks is not None and prev_score_f and prev_score_f != 0:
        pct_2w = round(score_change_2_weeks / abs(prev_score_f) * 100, 1)

    # Rank change
    rank_prev = prev_row.get("rank")
    rank_current = current_row.get("rank")
    rank_change = None
    if rank_current is not None and rank_prev is not None:
        rank_change = int(rank_current) - int(rank_prev)

    # Lifecycle and verdict transitions
    lifecycle_prev = str(prev_row.get("lifecycle_stage") or "")
    lifecycle_current = _lifecycle_stage(current_row)
    lifecycle_changed = lifecycle_prev != lifecycle_current and bool(lifecycle_prev)

    verdict_prev = str(prev_row.get("verdict") or "")
    verdict_current = str(current_row.get("final_verdict") or current_row.get("verdict") or "")
    verdict_changed = verdict_prev != verdict_current and bool(verdict_prev)

    # Trend: look at last 5 score-change runs
    trend_dir = "stable"
    improving = 0
    deteriorating = 0
    streak = "stable"
    prior_with_scores = [r for r in all_prior if r.get("score") is not None]
    if current_score_f is not None and prior_with_scores:
        for i, r in enumerate(prior_with_scores[:5]):
            r_score = float(r["score"])
            ref = current_score_f if i == 0 else float(prior_with_scores[i - 1]["score"])
            diff = (current_score_f if i == 0 else ref) - r_score
            if diff > 0.5:
                improving += 1
            elif diff < -0.5:
                deteriorating += 1
        if improving > deteriorating:
            trend_dir = "improving"
            streak = "improving"
        elif deteriorating > improving:
            trend_dir = "deteriorating"
            streak = "deteriorating"

    # Human-readable trend summary
    ticker = str(current_row.get("ticker") or "UNKNOWN").upper()
    trend_summary_parts: list[str] = []
    if score_change_1_day is not None and abs(score_change_1_day) >= 0.5:
        direction = "increased" if score_change_1_day > 0 else "decreased"
        trend_summary_parts.append(
            f"{ticker} score {direction} {abs(score_change_1_day):.1f} points since yesterday."
        )
    if score_change_2_weeks is not None and abs(score_change_2_weeks) >= 0.5:
        direction = "up" if score_change_2_weeks > 0 else "down"
        trend_summary_parts.append(
            f"Score {direction} {abs(score_change_2_weeks):.1f} over two weeks."
        )
    if verdict_changed:
        trend_summary_parts.append(f"{ticker} verdict changed from {verdict_prev!r} to {verdict_current!r}.")
    if lifecycle_changed:
        trend_summary_parts.append(f"Discovery stage changed from {lifecycle_prev!r} to {lifecycle_current!r}.")
    if not trend_summary_parts:
        trend_summary_parts.append(f"No significant change since last observation ({obs_count} prior).")

    trend_summary = " ".join(trend_summary_parts)

    return {
        "first_seen_at": first_seen,
        "last_seen_at": last_seen,
        "observation_count": obs_count,
        "previous_score": prev_score_f,
        "score_change_1_run": score_change_1_run,
        "score_change_1_day": score_change_1_day,
        "score_change_2_weeks": score_change_2_weeks,
        "percent_change_1_day": pct_1d,
        "percent_change_2_weeks": pct_2w,
        "rank_previous": rank_prev,
        "rank_change": rank_change,
        "highest_score": highest_score,
        "highest_score_at": highest_score_at,
        "lowest_score": lowest_score,
        "lifecycle_previous_stage": lifecycle_prev or None,
        "lifecycle_stage_changed": lifecycle_changed,
        "verdict_previous": verdict_prev or None,
        "verdict_changed": verdict_changed,
        "trend_direction": trend_dir,
        "trend_summary": trend_summary,
        "newly_discovered": first_seen is None,
        "reappeared_after_absence": _detect_reappearance(all_prior, current_trading_date),
        "improving_run_count": improving,
        "deteriorating_run_count": deteriorating,
        "current_streak_direction": streak,
    }


def _score_change_by_days(
    current_score: float | None,
    prior_rows: list[dict[str, Any]],
    current_date: str,
    target_days: int,
) -> float | None:
    """Find the closest prior observation approximately `target_days` ago and compute change."""
    if current_score is None or not prior_rows:
        return None
    try:
        today = date.fromisoformat(current_date[:10])
    except (ValueError, TypeError):
        return None
    best: dict[str, Any] | None = None
    best_gap = 999
    for r in prior_rows:
        r_date_str = str(r.get("trading_date") or r.get("observed_at") or "")[:10]
        try:
            r_date = date.fromisoformat(r_date_str)
        except (ValueError, TypeError):
            continue
        gap = abs((today - r_date).days - target_days)
        if gap < best_gap and r.get("score") is not None:
            best_gap = gap
            best = r
    if best is None or best_gap > max(target_days // 2, 3):
        return None
    prev_score = float(best["score"])
    return round(current_score - prev_score, 2)


def _detect_reappearance(prior_rows: list[dict[str, Any]], current_date: str) -> bool:
    """Return True if the most recent prior observation was more than 3 trading days ago."""
    if not prior_rows:
        return False
    last_str = str(prior_rows[0].get("trading_date") or prior_rows[0].get("observed_at") or "")[:10]
    try:
        last_date = date.fromisoformat(last_str)
        today = date.fromisoformat(current_date[:10])
        return (today - last_date).days > 3
    except (ValueError, TypeError):
        return False


def cleanup_old_observations(
    retention_days: int | None = None,
    db_path: str | None = None,
) -> int:
    """Delete observations older than retention_days. Returns count deleted."""
    _ensure_schema(db_path)
    days = retention_days or getattr(config, "OPPORTUNITY_HISTORY_RETENTION_DAYS", 180)
    with _connect(db_path) as conn:
        result = conn.execute(
            "DELETE FROM strategy_opportunity_history WHERE trading_date < date('now', ?)",
            (f"-{days} days",),
        )
        return result.rowcount
