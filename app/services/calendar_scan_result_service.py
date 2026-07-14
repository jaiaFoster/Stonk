"""Run-scoped calendar scan result helpers.

Patch 33B replaces prior-run background candidate reuse with an explicit
current-run scan result. The object is intentionally small and serializable so
pipeline consumers can see whether they are reading current, empty, failed, or
timed-out scan data.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


class CalendarScanStatus:
    NOT_REQUESTED = "NOT_REQUESTED"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    COMPLETE_EMPTY = "COMPLETE_EMPTY"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    STALE_FALLBACK = "STALE_FALLBACK"


@dataclass
class CalendarScanResult:
    run_id: str
    scan_id: str
    status: str = CalendarScanStatus.NOT_REQUESTED
    candidates: list[dict[str, Any]] = field(default_factory=list)
    ticker_dispositions: dict[str, Any] = field(default_factory=dict)
    started_at: str | None = None
    completed_at: str | None = None
    reason: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def new_scan_result(run_id: str, scan_id: str) -> CalendarScanResult:
    now = datetime.now(timezone.utc).isoformat()
    return CalendarScanResult(
        run_id=run_id,
        scan_id=scan_id,
        status=CalendarScanStatus.RUNNING,
        started_at=now,
    )


def complete_scan_result(
    result: CalendarScanResult,
    candidates: list[dict[str, Any]] | None,
    *,
    reason: str | None = None,
) -> CalendarScanResult:
    rows = [row for row in (candidates or []) if isinstance(row, dict)]
    result.candidates = rows
    result.status = CalendarScanStatus.COMPLETE if rows else CalendarScanStatus.COMPLETE_EMPTY
    result.reason = reason or ("SCAN_COMPLETE" if rows else "SCAN_COMPLETE_EMPTY")
    result.completed_at = datetime.now(timezone.utc).isoformat()
    for row in result.candidates:
        row.setdefault("scan_id", result.scan_id)
        row.setdefault("scan_run_id", result.run_id)
        row.setdefault("scan_source", "current_run")
    return result


def fail_scan_result(result: CalendarScanResult, error: Exception | str) -> CalendarScanResult:
    result.status = CalendarScanStatus.FAILED
    result.reason = "SCAN_FAILED"
    result.error = str(error)[:300]
    result.completed_at = datetime.now(timezone.utc).isoformat()
    result.candidates = []
    return result
