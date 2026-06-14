"""Compact runtime profile derived from existing pipeline step timings."""

from __future__ import annotations

from typing import Any


def build_runtime_profile(pipeline_status: dict[str, Any]) -> dict[str, Any]:
    steps = pipeline_status.get("steps", []) or []
    timings = {
        str(step.get("key") or "unknown"): int(step.get("duration_ms") or 0)
        for step in steps if step.get("duration_ms") is not None
    }
    return {
        "source": "pipeline_steps",
        "total_ms": int(pipeline_status.get("total_duration_ms") or sum(timings.values())),
        "phase_count": len(timings),
        "phases_ms": timings,
        "slowest": sorted(timings.items(), key=lambda item: item[1], reverse=True)[:5],
    }


def compact_runtime_log(profile: dict[str, Any]) -> str:
    slow = ", ".join(f"{key}={value}ms" for key, value in profile.get("slowest", [])[:5])
    return f"RuntimeProfile: total={profile.get('total_ms', 0)}ms phases={profile.get('phase_count', 0)} slowest=[{slow}]"
