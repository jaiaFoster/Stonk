"""SQLite audit cache for automatically discovered calendar opportunities."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import date, datetime, timezone
from typing import Any, Callable

from app import config

LogFn = Callable[[str], None]


def cache_calendar_opportunities(
    rows: list[dict[str, Any]] | None,
    *,
    run_id: str | None = None,
    log_print: LogFn | None = None,
) -> dict[str, Any]:
    logger = log_print or (lambda msg: None)
    result = {
        "source": "calendar_opportunity_cache_v1",
        "enabled": bool(config.CALENDAR_OPPORTUNITY_CACHE_ENABLED),
        "has_data": False,
        "recent": [],
        "summary": {"write_count": 0, "recent_count": 0},
        "errors": [],
    }
    if not result["enabled"]:
        result["errors"].append("CALENDAR_OPPORTUNITY_CACHE_ENABLED=false")
        return result

    try:
        path = str(config.CALENDAR_OPPORTUNITY_DB_PATH)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(path)
        try:
            conn.row_factory = sqlite3.Row
            _ensure_schema(conn)
            writes = 0
            for row in rows or []:
                if isinstance(row, dict) and _upsert(conn, row, run_id or uuid.uuid4().hex):
                    writes += 1
            conn.commit()
            recent = [
                dict(item)
                for item in conn.execute(
                    """
                    SELECT symbol, earnings_date, trade_type, final_verdict, main_blocker,
                           score, ranking_score, candidate_status, first_seen_at, last_seen_at,
                           seen_count, candle_provider, candle_quality, backtest_status
                    FROM calendar_opportunities
                    ORDER BY last_seen_at DESC
                    LIMIT ?
                    """,
                    (max(1, int(config.CALENDAR_OPPORTUNITY_CACHE_RECENT_LIMIT or 20)),),
                ).fetchall()
            ]
        finally:
            conn.close()
        result["recent"] = recent
        result["has_data"] = bool(recent)
        result["summary"] = {"write_count": writes, "recent_count": len(recent), "db_path": path}
        logger(f"Calendar Opportunity Cache: wrote/upserted {writes} row(s); recent={len(recent)}.")
    except Exception as exc:
        result["errors"].append(str(exc))
        logger(f"Calendar Opportunity Cache failed: {exc}")
    return result


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS calendar_opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            natural_key TEXT NOT NULL UNIQUE,
            run_id TEXT,
            created_at TEXT,
            as_of_date TEXT,
            source TEXT,
            strategy TEXT,
            symbol TEXT,
            earnings_date TEXT,
            earnings_session TEXT,
            confirmed_timestamp INTEGER,
            trade_type TEXT,
            final_verdict TEXT,
            main_blocker TEXT,
            score REAL,
            ranking_score REAL,
            candidate_status TEXT,
            short_expiration TEXT,
            long_expiration TEXT,
            strike REAL,
            option_type TEXT,
            estimated_debit REAL,
            max_risk REAL,
            max_profit REAL,
            reward_risk REAL,
            liquidity_status TEXT,
            candle_provider TEXT,
            candle_quality TEXT,
            backtest_status TEXT,
            payload_json TEXT,
            first_seen_at TEXT,
            last_seen_at TEXT,
            seen_count INTEGER NOT NULL DEFAULT 1
        )
        """
    )


def _upsert(conn: sqlite3.Connection, row: dict[str, Any], run_id: str) -> bool:
    ticker = str(row.get("ticker") or row.get("symbol") or "").upper().strip()
    if not ticker:
        return False
    earnings = row.get("earnings") if isinstance(row.get("earnings"), dict) else {}
    spread = row.get("possible_spread") if isinstance(row.get("possible_spread"), dict) else {}
    final = row.get("final_verdict") if isinstance(row.get("final_verdict"), dict) else {}
    candidate = row.get("candidate") if isinstance(row.get("candidate"), dict) else {}
    quality = row.get("candle_quality") if isinstance(row.get("candle_quality"), dict) else candidate.get("candle_quality") if isinstance(candidate.get("candle_quality"), dict) else {}
    now = datetime.now(timezone.utc).isoformat()
    earnings_date = str(earnings.get("earnings_date") or earnings.get("date") or row.get("earnings_date") or "")
    strategy = str(row.get("strategy") or "earnings_calendar")
    strike = spread.get("strike") if spread.get("strike") is not None else row.get("strike")
    short_expiration = spread.get("short_expiration") or row.get("short_expiration")
    long_expiration = spread.get("long_expiration") or row.get("long_expiration")
    option_type = spread.get("option_type") or row.get("option_type") or "call"
    natural_key = "|".join(str(value or "") for value in (ticker, earnings_date, strategy, strike, short_expiration, long_expiration, option_type))
    values = {
        "natural_key": natural_key,
        "run_id": run_id,
        "created_at": now,
        "as_of_date": date.today().isoformat(),
        "source": str(row.get("source") or "unified_calendar_trade_engine_v1"),
        "strategy": strategy,
        "symbol": ticker,
        "earnings_date": earnings_date,
        "earnings_session": str(earnings.get("session_label") or row.get("earnings_session") or ""),
        "confirmed_timestamp": 1 if earnings.get("is_timestamp_confirmed") else 0,
        "trade_type": str(row.get("trade_type") or final.get("trade_type") or ""),
        "final_verdict": str(row.get("verdict") or final.get("final_verdict") or row.get("action") or ""),
        "main_blocker": str(row.get("main_blocker") or final.get("main_blocker") or ""),
        "score": _num(row.get("score")),
        "ranking_score": _num(row.get("rank_score") or row.get("ranking_score")),
        "candidate_status": _status(row),
        "short_expiration": str(short_expiration or ""),
        "long_expiration": str(long_expiration or ""),
        "strike": _num(strike),
        "option_type": str(option_type),
        "estimated_debit": _num(spread.get("conservative_debit") or spread.get("mid_debit") or row.get("estimated_debit")),
        "max_risk": _num(row.get("max_risk")),
        "max_profit": _num(row.get("max_profit")),
        "reward_risk": _num(row.get("reward_risk")),
        "liquidity_status": str(row.get("liquidity_status") or ""),
        "candle_provider": str(quality.get("selected_provider") or row.get("candle_provider") or ""),
        "candle_quality": str(quality.get("confidence") or "missing"),
        "backtest_status": str(row.get("backtest_mode") or row.get("backtest_status") or final.get("backtest_status") or ""),
        "payload_json": json.dumps(row, separators=(",", ":"), default=str),
        "first_seen_at": now,
        "last_seen_at": now,
    }
    columns = ", ".join(values)
    placeholders = ", ".join(f":{key}" for key in values)
    updates = ", ".join(
        f"{key}=excluded.{key}"
        for key in values
        if key not in {"natural_key", "first_seen_at", "created_at"}
    )
    conn.execute(
        f"""
        INSERT INTO calendar_opportunities ({columns}, seen_count)
        VALUES ({placeholders}, 1)
        ON CONFLICT(natural_key) DO UPDATE SET {updates}, seen_count=calendar_opportunities.seen_count + 1
        """,
        values,
    )
    return True


def _status(row: dict[str, Any]) -> str:
    verdict = str(row.get("verdict") or row.get("action") or "").upper()
    if "PASS" in verdict:
        return "passed"
    if "FAIL" in verdict or "REJECT" in verdict:
        return "blocked"
    return "watch"


def _num(value: Any) -> float | None:
    try:
        return None if value in {None, ""} else float(value)
    except (TypeError, ValueError):
        return None
