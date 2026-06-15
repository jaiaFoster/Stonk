"""Scanner-generated audit cache for Strategy 2 opportunities."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import Any, Callable

from app import config

LogFn = Callable[[str], None]


def cache_skew_momentum_vertical_opportunities(rows: list[dict[str, Any]] | None, log_print: LogFn | None = None) -> dict[str, Any]:
    logger = log_print or (lambda msg: None)
    result = {"source": "skew_momentum_vertical_cache_v1", "enabled": bool(config.SKEW_VERTICAL_OPPORTUNITY_CACHE_ENABLED), "has_data": False, "recent": [], "summary": {}, "errors": []}
    if not result["enabled"]:
        return result
    try:
        path = str(config.SKEW_VERTICAL_OPPORTUNITY_DB_PATH)
        if os.path.dirname(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.row_factory = sqlite3.Row
            _ensure_schema(conn)
            writes = 0
            for row in rows or []:
                if not isinstance(row, dict) or not row.get("ticker"):
                    continue
                _upsert(conn, row)
                writes += 1
            recent = [dict(item) for item in conn.execute(
                "SELECT strategy_id, ticker, direction, expiration, long_strike, short_strike, option_type, final_verdict, display_state, score, main_blocker, primary_reason, first_seen_at, last_seen_at, seen_count, payload_json FROM skew_vertical_opportunities ORDER BY last_seen_at DESC LIMIT ?",
                (max(1, int(config.SKEW_VERTICAL_OPPORTUNITY_CACHE_RECENT_LIMIT)),),
            ).fetchall()]
        for row in recent:
            row["payload"] = json.loads(row.pop("payload_json") or "{}")
        result.update({"has_data": bool(recent), "recent": recent, "summary": {"write_count": writes, "recent_count": len(recent), "db_path": path}})
        logger(f"Strategy 2 opportunity cache wrote {writes} row(s); recent={len(recent)}.")
    except Exception as exc:
        result["errors"].append(str(exc))
        logger(f"Strategy 2 opportunity cache failed: {exc}")
    return result


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS skew_vertical_opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            natural_key TEXT NOT NULL UNIQUE,
            strategy_id TEXT,
            ticker TEXT,
            direction TEXT,
            expiration TEXT,
            long_strike REAL,
            short_strike REAL,
            option_type TEXT,
            final_verdict TEXT,
            display_state TEXT,
            score REAL,
            main_blocker TEXT,
            primary_reason TEXT,
            first_seen_at TEXT,
            last_seen_at TEXT,
            seen_count INTEGER NOT NULL DEFAULT 1,
            payload_json TEXT
        )
    """)


def _upsert(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    spread = row.get("possible_spread") if isinstance(row.get("possible_spread"), dict) else {}
    natural_key = "|".join(str(value or "") for value in (
        "skew_momentum_vertical", row.get("ticker"), row.get("direction"), spread.get("expiration"),
        spread.get("long_strike"), spread.get("short_strike"), spread.get("option_type"),
    ))
    now = datetime.now(timezone.utc).isoformat()
    values = {
        "natural_key": natural_key,
        "strategy_id": "skew_momentum_vertical",
        "ticker": str(row.get("ticker") or "").upper(),
        "direction": str(row.get("direction") or ""),
        "expiration": str(spread.get("expiration") or ""),
        "long_strike": _num(spread.get("long_strike")),
        "short_strike": _num(spread.get("short_strike")),
        "option_type": str(spread.get("option_type") or ""),
        "final_verdict": str(row.get("verdict") or ""),
        "display_state": str(row.get("display_state") or ""),
        "score": _num(row.get("score")),
        "main_blocker": str(row.get("primary_blocker") or ""),
        "primary_reason": str(row.get("primary_reason") or ""),
        "first_seen_at": now,
        "last_seen_at": now,
        "payload_json": json.dumps(row, default=str, separators=(",", ":")),
    }
    columns = ", ".join(values)
    placeholders = ", ".join(f":{key}" for key in values)
    updates = ", ".join(f"{key}=excluded.{key}" for key in values if key not in {"natural_key", "first_seen_at"})
    conn.execute(
        f"INSERT INTO skew_vertical_opportunities ({columns}, seen_count) VALUES ({placeholders}, 1) ON CONFLICT(natural_key) DO UPDATE SET {updates}, seen_count=skew_vertical_opportunities.seen_count + 1",
        values,
    )


def _num(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None
