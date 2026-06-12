"""Generic scanner-generated strategy opportunity history."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import config


class StrategyOpportunityRepository:
    def __init__(self, db_path: str | None = None):
        self.db_path = str(db_path or config.STRATEGY_OPPORTUNITY_DB_PATH)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS strategy_opportunities (
                strategy_id TEXT, structure_key TEXT, strategy_version TEXT, ticker TEXT, direction TEXT,
                expiration TEXT, verdict TEXT, display_state TEXT, score REAL, primary_reason TEXT,
                primary_blocker TEXT, payload_json TEXT, first_seen_at TEXT, last_seen_at TEXT, seen_count INTEGER,
                PRIMARY KEY(strategy_id, structure_key))""")

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def upsert_results(self, results: dict[str, dict[str, Any]]) -> int:
        count = 0
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            for strategy_id, result in results.items():
                for index, row in enumerate(result.get("rows", []) or []):
                    ticker = str(row.get("ticker") or row.get("symbol") or "UNKNOWN")
                    key = str(row.get("structure_key") or row.get("possible_spread") or row.get("structure") or f"{ticker}:{index}")
                    verdict = str(row.get("final_verdict") or row.get("verdict") or row.get("action") or "UNKNOWN")
                    conn.execute("""INSERT INTO strategy_opportunities VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(strategy_id,structure_key) DO UPDATE SET verdict=excluded.verdict,
                        display_state=excluded.display_state, score=excluded.score, primary_reason=excluded.primary_reason,
                        primary_blocker=excluded.primary_blocker, payload_json=excluded.payload_json,
                        last_seen_at=excluded.last_seen_at, seen_count=strategy_opportunities.seen_count+1""",
                        (strategy_id, key, result.get("version", "v1"), ticker, row.get("direction"), row.get("expiration"),
                         verdict, _display_state(verdict), row.get("score"), row.get("primary_reason") or row.get("why"),
                         row.get("primary_blocker"), json.dumps(row, default=str), now, now, 1))
                    count += 1
        return count

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
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
