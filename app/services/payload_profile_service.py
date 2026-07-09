"""Approximate serialized sizes for major report sections."""

from __future__ import annotations

import json
from typing import Any

from app.services.provider_payload_compaction_service import build_provider_payload_budget
from app.services.payload_path_audit_service import largest_json_paths

# TKT-038 / 29.8: Tiered payload budget thresholds
_PAYLOAD_HEALTHY_BYTES  = 750_000    # ≤750KB = healthy
_PAYLOAD_WATCH_BYTES    = 750_000    # 750KB–1MB = watch
_PAYLOAD_WARNING_BYTES  = 1_000_000  # 1MB–2MB = warning
_PAYLOAD_CRITICAL_BYTES = 2_000_000  # >2MB = critical (lowered from 3MB)
_PROVIDER_CALL_WARN     = 200
_STRATEGY_ROW_WARN_BYTES = 50_000   # any single strategy row this large is suspicious
_LARGEST_STRATEGY_ROWS_N = 5        # number of largest rows to surface in diagnostics

# Legacy alias kept for compatibility
_PAYLOAD_WARN_BYTES = _PAYLOAD_WARNING_BYTES


def json_bytes(value: Any) -> int:
    try:
        return len(json.dumps(value, default=str, separators=(",", ":")).encode("utf-8"))
    except Exception:
        return 0


def build_payload_size_profile(
    payload: str, positions: Any, news: Any, recommendations: Any,
    snapshot: dict[str, Any], log: list[str], report_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    strategy = snapshot.get("_strategy_results", {}) or {}
    calendar_data = snapshot.get("_unified_calendar_engine") or snapshot.get("_calendar_ranking")
    skew_data = strategy.get("skew_momentum_vertical") or snapshot.get("_skew_momentum_vertical_strategy")
    ff_data = strategy.get("forward_factor_calendar") or snapshot.get("_forward_factor_strategy")
    stock_data = strategy.get("stock_momentum") or snapshot.get("_stock_momentum_strategy")

    legacy_report_summary_bytes = json_bytes(report_summary or {})
    sections = {
        "payload_text": len((payload or "").encode("utf-8")),
        # Active hot-path summary bytes are filled later after compact manifest
        # construction. Legacy report summary remains measured separately so it
        # cannot masquerade as the dashboard/API summary size.
        "report_summary_json": 0,
        "legacy_report_summary_json": legacy_report_summary_bytes,
        "tradier_snapshot": json_bytes(snapshot),
        "positions": json_bytes(positions),
        "news": json_bytes(news),
        "recommendations": json_bytes(recommendations),
        "calendar": json_bytes(calendar_data),
        "skew": json_bytes(skew_data),
        "forward_factor": json_bytes(ff_data),
        "stock_momentum": json_bytes(stock_data),
        "portfolio_gap": json_bytes(snapshot.get("_portfolio_gap")),
        "daily_opportunity": json_bytes(snapshot.get("_daily_opportunity_engine")),
        "pipeline_status": json_bytes(snapshot.get("_pipeline_status")),
        "data_coverage": json_bytes(snapshot.get("_data_coverage")),
        "log": json_bytes(log),
    }
    provider_budget = build_provider_payload_budget(snapshot)
    sections["tradier_snapshot_compact"] = provider_budget["compact_tradier_snapshot_bytes"]
    summary_json_bytes = sections.get("report_summary_json", 0)
    largest_top_level_keys = _largest_snapshot_keys(snapshot)
    largest_report_summary_paths = largest_json_paths(report_summary or {}, root="report_summary", limit=10)
    largest_snapshot_paths = largest_json_paths(snapshot or {}, root="tradier_snapshot", limit=10)

    # TKT-038: per-strategy row-level breakdown.
    strategy_row_profile = _strategy_row_profile(
        calendar=calendar_data, skew=skew_data, ff=ff_data, stock=stock_data,
    )

    # 29.8: payload budget status tier.
    summary_payload_status = _payload_status(summary_json_bytes)

    # 29.8: largest individual rows across all strategies.
    largest_strategy_rows = _largest_strategy_rows(
        calendar=calendar_data, skew=skew_data, ff=ff_data, stock=stock_data,
    )

    return {
        "total_profiled_bytes": sum(sections.values()),
        # Backward-compatible alias: historical tests/diagnostics expect this
        # to reflect the supplied report_summary object. Do not use it as the
        # active hot-path warning source; `summary_payload_status` below is
        # computed from `sections_bytes.report_summary_json`.
        "summary_json_bytes": legacy_report_summary_bytes,
        "active_summary_json_bytes": summary_json_bytes,
        "legacy_report_summary_json_bytes": legacy_report_summary_bytes,
        "compact_summary_json_bytes": 0,
        "full_archive_blob_bytes": 0,
        "raw_provider_archive_blob_bytes": 0,
        "api_hot_path_bytes": 0,
        "summary_payload_status": summary_payload_status,
        "summary_payload_limit_bytes": _PAYLOAD_HEALTHY_BYTES,
        "summary_payload_watch_bytes": _PAYLOAD_WATCH_BYTES,
        "summary_payload_warning_bytes": _PAYLOAD_WARNING_BYTES,
        "summary_payload_critical_bytes": _PAYLOAD_CRITICAL_BYTES,
        "sections_bytes": sections,
        "strategy_row_profile": strategy_row_profile,
        "largest_strategy_rows": largest_strategy_rows,
        "provider_payload_budget": provider_budget,
        "largest_top_level_keys": largest_top_level_keys,
        "largest_report_summary_paths": largest_report_summary_paths,
        "largest_snapshot_paths": largest_snapshot_paths,
    }


def _payload_status(size_bytes: int) -> str:
    if size_bytes <= _PAYLOAD_HEALTHY_BYTES:
        return "healthy"
    if size_bytes <= _PAYLOAD_WARNING_BYTES:
        return "watch"
    if size_bytes <= _PAYLOAD_CRITICAL_BYTES:
        return "warning"
    return "critical"


def build_payload_warnings(profile: dict[str, Any], provider_calls: int = 0) -> list[dict[str, Any]]:
    """TKT-038 / 29.8: Emit tiered payload warnings when thresholds are exceeded."""
    warnings: list[dict[str, Any]] = []
    summary_bytes = (
        profile.get("api_hot_path_bytes")
        or profile.get("compact_summary_json_bytes")
        or profile.get("summary_json_bytes")
        or profile.get("total_profiled_bytes")
        or 0
    )
    if profile.get("api_hot_path_bytes") or profile.get("compact_summary_json_bytes"):
        status = _payload_status(summary_bytes)
    else:
        status = profile.get("summary_payload_status") or _payload_status(summary_bytes)

    if status == "critical":
        largest = profile.get("largest_top_level_keys") or []
        top_keys = ", ".join(f"{k['key']}={k['bytes'] // 1024}KB" for k in largest[:3]) if largest else ""
        warnings.append({
            "name": "payload_size_warning",
            "level": "critical",
            "message": (
                f"Summary payload is {summary_bytes // 1024}KB — exceeds 2MB critical threshold. "
                + (f"Largest contributors: {top_keys}." if top_keys else "")
            ),
            "threshold_bytes": _PAYLOAD_CRITICAL_BYTES,
            "actual_bytes": summary_bytes,
            "summary_payload_status": "critical",
        })
    elif status == "warning":
        warnings.append({
            "name": "payload_size_warning",
            "level": "warning",
            "message": f"Summary payload is {summary_bytes // 1024}KB — exceeds 1MB warning threshold.",
            "threshold_bytes": _PAYLOAD_WARNING_BYTES,
            "actual_bytes": summary_bytes,
            "summary_payload_status": "warning",
        })
    elif status == "watch":
        warnings.append({
            "name": "payload_size_warning",
            "level": "watch",
            "message": f"Summary payload is {summary_bytes // 1024}KB — in watch zone (750KB–1MB).",
            "threshold_bytes": _PAYLOAD_WATCH_BYTES,
            "actual_bytes": summary_bytes,
            "summary_payload_status": "watch",
        })

    if provider_calls > _PROVIDER_CALL_WARN:
        warnings.append({
            "name": "provider_call_warning",
            "level": "warn",
            "message": f"Provider calls this run: {provider_calls} — exceeds warning threshold of {_PROVIDER_CALL_WARN}.",
            "threshold": _PROVIDER_CALL_WARN,
            "actual": provider_calls,
        })
    return warnings


def compact_payload_log(profile: dict[str, Any]) -> str:
    sections = profile.get("sections_bytes", {}) or {}
    largest = sorted(sections.items(), key=lambda item: item[1], reverse=True)[:5]
    status = profile.get("summary_payload_status", "unknown")
    hot = int(profile.get("api_hot_path_bytes") or profile.get("compact_summary_json_bytes") or profile.get("active_summary_json_bytes") or 0)
    legacy = int(profile.get("legacy_report_summary_json_bytes") or sections.get("legacy_report_summary_json") or 0)
    archive = int(profile.get("full_archive_blob_bytes") or 0)
    return (
        f"PayloadProfile[{status}]: hot_path={hot}B, legacy_archive={legacy}B, "
        f"full_archive={archive}B, largest="
        + ", ".join(f"{key}={value}B" for key, value in largest)
    )


def _strategy_row_profile(
    *,
    calendar: Any = None,
    skew: Any = None,
    ff: Any = None,
    stock: Any = None,
) -> dict[str, Any]:
    """TKT-038: Per-strategy row-level byte breakdown for payload diagnostics."""

    def _rows_bytes(data: Any) -> tuple[int, int]:
        """Return (total_bytes, row_count) for a strategy result dict."""
        if not isinstance(data, dict):
            return 0, 0
        rows = data.get("canonical_opportunities") or data.get("rows") or data.get("items") or []
        if not isinstance(rows, list):
            return 0, 0
        total = sum(json_bytes(r) for r in rows if isinstance(r, dict))
        return total, len(rows)

    cal_bytes, cal_rows = _rows_bytes(calendar)
    skew_bytes, skew_rows = _rows_bytes(skew)
    ff_bytes, ff_rows = _rows_bytes(ff)
    stock_bytes, stock_rows = _rows_bytes(stock)
    strategy_results_bytes = cal_bytes + skew_bytes + ff_bytes + stock_bytes

    return {
        "strategy_results_bytes": strategy_results_bytes,
        "calendar_rows_bytes": cal_bytes,
        "calendar_row_count": cal_rows,
        "skew_rows_bytes": skew_bytes,
        "skew_row_count": skew_rows,
        "ff_rows_bytes": ff_bytes,
        "ff_row_count": ff_rows,
        "stock_rows_bytes": stock_bytes,
        "stock_row_count": stock_rows,
    }


def _largest_strategy_rows(
    *,
    calendar: Any = None,
    skew: Any = None,
    ff: Any = None,
    stock: Any = None,
    top_n: int = _LARGEST_STRATEGY_ROWS_N,
) -> list[dict[str, Any]]:
    """Return the top-N largest individual strategy rows by serialized size."""
    candidates: list[dict[str, Any]] = []
    sources = [
        ("earnings_calendar", calendar),
        ("skew_momentum_vertical", skew),
        ("forward_factor_calendar", ff),
        ("stock_momentum", stock),
    ]
    for strategy_id, data in sources:
        if not isinstance(data, dict):
            continue
        rows = data.get("canonical_opportunities") or data.get("rows") or data.get("items") or []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            size = json_bytes(row)
            candidates.append({
                "strategy_id": strategy_id,
                "ticker": str(row.get("ticker") or ""),
                "bytes": size,
                "large": size >= _STRATEGY_ROW_WARN_BYTES,
                "verdict": str(row.get("verdict") or row.get("action") or ""),
            })
    return sorted(candidates, key=lambda x: x["bytes"], reverse=True)[:top_n]


def _largest_snapshot_keys(snapshot: dict[str, Any], top_n: int = 10) -> list[dict[str, Any]]:
    """Return top-N snapshot keys by approximate serialized byte size."""
    if not isinstance(snapshot, dict):
        return []
    sized = []
    for key, value in snapshot.items():
        sized.append({"key": key, "bytes": json_bytes(value)})
    return sorted(sized, key=lambda x: x["bytes"], reverse=True)[:top_n]
