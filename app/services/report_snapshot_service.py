"""Persistent completed report snapshots. Failed runs never replace success."""

from __future__ import annotations

import json
import sqlite3
import zlib
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import config


class ReportSnapshotRepository:
    SCHEMA_VERSION = 1

    def __init__(self, db_path: str | None = None, log_print=None):
        self.db_path = str(db_path or config.REPORT_SNAPSHOT_DB_PATH)
        self.log = log_print or (lambda message: None)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS report_snapshots (
                run_id TEXT PRIMARY KEY, mode TEXT, status TEXT, started_at TEXT, completed_at TEXT,
                payload_json TEXT, summary_json TEXT, data_coverage_json TEXT, provider_status_json TEXT,
                schema_version INTEGER, created_at TEXT)""")
            self._ensure_column(conn, "full_payload_blob", "BLOB")
            self._ensure_column(conn, "full_summary_blob", "BLOB")
            self._ensure_column(conn, "snapshot_profile_json", "TEXT")

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

    def save_success(self, run_id: str, mode: str, payload: str, summary: dict[str, Any], coverage: dict[str, Any], provider_status: dict[str, Any]) -> None:
        self._save(run_id, mode, "complete", payload, summary, coverage, provider_status)
        self.log(f"ReportSnapshot: saved successful run={run_id} schema={self.SCHEMA_VERSION}")
        self.log("ReportSnapshot: canonical snapshot updated")

    def save_degraded(self, run_id: str, mode: str, payload: str, summary: dict[str, Any], coverage: dict[str, Any], provider_status: dict[str, Any]) -> None:
        self._save(run_id, mode, "degraded", payload, summary, coverage, provider_status)
        self.log(f"ReportSnapshot: saved degraded run={run_id}; canonical complete snapshot preserved")
        self.log("ReportSnapshot: canonical snapshot preserved")

    def _save(self, run_id: str, mode: str, status: str, payload: str, summary: dict[str, Any], coverage: dict[str, Any], provider_status: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        full_summary_json = json.dumps(summary, default=str, separators=(",", ":"))
        full_payload_json = json.dumps(payload, separators=(",", ":"))
        hot_summary = build_hot_report_summary(summary)
        hot_summary_json = json.dumps(hot_summary, default=str, separators=(",", ":"))
        compressed = bool(getattr(config, "REPORT_SNAPSHOT_STORE_COMPRESSED_FULL", True))
        full_summary_blob = zlib.compress(full_summary_json.encode("utf-8")) if compressed else None
        full_payload_blob = zlib.compress(full_payload_json.encode("utf-8")) if compressed else None
        profile = {
            "hot_summary_bytes": len(hot_summary_json.encode("utf-8")),
            "full_summary_bytes": len(full_summary_json.encode("utf-8")),
            "compressed_full_summary_bytes": len(full_summary_blob or b""),
            "full_payload_bytes": len(full_payload_json.encode("utf-8")),
            "compressed_full_payload_bytes": len(full_payload_blob or b""),
            "compression_enabled": compressed,
        }
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO report_snapshots
                   (run_id,mode,status,started_at,completed_at,payload_json,summary_json,data_coverage_json,
                    provider_status_json,schema_version,created_at,full_payload_blob,full_summary_blob,snapshot_profile_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id, mode, status, now, now,
                    "" if compressed else full_payload_json,
                    hot_summary_json if compressed else full_summary_json,
                    json.dumps(coverage, default=str), json.dumps(provider_status, default=str),
                    self.SCHEMA_VERSION, now, full_payload_blob, full_summary_blob,
                    json.dumps(profile, separators=(",", ":")),
                ),
            )
            conn.execute(
                "DELETE FROM report_snapshots WHERE run_id IN (SELECT run_id FROM report_snapshots ORDER BY created_at DESC LIMIT -1 OFFSET ?)",
                (config.REPORT_SNAPSHOT_RETENTION_LIMIT,),
            )

    def record_failure(self, run_id: str, mode: str, summary: dict[str, Any] | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        previous = self.latest_success()
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO report_snapshots
                   (run_id,mode,status,started_at,completed_at,payload_json,summary_json,data_coverage_json,
                    provider_status_json,schema_version,created_at,full_payload_blob,full_summary_blob,snapshot_profile_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id, mode, "failed", now, now, "", json.dumps(summary or {}, default=str), "{}", "{}",
                 self.SCHEMA_VERSION, now, None, None, "{}"),
            )
        self.log(f"ReportSnapshot: failed run preserved previous snapshot={(previous or {}).get('run_id', 'none')}")

    def latest_success(self, *, include_full: bool = False) -> dict[str, Any] | None:
        columns = "*" if include_full else (
            "run_id,mode,status,started_at,completed_at,payload_json,summary_json,data_coverage_json,"
            "provider_status_json,schema_version,created_at,snapshot_profile_json"
        )
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {columns} FROM report_snapshots WHERE status='complete' AND schema_version=? ORDER BY completed_at DESC LIMIT 1",
                (self.SCHEMA_VERSION,),
            ).fetchone()
        result = dict(row) if row else None
        if result:
            self.log(f"ReportSnapshot: loaded latest successful run={result['run_id']}")
        return result

    def latest_degraded(self, *, include_full: bool = False) -> dict[str, Any] | None:
        columns = "*" if include_full else (
            "run_id,mode,status,started_at,completed_at,payload_json,summary_json,data_coverage_json,"
            "provider_status_json,schema_version,created_at,snapshot_profile_json"
        )
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {columns} FROM report_snapshots WHERE status='degraded' AND schema_version=? ORDER BY completed_at DESC LIMIT 1",
                (self.SCHEMA_VERSION,),
            ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def load_summary(snapshot: dict[str, Any] | None, *, full: bool = False) -> dict[str, Any]:
        if not snapshot:
            return {}
        if full and snapshot.get("full_summary_blob"):
            return _decompress_json(snapshot.get("full_summary_blob"), {})
        return _json(snapshot.get("summary_json"), {})

    @staticmethod
    def load_payload(snapshot: dict[str, Any] | None, *, full: bool = False) -> str:
        if not snapshot:
            return ""
        if full and snapshot.get("full_payload_blob"):
            return str(_decompress_json(snapshot.get("full_payload_blob"), ""))
        return str(_json(snapshot.get("payload_json"), ""))

    @staticmethod
    def snapshot_profile(snapshot: dict[str, Any] | None) -> dict[str, Any]:
        return _json((snapshot or {}).get("snapshot_profile_json"), {})

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, name: str, sql_type: str) -> None:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(report_snapshots)")}
        if name not in columns:
            conn.execute(f"ALTER TABLE report_snapshots ADD COLUMN {name} {sql_type}")


def build_hot_report_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Keep only facts needed by shell, diagnostics, and lightweight advisor reads."""
    report = (summary or {}).get("report_data", {}) or {}
    tradier = report.get("tradier_snapshot", {}) or {}
    keep_keys = {
        "_benchmark_metrics", "_calendar_lifecycle_checks", "_daily_opportunity_engine",
        "_data_coverage", "_open_options_positions", "_payload_size_profile", "_pipeline_status",
        "_portfolio_gap", "_provider_status", "_runtime_profile", "_storage_profile",
        "_stock_momentum_strategy", "_unified_calendar_trade_engine",
    }
    hot_tradier = {key: tradier.get(key) for key in keep_keys if key in tradier}
    hot_tradier["_strategy_results"] = {
        key: _compact_strategy(value)
        for key, value in (tradier.get("_strategy_results", {}) or {}).items()
        if isinstance(value, dict)
    }
    skew = tradier.get("_skew_momentum_vertical_strategy")
    if isinstance(skew, dict):
        hot_tradier["_skew_momentum_vertical_strategy"] = _compact_strategy(skew)
    output = {
        key: value for key, value in (summary or {}).items()
        if key != "report_data" and key != "strategy_results"
    }
    output["strategy_results"] = {
        key: _compact_strategy(value)
        for key, value in ((summary or {}).get("strategy_results", {}) or {}).items()
        if isinstance(value, dict)
    }
    output["report_data"] = {
        "positions": report.get("positions", []),
        "news": {},
        "recommendations": report.get("recommendations", []),
        "tradier_snapshot": hot_tradier,
        "log": list(report.get("log", []) or [])[-int(getattr(config, "REPORT_SNAPSHOT_HOT_LOG_LINES", 10) or 10):],
    }
    return output


def _compact_strategy(value: dict[str, Any]) -> dict[str, Any]:
    row_limit = int(getattr(config, "REPORT_SNAPSHOT_HOT_STRATEGY_ROWS", 5) or 5)
    keep = {
        "strategy_id", "strategy_label", "enabled", "ran", "mode", "run_mode",
        "pass_count", "watch_count", "fail_count", "skipped_count", "summary",
    }
    output = {key: value.get(key) for key in keep if key in value}
    for key in ("pass_items", "watch_items", "blocked_items", "items", "rows"):
        if isinstance(value.get(key), list):
            output[key] = value.get(key)[:row_limit]
    return output


def _json(raw: Any, fallback: Any) -> Any:
    try:
        return json.loads(raw) if isinstance(raw, str) and raw else fallback
    except (json.JSONDecodeError, TypeError):
        return fallback


def _decompress_json(raw: Any, fallback: Any) -> Any:
    try:
        return json.loads(zlib.decompress(bytes(raw)).decode("utf-8"))
    except (ValueError, TypeError, zlib.error, json.JSONDecodeError):
        return fallback
