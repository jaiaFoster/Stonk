"""SQLite read-through cache for reusable market facts."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app import config
from app.models.market_data_models import MarketDataRecord


class MarketDataRepository:
    def __init__(self, db_path: str | None = None):
        self.db_path = str(db_path or config.MARKET_DATA_DB_PATH)
        self.enabled = bool(config.MARKET_DATA_ENABLE_SQLITE_CACHE)
        if self.enabled:
            self._initialize()

    @contextmanager
    def _connect(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=5)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=5000")
            if config.MARKET_DATA_ENABLE_WAL:
                conn.execute("PRAGMA journal_mode=WAL")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _initialize(self) -> None:
        try:
            with self._connect() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS market_data_records (
                        ticker TEXT NOT NULL, data_type TEXT NOT NULL, cache_key TEXT NOT NULL,
                        provider TEXT NOT NULL, payload_json TEXT NOT NULL, confidence TEXT NOT NULL,
                        fetched_at TEXT NOT NULL, expires_at TEXT NOT NULL, payload_hash TEXT NOT NULL,
                        PRIMARY KEY (ticker, data_type, cache_key)
                    );
                    CREATE TABLE IF NOT EXISTS market_data_fetch_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, ticker TEXT, data_type TEXT,
                        provider TEXT, status TEXT, source TEXT, error_message TEXT, created_at TEXT
                    );
                    CREATE TABLE IF NOT EXISTS provider_errors (
                        ticker TEXT NOT NULL, data_type TEXT NOT NULL, provider TEXT NOT NULL,
                        error_message TEXT, first_seen TEXT, last_seen TEXT, seen_count INTEGER DEFAULT 1,
                        suppress_until TEXT, PRIMARY KEY (ticker, data_type, provider)
                    );
                    CREATE TABLE IF NOT EXISTS data_coverage_runs (
                        run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, summary_json TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS equity_quotes (
                        ticker TEXT PRIMARY KEY, provider TEXT, fetched_at TEXT, expires_at TEXT, payload_json TEXT
                    );
                    CREATE TABLE IF NOT EXISTS equity_daily_candles (
                        ticker TEXT, provider TEXT, date TEXT, payload_json TEXT, fetched_at TEXT,
                        PRIMARY KEY(ticker, provider, date)
                    );
                    CREATE TABLE IF NOT EXISTS option_chain_snapshots (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, provider TEXT, fetched_at TEXT,
                        expires_at TEXT, raw_json TEXT
                    );
                    CREATE TABLE IF NOT EXISTS earnings_events (
                        ticker TEXT PRIMARY KEY, provider TEXT, event_date TEXT, fetched_at TEXT, expires_at TEXT, raw_json TEXT
                    );
                    CREATE TABLE IF NOT EXISTS derived_metrics (
                        ticker TEXT, metric_name TEXT, metric_json TEXT, provider_basis TEXT, computed_at TEXT,
                        expires_at TEXT, PRIMARY KEY(ticker, metric_name)
                    );
                    """
                )
        except sqlite3.DatabaseError:
            self.enabled = False

    def put(self, ticker: str, data_type: str, payload: Any, provider: str, ttl_seconds: int, confidence: str = "high", cache_key: str = "default") -> MarketDataRecord:
        now = datetime.now(timezone.utc)
        record = MarketDataRecord(
            ticker=ticker.upper(), data_type=data_type, payload=payload, provider=provider,
            fetched_at=now.isoformat(), expires_at=(now + timedelta(seconds=ttl_seconds)).isoformat(),
            confidence=confidence,
        )
        if not self.enabled:
            return record
        raw = json.dumps(payload, sort_keys=True, default=str)
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO market_data_records
                (ticker,data_type,cache_key,provider,payload_json,confidence,fetched_at,expires_at,payload_hash)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (record.ticker, data_type, cache_key, provider, raw, confidence, record.fetched_at, record.expires_at, hashlib.sha256(raw.encode()).hexdigest()),
            )
        return record

    def get(self, ticker: str, data_type: str, cache_key: str = "default", allow_stale: bool = False) -> MarketDataRecord | None:
        if not self.enabled:
            return None
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM market_data_records WHERE ticker=? AND data_type=? AND cache_key=?",
                    (ticker.upper(), data_type, cache_key),
                ).fetchone()
            if not row:
                return None
            fresh = datetime.fromisoformat(row["expires_at"]) >= datetime.now(timezone.utc)
            if not fresh and not allow_stale:
                return None
            return MarketDataRecord(
                ticker=row["ticker"], data_type=row["data_type"], payload=json.loads(row["payload_json"]),
                provider=row["provider"], fetched_at=row["fetched_at"], expires_at=row["expires_at"],
                confidence=row["confidence"], fresh=fresh, state="COMPLETE" if fresh else "STALE_CACHE_USED",
            )
        except (sqlite3.DatabaseError, ValueError, json.JSONDecodeError):
            return None

    def log_fetch(self, run_id: str, ticker: str, data_type: str, provider: str, status: str, source: str, error: str = "") -> None:
        if not self.enabled:
            return
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO market_data_fetch_log (run_id,ticker,data_type,provider,status,source,error_message,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (run_id, ticker, data_type, provider, status, source, error, datetime.now(timezone.utc).isoformat()),
            )

    def record_provider_error(self, ticker: str, data_type: str, provider: str, error: str, ttl_seconds: int) -> None:
        if not self.enabled:
            return
        now = datetime.now(timezone.utc)
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO provider_errors (ticker,data_type,provider,error_message,first_seen,last_seen,seen_count,suppress_until)
                VALUES (?,?,?,?,?,?,1,?)
                ON CONFLICT(ticker,data_type,provider) DO UPDATE SET error_message=excluded.error_message,
                last_seen=excluded.last_seen, seen_count=provider_errors.seen_count+1, suppress_until=excluded.suppress_until""",
                (ticker, data_type, provider, error, now.isoformat(), now.isoformat(), (now + timedelta(seconds=ttl_seconds)).isoformat()),
            )

    def provider_error_suppressed(self, ticker: str, data_type: str, provider: str) -> bool:
        if not self.enabled:
            return False
        with self._connect() as conn:
            row = conn.execute(
                "SELECT suppress_until FROM provider_errors WHERE ticker=? AND data_type=? AND provider=?",
                (ticker, data_type, provider),
            ).fetchone()
        return bool(row and datetime.fromisoformat(row["suppress_until"]) > datetime.now(timezone.utc))

    def save_coverage(self, run_id: str, summary: dict[str, Any]) -> None:
        if self.enabled:
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO data_coverage_runs (run_id,created_at,summary_json) VALUES (?,?,?)",
                    (run_id, datetime.now(timezone.utc).isoformat(), json.dumps(summary, default=str)),
                )
