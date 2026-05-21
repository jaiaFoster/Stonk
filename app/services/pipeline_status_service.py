"""
app/services/pipeline_status_service.py — Structured run status helpers.

The pipeline still returns the historical tuple shape used by app/main.py, but
this module gives the report a reliable machine-readable status object. That
makes it easier to see whether a run completed all expected stages before the
payload was formatted.
"""

from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_pipeline_status(run_mode: str) -> dict[str, Any]:
    return {
        "source": "pipeline_status_v1",
        "run_mode": "dev" if str(run_mode).lower() == "dev" else "prod",
        "started_at": utc_now_iso(),
        "finished_at": None,
        "overall_status": "running",
        "steps": [],
        "step_map": {},
        "warnings": [],
        "errors": [],
        "config_snapshot": {},
        "summary": {
            "completed_count": 0,
            "warning_count": 0,
            "error_count": 0,
            "skipped_count": 0,
        },
    }


def begin_step(status: dict[str, Any], key: str, label: str) -> dict[str, Any]:
    step = {
        "key": key,
        "label": label,
        "status": "running",
        "started_at": utc_now_iso(),
        "finished_at": None,
        "duration_ms": None,
        "message": "Running...",
        "meta": {},
        "_perf_start": perf_counter(),
    }
    status.setdefault("steps", []).append(step)
    status.setdefault("step_map", {})[key] = step
    return step


def complete_step(
    status: dict[str, Any],
    key: str,
    message: str = "Complete.",
    meta: dict[str, Any] | None = None,
) -> None:
    _finish_step(status, key, "complete", message, meta)


def warn_step(
    status: dict[str, Any],
    key: str,
    message: str,
    meta: dict[str, Any] | None = None,
) -> None:
    _finish_step(status, key, "warning", message, meta)
    status.setdefault("warnings", []).append({"step": key, "message": message})


def fail_step(
    status: dict[str, Any],
    key: str,
    message: str,
    meta: dict[str, Any] | None = None,
) -> None:
    _finish_step(status, key, "error", message, meta)
    status.setdefault("errors", []).append({"step": key, "message": message})


def skip_step(
    status: dict[str, Any], key: str, label: str, message: str = "Skipped.") -> None:
    begin_step(status, key, label)
    _finish_step(status, key, "skipped", message, {})


def finish_pipeline(status: dict[str, Any], overall_status: str = "complete") -> None:
    status["finished_at"] = utc_now_iso()
    status["overall_status"] = overall_status
    steps = status.get("steps", []) or []
    status["summary"] = {
        "completed_count": sum(1 for step in steps if step.get("status") == "complete"),
        "warning_count": sum(1 for step in steps if step.get("status") == "warning"),
        "error_count": sum(1 for step in steps if step.get("status") == "error"),
        "skipped_count": sum(1 for step in steps if step.get("status") == "skipped"),
        "step_count": len(steps),
    }


def _finish_step(
    status: dict[str, Any],
    key: str,
    state: str,
    message: str,
    meta: dict[str, Any] | None,
) -> None:
    step = status.setdefault("step_map", {}).get(key)
    if not step:
        step = begin_step(status, key, key.replace("_", " ").title())
    started = step.pop("_perf_start", None)
    step["status"] = state
    step["finished_at"] = utc_now_iso()
    step["message"] = message
    if meta:
        step.setdefault("meta", {}).update(meta)
    if started is not None:
        try:
            step["duration_ms"] = round((perf_counter() - float(started)) * 1000)
        except Exception:
            step["duration_ms"] = None


def visible_step_summary(status: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for step in status.get("steps", []) or []:
        state = str(step.get("status") or "unknown").upper()
        label = str(step.get("label") or step.get("key") or "Step")
        message = str(step.get("message") or "")
        duration = step.get("duration_ms")
        suffix = f" ({duration} ms)" if duration is not None else ""
        lines.append(f"{state}: {label} — {message}{suffix}")
    return lines
