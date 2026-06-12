"""One truthful formatter for shared market-data availability states."""

from __future__ import annotations

from typing import Any


def data_state_message(state: str | None, *, fetched_at: str | None = None, reason: str | None = None) -> str:
    clean = str(state or "MISSING_PROVIDER_FAILED").upper()
    if clean == "COMPLETE":
        return ""
    if clean == "SKIPPED_DEV_CAP":
        return "Market metrics were not evaluated in this dev run. Reason: skipped by dev data cap."
    if clean == "SKIPPED_PROVIDER_BUDGET":
        return "Market metrics were not evaluated. Reason: shared provider budget was exhausted."
    if clean in {"MISSING_UNSUPPORTED", "MISSING_UNSUPPORTED_TICKER"}:
        return "Market metrics are unavailable for this asset type."
    if clean == "STALE_CACHE_USED":
        timestamp = f" from {fetched_at}" if fetched_at else ""
        return f"Using cached market metrics{timestamp}; live refresh failed."
    if clean == "PARTIAL":
        return reason or "Market metrics are incomplete; required trend confirmation is unavailable."
    return reason or "Market metrics unavailable after provider attempts."


def required_market_metrics_complete(metrics: dict[str, Any] | None) -> bool:
    row = metrics or {}
    return bool(
        row.get("has_data")
        and row.get("data_state") == "COMPLETE"
        and row.get("current_price") is not None
        and int(row.get("bar_count") or 0) >= 200
        and row.get("return_3m_pct") is not None
        and row.get("above_sma_200") is not None
        and row.get("avg_volume_30d") is not None
        and row.get("fresh") is not False
    )
