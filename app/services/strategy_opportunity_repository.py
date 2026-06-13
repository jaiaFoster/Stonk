"""Generic scanner-generated strategy opportunity history."""

from __future__ import annotations

import json
import sqlite3
import hashlib
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
                schema_version INTEGER DEFAULT 1, last_run_id TEXT, PRIMARY KEY(strategy_id, structure_key))""")
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(strategy_opportunities)").fetchall()}
            if "schema_version" not in columns:
                conn.execute("ALTER TABLE strategy_opportunities ADD COLUMN schema_version INTEGER DEFAULT 1")
            if "last_run_id" not in columns:
                conn.execute("ALTER TABLE strategy_opportunities ADD COLUMN last_run_id TEXT")

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

    def upsert_results(self, results: dict[str, dict[str, Any]], run_id: str | None = None) -> int:
        count = 0
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            for strategy_id, result in results.items():
                for row in result.get("rows", []) or []:
                    if strategy_id == "forward_factor_calendar" and str(row.get("verdict") or "").upper().startswith("SKIPPED"):
                        continue
                    ticker = str(row.get("ticker") or row.get("symbol") or "UNKNOWN")
                    key = opportunity_structure_key(strategy_id, row)
                    verdict = str(row.get("final_verdict") or row.get("verdict") or row.get("action") or "UNKNOWN")
                    conn.execute("""INSERT INTO strategy_opportunities
                        (strategy_id,structure_key,strategy_version,ticker,direction,expiration,verdict,display_state,score,
                         primary_reason,primary_blocker,payload_json,first_seen_at,last_seen_at,seen_count,schema_version,last_run_id)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(strategy_id,structure_key) DO UPDATE SET verdict=excluded.verdict,
                        display_state=excluded.display_state, score=excluded.score, primary_reason=excluded.primary_reason,
                        primary_blocker=excluded.primary_blocker, payload_json=excluded.payload_json,
                        last_seen_at=excluded.last_seen_at, seen_count=strategy_opportunities.seen_count+1,
                        last_run_id=excluded.last_run_id""",
                        (strategy_id, key, result.get("version", "v1"), ticker, row.get("direction"), row.get("expiration"),
                         verdict, _display_state(verdict), row.get("score"), row.get("primary_reason") or row.get("why"),
                         row.get("primary_blocker"), json.dumps(row, default=str), now, now, 1, self.SCHEMA_VERSION, run_id))
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

    def observation_summary(self, strategy_id: str, limit: int = 50) -> dict[str, dict[str, Any]]:
        summary: dict[str, dict[str, Any]] = {}
        for record in self.recent(limit, strategy_id):
            try:
                payload = json.loads(record.get("payload_json") or "{}")
            except json.JSONDecodeError:
                payload = {}
            ticker = str(record.get("ticker") or "UNKNOWN")
            item = summary.setdefault(ticker, {
                "seen_count": 0, "best_diagnostic_ff": None, "best_liquidity_status": "NOT_EVALUATED",
                "last_positive_signal": None, "failure_modes": {}, "valid_pair_seen": False,
                "structure_seen": False, "liquidity_pass_seen": False,
                "last_run_id": None,
            })
            item["seen_count"] += int(record.get("seen_count") or 0)
            item["last_run_id"] = item["last_run_id"] or record.get("last_run_id")
            value = payload.get("diagnostic_raw_iv_forward_factor")
            if value is not None:
                item["best_diagnostic_ff"] = max(float(value), float(item["best_diagnostic_ff"] or value))
            liquidity = str(payload.get("liquidity_status") or "NOT_EVALUATED")
            if {"NOT_EVALUATED": 0, "FAIL": 1, "WATCH": 2, "PASS": 3}.get(liquidity, 0) > {"NOT_EVALUATED": 0, "FAIL": 1, "WATCH": 2, "PASS": 3}.get(item["best_liquidity_status"], 0):
                item["best_liquidity_status"] = liquidity
            if payload.get("is_positive_signal") and item["last_positive_signal"] is None:
                item["last_positive_signal"] = record.get("last_seen_at")
            verdict = str(payload.get("verdict") or record.get("verdict") or "").upper()
            mode = _ff_failure_mode(verdict)
            if mode:
                item["failure_modes"][mode] = item["failure_modes"].get(mode, 0) + int(record.get("seen_count") or 1)
            item["valid_pair_seen"] = bool(item["valid_pair_seen"] or (payload.get("front_expiration") and payload.get("back_expiration")))
            item["structure_seen"] = bool(item["structure_seen"] or payload.get("structure_status") == "COMPLETE")
            item["liquidity_pass_seen"] = bool(item["liquidity_pass_seen"] or str(payload.get("liquidity_status") or "").upper() == "PASS")
        return summary


def _display_state(verdict: str) -> str:
    upper = verdict.upper()
    if upper.startswith(("PASS", "DRY RUN PASS")) or "CONSIDER ADDING" in upper:
        return "PASS"
    if "WATCH" in upper or "RESEARCH" in upper:
        return "WATCH"
    if "SKIPPED" in upper or "DATA CAP" in upper:
        return "SKIPPED"
    return "FAIL"


def _ff_failure_mode(verdict: str) -> str | None:
    for text, mode in (
        ("NO ELIGIBLE EXPIRATION PAIR", "NO_ELIGIBLE_EXPIRATION_PAIR"),
        ("OPTIONS ILLIQUID", "OPTIONS_ILLIQUID"),
        ("PACKAGE SLIPPAGE TOO WIDE", "PACKAGE_SLIPPAGE_TOO_WIDE"),
        ("DELTA DATA UNAVAILABLE", "DELTA_DATA_UNAVAILABLE"),
        ("EX-EARNINGS IV UNAVAILABLE", "SOURCE_IV_UNAVAILABLE"),
    ):
        if text in verdict:
            return mode
    return None


def opportunity_structure_key(strategy_id: str, row: dict[str, Any]) -> str:
    spread = row.get("possible_spread") if isinstance(row.get("possible_spread"), dict) else {}
    identity = {
        "strategy_id": str(strategy_id).lower().strip(),
        "ticker": str(row.get("ticker") or row.get("symbol") or "UNKNOWN").upper().strip(),
        "direction": str(row.get("direction") or spread.get("direction") or "").lower().strip(),
        "structure_type": str(row.get("structure_type") or row.get("trade_type") or spread.get("option_type") or "").lower().strip(),
        "front_expiration": _date(row.get("front_expiration") or spread.get("front_expiration") or row.get("expiration")),
        "back_expiration": _date(row.get("back_expiration") or spread.get("back_expiration")),
        "long_strike": _number(row.get("long_strike") or spread.get("long_strike")),
        "short_strike": _number(row.get("short_strike") or spread.get("short_strike") or row.get("strike")),
        "put_strike": _number(row.get("put_strike")),
        "call_strike": _number(row.get("call_strike")),
        "formula_version": str(row.get("formula_version") or ""),
        "event_date": _date(row.get("event_date") or row.get("earnings_date")),
    }
    explicit = row.get("structure_key")
    if explicit:
        identity["explicit"] = str(explicit).strip()
    raw = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return f"{identity['ticker']}:{hashlib.sha256(raw.encode()).hexdigest()[:20]}"


def _date(value: Any) -> str:
    return str(value or "")[:10]


def _number(value: Any) -> str:
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return ""
