"""Generic scanner-generated strategy opportunity history."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import config


class StrategyOpportunityRepository:
    SCHEMA_VERSION = 1

    def __init__(self, db_path: str | None = None):
        self.db_path = str(db_path or config.STRATEGY_OPPORTUNITY_DB_PATH)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS strategy_opportunities (
                strategy_id TEXT, structure_key TEXT, strategy_version TEXT, ticker TEXT, direction TEXT,
                expiration TEXT, verdict TEXT, display_state TEXT, score REAL, primary_reason TEXT,
                primary_blocker TEXT, payload_json TEXT, first_seen_at TEXT, last_seen_at TEXT, seen_count INTEGER,
                schema_version INTEGER DEFAULT 1, PRIMARY KEY(strategy_id, structure_key))""")
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(strategy_opportunities)").fetchall()}
            if "schema_version" not in columns:
                conn.execute("ALTER TABLE strategy_opportunities ADD COLUMN schema_version INTEGER DEFAULT 1")

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=5)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def upsert_results(self, results: dict[str, dict[str, Any]]) -> int:
        count = 0
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            for strategy_id, result in results.items():
                for index, row in enumerate(result.get("rows", []) or []):
                    ticker = str(row.get("ticker") or row.get("symbol") or "UNKNOWN")
                    key = str(row.get("structure_key") or row.get("possible_spread") or row.get("structure") or f"{ticker}:{index}")
                    verdict = str(row.get("final_verdict") or row.get("verdict") or row.get("action") or "UNKNOWN")
                    conn.execute("""INSERT INTO strategy_opportunities
                        (strategy_id,structure_key,strategy_version,ticker,direction,expiration,verdict,display_state,score,
                         primary_reason,primary_blocker,payload_json,first_seen_at,last_seen_at,seen_count,schema_version)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(strategy_id,structure_key) DO UPDATE SET verdict=excluded.verdict,
                        display_state=excluded.display_state, score=excluded.score, primary_reason=excluded.primary_reason,
                        primary_blocker=excluded.primary_blocker, payload_json=excluded.payload_json,
                        last_seen_at=excluded.last_seen_at, seen_count=strategy_opportunities.seen_count+1""",
                        (strategy_id, key, result.get("version", "v1"), ticker, row.get("direction"), row.get("expiration"),
                         verdict, _display_state(verdict), row.get("score"), row.get("primary_reason") or row.get("why"),
                         row.get("primary_blocker"), json.dumps(row, default=str), now, now, 1, self.SCHEMA_VERSION))
                    count += 1
        return count

    def recent(self, limit: int = 20, strategy_id: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if strategy_id:
                rows = conn.execute(
                    "SELECT * FROM strategy_opportunities WHERE strategy_id=? ORDER BY last_seen_at DESC LIMIT ?",
                    (strategy_id, limit),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM strategy_opportunities ORDER BY last_seen_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]


def _display_state(verdict: str) -> str:
    upper = verdict.upper()
    if upper.startswith("PASS") or "CONSIDER ADDING" in upper:
        return "PASS"
    if "WATCH" in upper or "RESEARCH" in upper:
        return "WATCH"
    if "SKIPPED" in upper or "DATA CAP" in upper:
        return "SKIPPED"
    return "FAIL"
