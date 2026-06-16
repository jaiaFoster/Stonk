"""Small persistent run manifests, independent from heavy report snapshots."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import config
from app.services.commit_identity_service import build_commit_identity


class RunManifestRepository:
    SCHEMA_VERSION = 1

    def __init__(self, db_path: str | None = None):
        self.db_path = str(db_path or config.RUN_MANIFEST_DB_PATH)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS run_manifests (
                run_id TEXT PRIMARY KEY, created_at TEXT, completed_at TEXT, mode TEXT, status TEXT,
                report_quality TEXT, manifest_json TEXT, schema_version INTEGER)""")

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

    def save(self, manifest: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO run_manifests VALUES (?,?,?,?,?,?,?,?)", (
                manifest["run_id"], manifest.get("created_at") or now, manifest.get("completed_at") or now,
                manifest.get("mode"), manifest.get("status"), manifest.get("report_quality"),
                json.dumps(manifest, default=str), self.SCHEMA_VERSION,
            ))
            conn.execute("DELETE FROM run_manifests WHERE run_id IN (SELECT run_id FROM run_manifests ORDER BY completed_at DESC LIMIT -1 OFFSET ?)", (config.RUN_MANIFEST_RETENTION_LIMIT,))

    def latest(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT manifest_json FROM run_manifests ORDER BY completed_at DESC LIMIT 1").fetchone()
        return json.loads(row["manifest_json"]) if row else None


def build_run_manifest(
    run_id: str, mode: str, status: str, report_quality: str, runtime_profile: dict[str, Any],
    payload_profile: dict[str, Any], pipeline_status: dict[str, Any], strategy_results: dict[str, Any],
    daily_opportunity: dict[str, Any], provider_fetch_count: int = 0,
) -> dict[str, Any]:
    counts = {
        key: {
            "pass": value.get("pass_count", 0), "watch": value.get("watch_count", 0),
            "fail": value.get("fail_count", 0), "skipped": value.get("skipped_count", 0),
        } for key, value in (strategy_results or {}).items()
    }
    deploy_identity = build_commit_identity()
    manifest_commit = deploy_identity["current_deploy_git_commit"] if deploy_identity["current_deploy_git_commit"] != "unknown" else deploy_identity["source_of_truth"]
    commit_identity = build_commit_identity({
        "git_commit": manifest_commit,
        "git_branch": deploy_identity["git_branch"],
        "deploy_label": deploy_identity["deploy_label"],
    })
    return {
        "run_id": run_id, "created_at": pipeline_status.get("started_at"), "completed_at": pipeline_status.get("finished_at"),
        "mode": mode, "status": status, "report_quality": report_quality,
        "git_commit": commit_identity["source_of_truth"],
        "git_branch": commit_identity["git_branch"],
        "deploy_label": commit_identity["deploy_label"],
        "commit_identity": commit_identity,
        "payload_chars": (payload_profile.get("sections_bytes") or {}).get("payload_text", 0),
        "summary_json_bytes": (payload_profile.get("sections_bytes") or {}).get("report_summary_json", 0),
        "runtime_total_ms": runtime_profile.get("total_ms", 0), "provider_fetch_count": provider_fetch_count,
        "strategy_counts": counts, "daily_opportunity_count": len((daily_opportunity or {}).get("actions", []) or []),
        "has_broker_data": bool(pipeline_status.get("broker_summary")), "has_market_data": provider_fetch_count > 0,
        "has_options_data": bool(counts.get("earnings_calendar") or counts.get("skew_momentum_vertical") or counts.get("forward_factor_calendar")),
        "has_errors": bool(pipeline_status.get("errors")), "error_count": len(pipeline_status.get("errors", []) or []),
        "redaction_version": 1, "schema_version": 1,
    }
