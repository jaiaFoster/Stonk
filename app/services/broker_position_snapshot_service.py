"""Persistent latest-known-good broker positions, stored per account."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import config


class BrokerPositionSnapshotRepository:
    SCHEMA_VERSION = 1

    def __init__(self, db_path: str | None = None, log_print=None):
        self.db_path = str(db_path or config.BROKER_POSITION_SNAPSHOT_DB_PATH)
        self.log = log_print or (lambda message: None)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS broker_position_snapshots (
                broker TEXT NOT NULL, account_id TEXT NOT NULL, account_name TEXT,
                status TEXT NOT NULL, positions_json TEXT NOT NULL, fetched_at TEXT NOT NULL,
                schema_version INTEGER NOT NULL, is_complete INTEGER NOT NULL, error_message TEXT,
                PRIMARY KEY (broker, account_id))""")

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

    def save_account(self, broker: str, account_id: str, account_name: str, positions: list[dict[str, Any]], status: str) -> None:
        if status not in {"SUCCESS", "SUCCESS_EMPTY"}:
            return
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO broker_position_snapshots VALUES (?,?,?,?,?,?,?,?,?)",
                (broker, account_id, account_name, status, json.dumps(positions, default=str), now, self.SCHEMA_VERSION, 1, None),
            )
        self.log(f"BrokerSnapshot: saved {broker} {account_name} positions count={len(positions)} status={status}")

    def latest_account(self, broker: str, account_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM broker_position_snapshots WHERE broker=? AND account_id=? AND is_complete=1 AND schema_version=?",
                (broker, account_id, self.SCHEMA_VERSION),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["positions"] = json.loads(result.pop("positions_json") or "[]")
        return result


def apply_broker_position_fallback(fetch_result: dict[str, Any], repository: BrokerPositionSnapshotRepository) -> dict[str, Any]:
    account_results = list(fetch_result.get("account_results") or [])
    positions: list[dict[str, Any]] = []
    counts = {"success": 0, "success_empty": 0, "failed": 0, "stale_fallback": 0, "unavailable": 0}
    for account in account_results:
        status = str(account.get("status") or "FAILED").upper()
        account_id = str(account.get("account_id") or account.get("account_name") or "unknown")
        account_name = str(account.get("account_name") or account_id)
        current = list(account.get("positions") or [])
        if status in {"SUCCESS", "SUCCESS_EMPTY"}:
            repository.save_account("robinhood", account_id, account_name, current, status)
            positions.extend(current)
            counts["success_empty" if status == "SUCCESS_EMPTY" else "success"] += 1
            continue
        counts["failed"] += 1
        cached = repository.latest_account("robinhood", account_id)
        if cached:
            for position in cached["positions"]:
                positions.append({**position, "broker_data_state": "STALE_FALLBACK", "broker_snapshot_fetched_at": cached["fetched_at"]})
            account["status"] = "STALE_FALLBACK"
            account["snapshot_fetched_at"] = cached["fetched_at"]
            account["positions"] = cached["positions"]
            counts["stale_fallback"] += 1
            repository.log(f"BrokerSnapshot: using stale fallback for robinhood {account_name} from {cached['fetched_at']}")
        else:
            account["status"] = "FAILED"
            account["positions"] = None
            counts["unavailable"] += 1
    quality = "SUCCESS_DEGRADED" if counts["failed"] else "SUCCESS_COMPLETE"
    repository.log("BrokerSnapshot: current positions complete" if quality == "SUCCESS_COMPLETE" else "BrokerSnapshot: current positions degraded")
    repository.log(f"ReportQuality: {quality}")
    provider_status = dict(fetch_result.get("provider_status") or {})
    provider_status["account_summary"] = counts
    provider_status["stale_fallback"] = bool(counts["stale_fallback"])
    provider_status["positions_available"] = bool(positions)
    return {
        **fetch_result, "positions": positions, "has_data": bool(positions),
        "provider_status": provider_status, "account_results": account_results,
        "account_summary": counts, "report_quality": quality,
    }
