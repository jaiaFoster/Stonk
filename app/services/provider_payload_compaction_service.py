"""Compact provider payloads for stored reports without changing live strategy data."""

from __future__ import annotations

import json
from typing import Any

from app import config


HEAVY_PROVIDER_KEYS = {
    "bars",
    "chain",
    "chains",
    "chains_by_expiration",
    "contracts",
    "option_chain",
    "option_chains",
    "raw",
    "raw_json",
    "raw_payload",
    "raw_provider_payload",
}


def compact_tradier_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """Remove raw provider collections while preserving report and audit fields."""
    raw = snapshot if isinstance(snapshot, dict) else {}
    compact = _compact_value(raw)
    if not isinstance(compact, dict):
        compact = {}
    compact["_provider_payload_budget"] = build_provider_payload_budget(raw, compact=compact)
    compact["_raw_provider_archive"] = {
        "available": bool(raw),
        "detail_section": "provider_raw",
        "storage": "compressed",
    }
    return compact


def build_provider_payload_budget(
    snapshot: dict[str, Any] | None,
    *,
    compact: dict[str, Any] | None = None,
    oversized_threshold_bytes: int | None = None,
) -> dict[str, Any]:
    raw = snapshot if isinstance(snapshot, dict) else {}
    compact_value = compact if isinstance(compact, dict) else _compact_value(raw)
    threshold = int(oversized_threshold_bytes or config.PROVIDER_PAYLOAD_BUDGET_BYTES)
    raw_bytes = _json_bytes(raw)
    compact_bytes = _json_bytes(compact_value)
    top_level = {
        str(key): _json_bytes(value)
        for key, value in raw.items()
    }
    largest_name, largest_bytes = max(top_level.items(), key=lambda item: item[1], default=(None, 0))
    return {
        "tradier_snapshot_bytes": raw_bytes,
        "compact_tradier_snapshot_bytes": compact_bytes,
        "saved_bytes": max(0, raw_bytes - compact_bytes),
        "reduction_pct": round(((raw_bytes - compact_bytes) / raw_bytes) * 100, 1) if raw_bytes else 0.0,
        "oversized_threshold_bytes": threshold,
        "oversized": raw_bytes > threshold,
        "largest_provider_section": {"name": largest_name, "bytes": largest_bytes},
        "top_provider_sections": [
            {"name": name, "bytes": size}
            for name, size in sorted(top_level.items(), key=lambda item: item[1], reverse=True)[:10]
        ],
    }


def _compact_value(value: Any, key: str | None = None) -> Any:
    if key in HEAVY_PROVIDER_KEYS:
        return _collection_summary(value)
    if isinstance(value, dict):
        return {item_key: _compact_value(item, str(item_key).lower()) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_compact_value(item) for item in value]
    return value


def _collection_summary(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {
            "compacted": True,
            "kind": "mapping",
            "count": len(value),
            "keys": [str(key) for key in list(value)[:20]],
        }
    if isinstance(value, list):
        return {"compacted": True, "kind": "list", "count": len(value)}
    return {"compacted": True, "kind": type(value).__name__, "present": value is not None}


def _json_bytes(value: Any) -> int:
    try:
        return len(json.dumps(value, default=str, separators=(",", ":")).encode("utf-8"))
    except Exception:
        return 0
