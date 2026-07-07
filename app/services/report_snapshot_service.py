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
from app.services.provider_payload_compaction_service import compact_tradier_snapshot


class ReportSnapshotRepository:
    SCHEMA_VERSION = 2

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
            self._ensure_column(conn, "raw_provider_blob", "BLOB")
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
        compact_full_summary = build_compact_full_report_summary(summary)
        full_summary_json = json.dumps(compact_full_summary, default=str, separators=(",", ":"))
        full_payload_json = json.dumps(payload, separators=(",", ":"))
        raw_provider_json = json.dumps(
            (((summary or {}).get("report_data") or {}).get("tradier_snapshot") or {}),
            default=str,
            separators=(",", ":"),
        )
        if getattr(config, "REPORT_FULL_DEBUG_PAYLOAD_ENABLED", False):
            hot_summary = build_hot_report_summary(summary)
        else:
            hot_summary = build_compact_manifest_summary(summary)
        hot_summary_json = json.dumps(hot_summary, default=str, separators=(",", ":"))
        compressed = bool(getattr(config, "REPORT_SNAPSHOT_STORE_COMPRESSED_FULL", True))
        full_summary_blob = zlib.compress(full_summary_json.encode("utf-8")) if compressed else None
        full_payload_blob = zlib.compress(full_payload_json.encode("utf-8")) if compressed else None
        raw_provider_blob = zlib.compress(raw_provider_json.encode("utf-8"))
        compact_tradier = (((compact_full_summary or {}).get("report_data") or {}).get("tradier_snapshot") or {})
        profile = {
            "hot_summary_bytes": len(hot_summary_json.encode("utf-8")),
            "full_summary_bytes": len(full_summary_json.encode("utf-8")),
            "compressed_full_summary_bytes": len(full_summary_blob or b""),
            "full_payload_bytes": len(full_payload_json.encode("utf-8")),
            "compressed_full_payload_bytes": len(full_payload_blob or b""),
            "raw_provider_snapshot_bytes": len(raw_provider_json.encode("utf-8")),
            "compressed_raw_provider_bytes": len(raw_provider_blob or b""),
            "compact_tradier_snapshot_bytes": len(json.dumps(compact_tradier, default=str, separators=(",", ":")).encode("utf-8")),
            "compression_enabled": compressed,
        }
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO report_snapshots
                   (run_id,mode,status,started_at,completed_at,payload_json,summary_json,data_coverage_json,
                    provider_status_json,schema_version,created_at,full_payload_blob,full_summary_blob,raw_provider_blob,snapshot_profile_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id, mode, status, now, now,
                    "" if compressed else full_payload_json,
                    hot_summary_json if compressed else full_summary_json,
                    json.dumps(coverage, default=str), json.dumps(provider_status, default=str),
                    self.SCHEMA_VERSION, now, full_payload_blob, full_summary_blob, raw_provider_blob,
                    json.dumps(profile, separators=(",", ":")),
                ),
            )
            conn.execute(
                "DELETE FROM report_snapshots WHERE run_id IN (SELECT run_id FROM report_snapshots ORDER BY created_at DESC LIMIT -1 OFFSET ?)",
                (config.REPORT_SNAPSHOT_RETENTION_LIMIT,),
            )
        try:
            from app.services.usage_telemetry_service import record_snapshot_size_profile

            payload_profile = (((summary or {}).get("report_data") or {}).get("tradier_snapshot") or {}).get("_payload_size_profile", {})
            record_snapshot_size_profile(
                run_id,
                mode=mode,
                status=status,
                snapshot_sizes=profile,
                section_sizes=(payload_profile or {}).get("sections_bytes", {}),
            )
        except Exception as exc:
            self.log(f"UsageTelemetry snapshot profile warning: {exc}")

    def record_failure(self, run_id: str, mode: str, summary: dict[str, Any] | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        previous = self.latest_success()
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO report_snapshots
                   (run_id,mode,status,started_at,completed_at,payload_json,summary_json,data_coverage_json,
                    provider_status_json,schema_version,created_at,full_payload_blob,full_summary_blob,raw_provider_blob,snapshot_profile_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id, mode, "failed", now, now, "", json.dumps(summary or {}, default=str), "{}", "{}",
                 self.SCHEMA_VERSION, now, None, None, None, "{}"),
            )
        self.log(f"ReportSnapshot: failed run preserved previous snapshot={(previous or {}).get('run_id', 'none')}")

    def latest_success(self, *, include_full: bool = False) -> dict[str, Any] | None:
        columns = "*" if include_full else (
            "run_id,mode,status,started_at,completed_at,payload_json,summary_json,data_coverage_json,"
            "provider_status_json,schema_version,created_at,snapshot_profile_json"
        )
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {columns} FROM report_snapshots"
                " WHERE status='complete' AND (schema_version IS NULL OR schema_version <= ?)"
                " ORDER BY completed_at DESC LIMIT 1",
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
                f"SELECT {columns} FROM report_snapshots"
                " WHERE status='degraded' AND (schema_version IS NULL OR schema_version <= ?)"
                " ORDER BY completed_at DESC LIMIT 1",
                (self.SCHEMA_VERSION,),
            ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def load_summary(snapshot: dict[str, Any] | None, *, full: bool = False) -> dict[str, Any]:
        if not snapshot:
            return {}
        if full:
            summary = (
                _decompress_json(snapshot.get("full_summary_blob"), {})
                if snapshot.get("full_summary_blob")
                else _json(snapshot.get("summary_json"), {})
            )
            raw_provider = ReportSnapshotRepository.load_raw_provider_snapshot(snapshot)
            if raw_provider:
                summary.setdefault("report_data", {})["tradier_snapshot"] = raw_provider
                _restore_full_summary_compatibility(summary, raw_provider)
            return summary
        return _json(snapshot.get("summary_json"), {})

    @staticmethod
    def load_raw_provider_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
        if not snapshot:
            return {}
        if snapshot.get("raw_provider_blob"):
            value = _decompress_json(snapshot.get("raw_provider_blob"), {})
            return value if isinstance(value, dict) else {}
        summary = _decompress_json(snapshot.get("full_summary_blob"), {}) if snapshot.get("full_summary_blob") else {}
        return (((summary or {}).get("report_data") or {}).get("tradier_snapshot") or {})

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


def build_compact_manifest_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Compact manifest for summary_json — schema_version=2, sub-50KB, no row arrays.

    Contains only strategy counts, DO summary (top 3 actions), broker status,
    position summary, provider status, and payload profile. Full pipeline data
    is preserved in full_summary_blob / raw_provider_blob for API reads.
    """
    report = (summary or {}).get("report_data", {}) or {}
    tradier = report.get("tradier_snapshot", {}) or {}
    run_manifest = tradier.get("_run_manifest") or {}
    pipeline = tradier.get("_pipeline_status") or (summary or {}).get("pipeline_status") or {}
    provider_status_raw = tradier.get("_provider_status") or {}
    payload_profile = tradier.get("_payload_size_profile") or (summary or {}).get("payload_size_profile") or {}
    strategy_results = tradier.get("_strategy_results") or summary.get("strategy_results") or {}
    do_engine = tradier.get("_daily_opportunity_engine") or {}
    open_opts = tradier.get("_open_options_positions") or {}

    strategy_counts = {
        str(sid): {
            "pass": int((data.get("pass_count") or 0)),
            "watch": int((data.get("watch_count") or 0)),
            "fail": int((data.get("fail_count") or 0)),
            "skipped": int((data.get("skipped_count") or 0)),
        }
        for sid, data in (strategy_results or {}).items()
        if isinstance(data, dict)
    }

    raw_actions = (do_engine.get("actions") or []) if isinstance(do_engine, dict) else []
    if isinstance(raw_actions, dict):
        raw_actions = raw_actions.get("sample") or []
    raw_actions = list(raw_actions)
    scores = [
        float(a.get("priority_score") or a.get("signal_score") or a.get("actionability_score") or 0)
        for a in raw_actions if isinstance(a, dict)
    ]
    do_summary = {
        "enabled": bool(do_engine.get("enabled", True) if isinstance(do_engine, dict) else True),
        "action_count": len(raw_actions),
        "top_actions": [
            {k: a.get(k) for k in ("ticker", "action", "type", "source")}
            for a in raw_actions[:3] if isinstance(a, dict)
        ],
        "signal_score_min": round(min(scores), 1) if scores else None,
        "signal_score_max": round(max(scores), 1) if scores else None,
    }

    positions = []
    if isinstance(open_opts, dict):
        positions = open_opts.get("options_positions") or open_opts.get("positions") or []
    open_position_summary = {
        "options_count": len(positions),
        "has_open_verticals": bool(open_opts.get("has_open_verticals") if isinstance(open_opts, dict) else False),
        "has_open_calendars": bool(open_opts.get("has_open_calendars") if isinstance(open_opts, dict) else False),
    }

    broker_summary_block = {
        "mode": str(pipeline.get("broker_mode") or "").strip() or None if isinstance(pipeline, dict) else None,
        "has_data": bool(
            (pipeline.get("broker_summary") if isinstance(pipeline, dict) else False)
            or run_manifest.get("has_broker_data")
        ),
        "auth_status": str(run_manifest.get("broker_auth_status") or "UNKNOWN"),
    }

    provider_summary = {}
    for pname, pdata in (provider_status_raw or {}).items():
        if isinstance(pdata, dict):
            provider_summary[str(pname)] = {
                "success": bool(pdata.get("success") or (not pdata.get("error") and pdata.get("configured"))),
            }

    sections_bytes = (payload_profile or {}).get("sections_bytes") or {}
    payload_compact = {
        "sections_bytes": {k: int(v) for k, v in sections_bytes.items() if isinstance(v, (int, float))},
    }

    pipeline_errors = list((pipeline.get("errors") or []))[:10] if isinstance(pipeline, dict) else []

    return {
        "schema_version": 2,
        "compact_manifest": True,
        "report_quality": str((summary or {}).get("report_quality") or ""),
        "strategy_counts": strategy_counts,
        "daily_opportunity_summary": do_summary,
        "open_position_summary": open_position_summary,
        "broker_snapshot_summary": broker_summary_block,
        "provider_status_summary": provider_summary,
        "payload_profile": payload_compact,
        "errors": pipeline_errors,
        "api_links": {
            "daily_opportunity": "/api/daily-opportunity",
            "open_positions": "/api/open-positions",
            "strategy_rows_template": "/api/strategies/{strategy_id}/rows",
            "forward_factor_calendar_rows": "/api/strategies/forward_factor_calendar/rows",
            "run_latest": "/api/runs/latest",
            "run_refresh": "/api/run/refresh",
        },
    }


def build_hot_report_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Keep only facts needed by shell, diagnostics, and lightweight advisor reads."""
    report = (summary or {}).get("report_data", {}) or {}
    tradier = report.get("tradier_snapshot", {}) or {}
    direct_keys = {
        "_benchmark_metrics", "_data_coverage", "_payload_size_profile",
        "_provider_status", "_runtime_profile", "_storage_profile",
    }
    hot_tradier = {key: tradier.get(key) for key in direct_keys if key in tradier}
    compact_detail_keys = {
        "_calendar_lifecycle_checks", "_daily_opportunity_engine",
        "_open_options_positions", "_portfolio_gap", "_stock_momentum_strategy",
        "_unified_calendar_trade_engine",
    }
    for key in compact_detail_keys:
        if isinstance(tradier.get(key), dict):
            hot_tradier[key] = _compact_hot_detail(tradier.get(key))
    if isinstance(tradier.get("_pipeline_status"), dict):
        hot_tradier["_pipeline_status"] = _compact_hot_detail(tradier.get("_pipeline_status"))
    hot_tradier["_strategy_results"] = {
        key: _compact_strategy(value, include_rows=False)
        for key, value in (tradier.get("_strategy_results", {}) or {}).items()
        if isinstance(value, dict)
    }
    skew = tradier.get("_skew_momentum_vertical_strategy")
    if isinstance(skew, dict):
        hot_tradier["_skew_momentum_vertical_strategy"] = _compact_strategy(skew, include_rows=False)
    output = {"report_quality": (summary or {}).get("report_quality")}
    output["report_data"] = {
        "positions": report.get("positions", []),
        "news": {},
        "recommendations": report.get("recommendations", []),
        "tradier_snapshot": hot_tradier,
        "log": list(report.get("log", []) or [])[-int(getattr(config, "REPORT_SNAPSHOT_HOT_LOG_LINES", 10) or 10):],
    }
    return output


def build_compact_full_report_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Keep full report sections but move raw provider collections to separate archive."""
    output = dict(summary or {})
    report = dict(output.get("report_data", {}) or {})
    raw_tradier = report.get("tradier_snapshot") if isinstance(report.get("tradier_snapshot"), dict) else {}
    for summary_key, provider_key in _FULL_SUMMARY_ALIASES.items():
        if provider_key in raw_tradier:
            output.pop(summary_key, None)
    report["tradier_snapshot"] = compact_tradier_snapshot(raw_tradier)
    output["report_data"] = report
    return output


def _compact_strategy(value: dict[str, Any], *, include_rows: bool) -> dict[str, Any]:
    row_limit = int(getattr(config, "REPORT_SNAPSHOT_HOT_STRATEGY_ROWS", 5) or 5)
    keep = {
        "strategy_id", "strategy_label", "enabled", "ran", "mode", "run_mode",
        "pass_count", "watch_count", "fail_count", "skipped_count", "summary",
        "lifecycle_status", "active_count",
    }
    output = {key: value.get(key) for key in keep if key in value}
    if "summary" in output:
        output["summary"] = _compact_nested_summary(output["summary"])
    for key in ("active_rows", "active_items"):
        if isinstance(value.get(key), list):
            output[key] = [_compact_hot_row(item) for item in value.get(key)[:row_limit]]
    if include_rows:
        for key in ("pass_items", "watch_items", "blocked_items", "items", "rows"):
            if isinstance(value.get(key), list):
                output[key] = [_compact_hot_row(item) for item in value.get(key)[:row_limit]]
    return output


def _compact_hot_detail(value: dict[str, Any]) -> dict[str, Any]:
    """Preserve shell-driving summaries and a bounded number of visible rows."""
    row_limit = max(5, int(getattr(config, "REPORT_SNAPSHOT_HOT_STRATEGY_ROWS", 5) or 5))
    scalar_keys = {
        "enabled", "has_data", "mode", "run_mode", "overall_status", "report_quality",
        "target_profile", "summary", "broker_summary", "config_snapshot", "errors", "warnings",
        "lifecycle_status", "active_count",
    }
    row_keys = {
        "actions", "active_rows", "blocked_items", "calendars", "checks",
        "fail_items", "items", "new_trade_rows", "open_options", "open_positions",
        "open_trade_rows", "pass_items", "positions", "risk_rows", "rows",
        "suggestions", "watch_items",
    }
    output = {key: value.get(key) for key in scalar_keys if key in value}
    if "summary" in output:
        output["summary"] = _compact_nested_summary(output["summary"])
    for key in row_keys:
        if isinstance(value.get(key), list):
            output[key] = [_compact_hot_row(item) for item in value.get(key)[:row_limit]]
    return output


def _compact_nested_summary(value: Any) -> Any:
    """Keep scalar summary facts while replacing embedded detail collections."""
    if isinstance(value, dict):
        return {
            key: _compact_nested_summary(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        if all(not isinstance(item, (dict, list)) for item in value):
            return value[:5]
        return []
    return value


def _compact_hot_row(value: Any, *, depth: int = 0) -> Any:
    """Preserve visible decision facts while bounding nested diagnostics."""
    if not isinstance(value, dict):
        return value
    output: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, dict):
            output[key] = _compact_hot_row(item, depth=depth + 1) if depth < 2 else {
                nested_key: nested_value
                for nested_key, nested_value in item.items()
                if not isinstance(nested_value, (dict, list))
            }
        elif isinstance(item, list):
            if all(not isinstance(entry, (dict, list)) for entry in item):
                output[key] = item[:5]
            elif depth < 1:
                output[key] = [
                    _compact_hot_row(entry, depth=depth + 1)
                    for entry in item[:2]
                    if isinstance(entry, dict)
                ]
        else:
            output[key] = item
    return output


def _restore_full_summary_compatibility(summary: dict[str, Any], raw_provider: dict[str, Any]) -> None:
    """Rehydrate legacy top-level aliases only for explicit full reads."""
    for summary_key, provider_key in _FULL_SUMMARY_ALIASES.items():
        if summary_key not in summary and provider_key in raw_provider:
            summary[summary_key] = raw_provider.get(provider_key)


_FULL_SUMMARY_ALIASES = {
    "strategy_results": "_strategy_results",
    "pipeline_status": "_pipeline_status",
    "runtime_profile": "_runtime_profile",
    "payload_size_profile": "_payload_size_profile",
    "storage_profile": "_storage_profile",
}


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
