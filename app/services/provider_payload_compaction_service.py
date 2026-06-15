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

TOP_LEVEL_COMPACTORS = {
    "_calendar_ranking",
    "_calendar_opportunity_cache",
    "_calendar_spread_candidates",
    "_daily_opportunity_engine",
    "_earnings_calendar_strategy",
    "_earnings_discovery_quality",
    "_earnings_events",
    "_earnings_mini_backtest",
    "_earnings_trade_discovery",
    "_forward_factor_strategy",
    "_open_options_positions",
    "_pipeline_status",
    "_portfolio_gap",
    "_run_data_context",
    "_skew_momentum_vertical_cache",
    "_skew_momentum_vertical_strategy",
    "_stock_momentum_strategy",
    "_strategy_opportunity_registry",
    "_strategy_results",
    "_unified_calendar_trade_engine",
    "_watchlist_candidates",
    "_watchlist_review",
}


def compact_tradier_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """Remove raw provider collections while preserving report and audit fields."""
    raw = snapshot if isinstance(snapshot, dict) else {}
    compact = _compact_snapshot_body(raw)
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
    compact_value = compact if isinstance(compact, dict) else _compact_snapshot_body(raw)
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


def _compact_top_level_section(key: str, value: Any) -> Any:
    lowered = str(key).lower()
    if lowered in TOP_LEVEL_COMPACTORS:
        return _summary_for_section(lowered, value)
    return _compact_value(value, lowered)


def _compact_snapshot_body(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): _compact_top_level_section(str(key), value)
        for key, value in (raw or {}).items()
    }


def _summary_for_section(section: str, value: Any) -> Any:
    if section == "_strategy_results":
        return _compact_strategy_results(value)
    if section == "_strategy_opportunity_registry":
        return _compact_registry(value)
    return _compact_section_payload(section, value)


def _compact_strategy_results(value: Any) -> dict[str, Any]:
    results = value if isinstance(value, dict) else {}
    compact: dict[str, Any] = {}
    for strategy_id, payload in results.items():
        if not isinstance(payload, dict):
            compact[str(strategy_id)] = _collection_summary(payload)
            continue
        compact[str(strategy_id)] = {
            key: _compact_summary_value(item)
            for key, item in payload.items()
            if key in {
                "strategy_id",
                "strategy_label",
                "enabled",
                "ran",
                "mode",
                "run_mode",
                "pass_count",
                "watch_count",
                "fail_count",
                "skipped_count",
                "summary",
                "signal_tier_counts",
                "selected_audit",
            }
        }
        compact[str(strategy_id)]["row_summary"] = _row_collection_summary(payload)
    return compact


def _compact_registry(value: Any) -> dict[str, Any]:
    payload = value if isinstance(value, dict) else {}
    output = {
        key: _compact_summary_value(item)
        for key, item in payload.items()
        if key in {"write_count", "forward_factor_observation_history"}
    }
    if "recent" in payload:
        output["recent_summary"] = _list_item_summary(payload.get("recent"))
    return output


def _compact_section_payload(section: str, value: Any) -> dict[str, Any]:
    payload = value if isinstance(value, dict) else {}
    output = {
        "section": section,
        "item_summary": _list_item_summary(_candidate_rows(payload)),
        "keys": [str(key) for key in list(payload)[:20]] if isinstance(payload, dict) else [],
    }
    for key in ("summary", "enabled", "has_data", "ran", "mode", "run_mode", "source", "errors", "warnings", "provider_status"):
        if key in payload:
            output[key] = _compact_summary_value(payload.get(key))
    if "rows" in payload or "items" in payload or "actions" in payload or "new_trade_rows" in payload or "calendars" in payload or "recent" in payload:
        output["row_summary"] = _row_collection_summary(payload)
    return output


def _row_collection_summary(payload: dict[str, Any]) -> dict[str, Any]:
    rows = _candidate_rows(payload)
    return {
        "count": len(rows),
        "sample": [_compact_row_sample(item) for item in rows[:5]],
    }


def _candidate_rows(payload: dict[str, Any]) -> list[Any]:
    for key in ("rows", "items", "actions", "new_trade_rows", "calendars", "recent", "pass_items", "watch_items", "blocked_items"):
        if isinstance(payload.get(key), list):
            return payload.get(key) or []
    return []


def _compact_row_sample(item: Any) -> Any:
    if not isinstance(item, dict):
        return _compact_value(item)
    keep = {}
    for key in (
        "ticker",
        "symbol",
        "strategy_id",
        "strategy_label",
        "verdict",
        "final_verdict",
        "signal_tier",
        "action",
        "direction",
        "score",
        "actionability_score",
        "signal_score",
        "expiration",
        "front_expiration",
        "back_expiration",
        "selected_expiration",
        "selected_pair",
        "selected_structure",
        "structure_status",
        "liquidity_status",
        "primary_blocker",
        "main_reason",
        "main_blocker",
    ):
        if key in item:
            keep[key] = _compact_value(item.get(key), key.lower())
    if "legs" in item and isinstance(item["legs"], list):
        keep["legs"] = [
            {
                sub_key: leg.get(sub_key)
                for sub_key in ("symbol", "option_type", "strike", "bid", "ask", "mid", "delta", "open_interest", "volume")
                if isinstance(leg, dict) and sub_key in leg
            }
            for leg in item["legs"][:4]
            if isinstance(leg, dict)
        ]
    return keep or {"keys": [str(key) for key in list(item)[:10]]}


def _compact_summary_value(value: Any, *, depth: int = 0) -> Any:
    """Bound repeated diagnostic collections while preserving summary scalars."""
    if isinstance(value, dict):
        return {
            str(key): _compact_summary_value(item, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list):
        if all(not isinstance(item, (dict, list)) for item in value):
            return value[:10]
        if depth >= 2:
            return {"count": len(value)}
        return {
            "count": len(value),
            "sample": [
                _compact_row_sample(item)
                for item in value[:3]
            ],
        }
    return value


def _list_item_summary(value: Any) -> dict[str, Any]:
    items = value if isinstance(value, list) else []
    return {
        "count": len(items),
        "sample": [_compact_row_sample(item) for item in items[:5]],
    }


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
