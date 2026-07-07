"""30B — Strategy observation summary builders for dev/admin views."""

from __future__ import annotations

from typing import Any

from app.db.strategy_observations import (
    global_summary,
    run_summary,
    read_observations,
    OBSERVATION_SCHEMA_VERSION,
)


def build_strategy_observation_summary(days: int = 7, db_path: str | None = None) -> dict[str, Any]:
    """Rolling summary for the last N days. Safe on any error."""
    summary = global_summary(days=days, db_path=db_path)
    summary["observation_schema_version"] = OBSERVATION_SCHEMA_VERSION
    return {
        "provider_calls_triggered": False,
        "read_only": True,
        "observation_schema_version": OBSERVATION_SCHEMA_VERSION,
        "summary": summary,
    }


def build_run_observation_summary(
    run_id: str, db_path: str | None = None
) -> dict[str, Any]:
    """Compact counts for a single run. Safe on any error."""
    if not run_id:
        return {
            "run_id": None,
            "status": "unavailable",
            "provider_calls_triggered": False,
        }
    result = run_summary(run_id=run_id, db_path=db_path)
    result["observation_schema_version"] = OBSERVATION_SCHEMA_VERSION
    result["provider_calls_triggered"] = False
    return result


def build_observation_list(
    *,
    run_id: str | None = None,
    strategy_id: str | None = None,
    ticker: str | None = None,
    status_bucket: str | None = None,
    verdict: str | None = None,
    days: int | None = None,
    limit: int = 100,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Compact filtered list for the observation list endpoint."""
    rows = read_observations(
        run_id=run_id,
        strategy_id=strategy_id,
        ticker=ticker,
        status_bucket=status_bucket,
        verdict=verdict,
        days=days,
        limit=limit,
        db_path=db_path,
    )
    return {
        "provider_calls_triggered": False,
        "read_only": True,
        "observation_schema_version": OBSERVATION_SCHEMA_VERSION,
        "count": len(rows),
        "observations": rows,
    }
