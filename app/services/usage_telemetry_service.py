"""Small, privacy-safe usage and storage telemetry. Never blocks app behavior."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import config

ALLOWED_EVENTS = {
    "copy_export",
    "dashboard_load",
    "detail_request",
    "download_export",
    "feedback",
    "section_close",
    "section_open",
    "snapshot_request",
}
ALLOWED_METADATA_KEYS = {
    "dashboard_view",
    "detail_section",
    "export_key",
    "feedback_type",
    "request_mode",
    "route_name",
    "strategy_id",
}


class UsageTelemetryRepository:
    SCHEMA_VERSION = 1

    def __init__(self, db_path: str | None = None):
        self.db_path = str(db_path or config.USAGE_TELEMETRY_DB_PATH)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS usage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                section TEXT,
                source TEXT,
                run_id TEXT,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                schema_version INTEGER NOT NULL
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS telemetry_size_profiles (
                run_id TEXT PRIMARY KEY,
                mode TEXT,
                status TEXT,
                snapshot_sizes_json TEXT NOT NULL,
                section_sizes_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                schema_version INTEGER NOT NULL
            )""")

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

    def record_event(
        self,
        event_type: str,
        *,
        section: str | None = None,
        source: str | None = None,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        event = _clean_name(event_type, 64)
        if event not in ALLOWED_EVENTS:
            return False
        now = datetime.now(timezone.utc).isoformat()
        safe_metadata = _safe_metadata(metadata)
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO usage_events
                   (event_type,section,source,run_id,metadata_json,created_at,schema_version)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    event,
                    _clean_name(section, 100),
                    _clean_name(source, 100),
                    _clean_name(run_id, 100),
                    json.dumps(safe_metadata, separators=(",", ":")),
                    now,
                    self.SCHEMA_VERSION,
                ),
            )
            conn.execute(
                "DELETE FROM usage_events WHERE id IN "
                "(SELECT id FROM usage_events ORDER BY id DESC LIMIT -1 OFFSET ?)",
                (max(1, int(config.USAGE_TELEMETRY_RETENTION_LIMIT)),),
            )
        return True

    def record_size_profile(
        self,
        run_id: str,
        *,
        mode: str,
        status: str,
        snapshot_sizes: dict[str, Any] | None,
        section_sizes: dict[str, Any] | None,
    ) -> bool:
        safe_snapshot = _safe_sizes(snapshot_sizes)
        safe_sections = _safe_sizes(section_sizes)
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO telemetry_size_profiles
                   (run_id,mode,status,snapshot_sizes_json,section_sizes_json,created_at,schema_version)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    _clean_name(run_id, 100),
                    _clean_name(mode, 20),
                    _clean_name(status, 30),
                    json.dumps(safe_snapshot, separators=(",", ":")),
                    json.dumps(safe_sections, separators=(",", ":")),
                    now,
                    self.SCHEMA_VERSION,
                ),
            )
            conn.execute(
                "DELETE FROM telemetry_size_profiles WHERE run_id IN "
                "(SELECT run_id FROM telemetry_size_profiles ORDER BY created_at DESC LIMIT -1 OFFSET ?)",
                (max(1, int(config.USAGE_TELEMETRY_SIZE_PROFILE_RETENTION_LIMIT)),),
            )
        return True

    def summary(self) -> dict[str, Any]:
        with self._connect() as conn:
            event_rows = conn.execute(
                "SELECT event_type,section,source,metadata_json,created_at FROM usage_events ORDER BY id DESC"
            ).fetchall()
            size_rows = conn.execute(
                "SELECT run_id,mode,status,snapshot_sizes_json,section_sizes_json,created_at "
                "FROM telemetry_size_profiles ORDER BY created_at DESC LIMIT 20"
            ).fetchall()
        event_counts = Counter(row["event_type"] for row in event_rows)
        detail_counts = Counter(
            row["section"] for row in event_rows
            if row["event_type"] == "detail_request" and row["section"]
        )
        metadata_rows = [(row, _json_dict(row["metadata_json"])) for row in event_rows]
        snapshot_modes = Counter(
            metadata.get("request_mode") for row, metadata in metadata_rows
            if row["event_type"] == "snapshot_request" and metadata.get("request_mode")
        )
        dashboard_views = Counter(
            metadata.get("dashboard_view") for row, metadata in metadata_rows
            if row["event_type"] == "dashboard_load" and metadata.get("dashboard_view")
        )
        export_actions = Counter(
            metadata.get("export_key") for row, metadata in metadata_rows
            if row["event_type"] in {"copy_export", "download_export"} and metadata.get("export_key")
        )
        compatibility_requests = {
            "full_snapshot": snapshot_modes.get("full", 0),
            "provider_raw_detail": detail_counts.get("provider_raw", 0),
        }
        latest_sizes = _size_row(size_rows[0]) if size_rows else None
        return {
            "enabled": bool(config.USAGE_TELEMETRY_ENABLED),
            "event_count": len(event_rows),
            "event_counts": dict(event_counts.most_common()),
            "usage_breakdown": {
                "snapshot_modes": dict(snapshot_modes.most_common()),
                "dashboard_views": dict(dashboard_views.most_common()),
                "detail_sections": dict(detail_counts.most_common()),
                "export_actions": dict(export_actions.most_common()),
                "compatibility_requests": compatibility_requests,
            },
            "most_requested_detail_sections": _counter_rows(detail_counts, reverse=True),
            "least_requested_detail_sections": _counter_rows(detail_counts, reverse=False),
            "baseline_ready": bool(latest_sizes),
            "latest_size_profile": latest_sizes,
            "size_budget_report": _size_budget_report(latest_sizes),
            "size_trends": [_size_row(row) for row in size_rows],
            "database_path": self.db_path,
            "database_size_bytes": Path(self.db_path).stat().st_size if Path(self.db_path).exists() else 0,
        }


def record_usage_event(event_type: str, **kwargs: Any) -> bool:
    """Fail-safe event recording. Telemetry failure never affects caller."""
    if not config.USAGE_TELEMETRY_ENABLED:
        return False
    try:
        return UsageTelemetryRepository().record_event(event_type, **kwargs)
    except Exception as exc:
        print(f"UsageTelemetry warning: {exc}", flush=True)
        return False


def record_snapshot_size_profile(
    run_id: str,
    *,
    mode: str,
    status: str,
    snapshot_sizes: dict[str, Any] | None,
    section_sizes: dict[str, Any] | None,
) -> bool:
    """Fail-safe size recording. Stores integer sizes only, never payload content."""
    if not config.USAGE_TELEMETRY_ENABLED:
        return False
    try:
        return UsageTelemetryRepository().record_size_profile(
            run_id,
            mode=mode,
            status=status,
            snapshot_sizes=snapshot_sizes,
            section_sizes=section_sizes,
        )
    except Exception as exc:
        print(f"UsageTelemetry size warning: {exc}", flush=True)
        return False


def build_usage_telemetry_diagnostics() -> dict[str, Any]:
    try:
        summary = UsageTelemetryRepository().summary() if config.USAGE_TELEMETRY_ENABLED else {"enabled": False}
        return {
            "status": "ok",
            "read_only": True,
            "provider_calls_triggered": False,
            "telemetry": summary,
        }
    except Exception as exc:
        return {
            "status": "warning",
            "read_only": True,
            "provider_calls_triggered": False,
            "telemetry": {"enabled": bool(config.USAGE_TELEMETRY_ENABLED), "error": str(exc)},
        }


def _safe_metadata(metadata: dict[str, Any] | None) -> dict[str, str]:
    output: dict[str, str] = {}
    for key, value in (metadata or {}).items():
        clean_key = _clean_name(key, 64)
        if clean_key not in ALLOWED_METADATA_KEYS or isinstance(value, (dict, list, tuple, set)):
            continue
        output[clean_key] = _clean_name(value, 200) or ""
    encoded = json.dumps(output, separators=(",", ":"))
    return output if len(encoded) <= max(100, int(config.USAGE_TELEMETRY_METADATA_MAX_CHARS)) else {}


def _safe_sizes(values: dict[str, Any] | None) -> dict[str, int]:
    output: dict[str, int] = {}
    for key, value in (values or {}).items():
        clean_key = _clean_name(key, 100)
        if not clean_key:
            continue
        try:
            output[clean_key] = max(0, int(value))
        except (TypeError, ValueError):
            continue
    return output


def _clean_name(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = "".join(char for char in str(value).strip() if char.isalnum() or char in "._:-/")
    return text[:limit] or None


def _counter_rows(counter: Counter, *, reverse: bool) -> list[dict[str, Any]]:
    rows = [{"section": key, "count": value} for key, value in counter.items()]
    return sorted(rows, key=lambda row: (row["count"], row["section"]), reverse=reverse)[:10]


def _size_row(row: sqlite3.Row) -> dict[str, Any]:
    snapshots = json.loads(row["snapshot_sizes_json"] or "{}")
    sections = json.loads(row["section_sizes_json"] or "{}")
    largest = sorted(
        ({"section": key, "bytes": value} for key, value in sections.items()),
        key=lambda item: item["bytes"],
        reverse=True,
    )[:10]
    return {
        "run_id": row["run_id"],
        "mode": row["mode"],
        "status": row["status"],
        "created_at": row["created_at"],
        "snapshot_sizes": snapshots,
        "largest_sections": largest,
    }


def _size_budget_report(profile: dict[str, Any] | None) -> dict[str, Any]:
    thresholds = {
        "warning": int(config.USAGE_TELEMETRY_SIZE_WARNING_BYTES),
        "large": int(config.USAGE_TELEMETRY_SIZE_LARGE_BYTES),
        "critical": int(config.USAGE_TELEMETRY_SIZE_CRITICAL_BYTES),
    }
    if not profile:
        return {"status": "awaiting_successful_snapshot", "thresholds_bytes": thresholds, "flags": [], "categories": {}}
    snapshot_sizes = profile.get("snapshot_sizes", {}) or {}
    section_sizes = {
        row["section"]: row["bytes"] for row in (profile.get("largest_sections", []) or [])
        if isinstance(row, dict)
    }
    values = {f"snapshot:{key}": value for key, value in snapshot_sizes.items()}
    values.update({f"section:{key}": value for key, value in section_sizes.items()})
    flags = [
        {"name": name, "bytes": value, "severity": _size_severity(value, thresholds)}
        for name, value in values.items()
        if _size_severity(value, thresholds) != "ok"
    ]
    flags.sort(key=lambda row: row["bytes"], reverse=True)
    categories: dict[str, list[dict[str, Any]]] = {
        "hot_summary": [],
        "full_compact_summary": [],
        "raw_provider_archive": [],
        "strategy_cache_output": [],
        "html_reports": [],
        "other": [],
    }
    for name, value in values.items():
        categories[_size_category(name)].append({
            "name": name,
            "bytes": value,
            "severity": _size_severity(value, thresholds),
        })
    return {
        "status": "ready",
        "thresholds_bytes": thresholds,
        "flags": flags,
        "categories": {key: sorted(rows, key=lambda row: row["bytes"], reverse=True) for key, rows in categories.items() if rows},
    }


def _size_severity(value: int, thresholds: dict[str, int]) -> str:
    if value >= thresholds["critical"]:
        return "critical"
    if value >= thresholds["large"]:
        return "large"
    if value >= thresholds["warning"]:
        return "warning"
    return "ok"


def _size_category(name: str) -> str:
    lowered = name.lower()
    if "hot_summary" in lowered:
        return "hot_summary"
    if "full_summary" in lowered or "compact_summary" in lowered:
        return "full_compact_summary"
    if "raw_provider" in lowered or "tradier_snapshot" in lowered or "provider_raw" in lowered:
        return "raw_provider_archive"
    if any(part in lowered for part in ("strategy", "cache", "calendar", "skew", "forward_factor")):
        return "strategy_cache_output"
    if "html" in lowered or "payload_text" in lowered:
        return "html_reports"
    return "other"


def _json_dict(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
