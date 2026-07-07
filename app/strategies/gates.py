"""Gate helpers for the strategies package.

Re-exports the canonical gate primitives from strategy_gate_service and adds
group/filter utilities specific to the universal row format.
"""

from __future__ import annotations

from typing import Any

from app.services.strategy_gate_service import (
    GATE_STATUSES,
    gate_status_rank,
    has_blocking_gate_failure,
    make_gate,
    normalize_gate_status,
    summarize_gates,
)

# The six status values that universal rows use (subset of GATE_STATUSES).
VALID_GATE_STATUSES: frozenset[str] = frozenset({
    "pass", "watch", "fail", "unknown", "skipped", "dry_run",
})

__all__ = [
    "GATE_STATUSES",
    "VALID_GATE_STATUSES",
    "make_gate",
    "make_gate_group",
    "normalize_gate_status",
    "summarize_gates",
    "get_failed_gates",
    "get_watch_gates",
    "has_blocking_gate_failure",
    "gate_status_rank",
]


def make_gate_group(name: str, gates: list[dict[str, Any]]) -> dict[str, Any]:
    """Wrap a flat gate list into a named group with an inline summary."""
    return {
        "group": name,
        "gates": gates,
        "summary": summarize_gates(gates),
    }


def _flatten(gates: list[dict[str, Any]] | dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(gates, dict):
        return [g for g in gates.values() if isinstance(g, dict)]
    return [g for g in (gates or []) if isinstance(g, dict)]


def get_failed_gates(gates: list[dict[str, Any]] | dict[str, Any]) -> list[dict[str, Any]]:
    """Return gates with status 'fail' or 'error'."""
    return [g for g in _flatten(gates) if g.get("status") in ("fail", "error")]


def get_watch_gates(gates: list[dict[str, Any]] | dict[str, Any]) -> list[dict[str, Any]]:
    """Return gates with status 'watch'."""
    return [g for g in _flatten(gates) if g.get("status") == "watch"]
