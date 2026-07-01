"""Read-only ASA smoke collector. Uses existing diagnostic endpoints only."""

from __future__ import annotations

import json
import os
import pathlib
import urllib.request
from collections import Counter
from datetime import datetime
from typing import Any


BASE = os.getenv("BASE", "https://web-production-4a8e8.up.railway.app").rstrip("/")
TOKEN = os.getenv("DEV_API_TOKEN", "")
ENDPOINTS = {
    "dev_status": "/api/dev/status",
    "feature_health": "/api/dev/feature-health",
    "latest_manifest": "/api/dev/latest-run-manifest",
    "latest_profiles": "/api/dev/latest-profiles",
    "snapshot_summary": "/api/dev/snapshot?mode=summary",
    "strategy_detail": "/api/dev/snapshot/detail/strategies",
    "calendar_trace": "/api/dev/calendar-pipeline-trace",
}


def collect() -> pathlib.Path:
    if not TOKEN:
        raise SystemExit("Set DEV_API_TOKEN first")
    output = pathlib.Path(f"asa_smoke_{datetime.now():%Y%m%d_%H%M%S}")
    output.mkdir()
    payloads = {}
    for name, path in ENDPOINTS.items():
        separator = "&" if "?" in path else "?"
        with urllib.request.urlopen(f"{BASE}{path}{separator}token={TOKEN}", timeout=60) as response:
            payloads[name] = json.loads(response.read())
        (output / f"{name}.json").write_text(json.dumps(payloads[name], indent=2), encoding="utf-8")
    summary = summarize(payloads)
    (output / "ASA_SMOKE_SUMMARY.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return output


def summarize(payloads: dict[str, Any]) -> dict[str, Any]:
    feature = payloads.get("feature_health") or {}
    detail = (payloads.get("strategy_detail") or {}).get("detail") or {}
    trace = payloads.get("calendar_trace") or {}
    strategies = {}
    blockers: Counter[str] = Counter()
    for strategy_id, block in detail.items():
        if not isinstance(block, dict):
            continue
        canonical = block.get("canonical_opportunities") or []
        strategies[strategy_id] = {
            "legacy_rows": len(block.get("rows") or []),
            "canonical_opportunities": block.get("canonical_opportunity_count", len(canonical)),
            "normalizer_errors": block.get("canonical_normalizer_error_count", 0),
            "lost_fields": block.get("canonical_lost_field_counts") or {},
        }
        for row in canonical:
            blockers.update(row.get("blockers") or [])
    checks = feature.get("checks") or {}
    return {
        "status": feature.get("status") or feature.get("overall_status"),
        "trade_execution_enabled": feature.get("trade_execution_enabled"),
        "ff_dry_run": checks.get("forward_factor_dry_run"),
        "ff_daily_opportunity_excluded": checks.get("forward_factor_daily_opportunity_excluded"),
        "provider_calls_triggered": feature.get("provider_calls_triggered", False),
        "strategies": strategies,
        "top_blockers": blockers.most_common(10),
        "calendar_pipeline": trace.get("summary") or {},
    }


if __name__ == "__main__":
    collect()
