"""
ASA Patch 32B — Earnings Provider Reconciliation Service

Instruments the earnings discovery pipeline to produce a structured
EARNINGS_PROVIDER_RECONCILIATION log block per run.

Key Alpha Vantage diagnosis:
  Alpha Vantage's EARNINGS_CALENDAR CSV endpoint provides date data but
  no session/hour information. is_timestamp_confirmed is always False for
  AV items by design — this is not a bug, but a known limitation of that
  endpoint. AV is still valuable as a secondary date-confirmation source.
"""

from __future__ import annotations

from typing import Any, Callable


def build_earnings_reconciliation(
    earnings_trade_discovery: dict[str, Any],
    configured_provider_names: list[str],
) -> dict[str, Any]:
    """Build a structured reconciliation report from merged earnings discovery results.

    Examines each merged event's sources_seen / provider_errors to reconstruct
    per-provider item counts and status without re-calling any provider.
    """
    items: list[dict[str, Any]] = list((earnings_trade_discovery or {}).get("items") or [])
    provider_errors_raw: list[str] = []

    # Collect provider_errors appended to events by CompositeEarningsProvider
    for ev in (items or []):
        if not isinstance(ev, dict):
            continue
        for err in (ev.get("provider_errors") or []):
            err_str = str(err)
            if err_str not in provider_errors_raw:
                provider_errors_raw.append(err_str)

    # Count items contributed by each provider via sources_seen
    items_by_provider: dict[str, int] = {}
    session_confirmed_by_provider: dict[str, int] = {}
    for ev in (items or []):
        if not isinstance(ev, dict):
            continue
        for src in (ev.get("sources_seen") or []):
            src = str(src).strip().lower()
            items_by_provider[src] = items_by_provider.get(src, 0) + 1
            if ev.get("is_timestamp_confirmed"):
                session_confirmed_by_provider[src] = session_confirmed_by_provider.get(src, 0) + 1

    # Derive per-provider status
    provider_reports: list[dict[str, Any]] = []
    for name in (configured_provider_names or []):
        key = name.lower().replace("-", "_").replace(" ", "_")
        count = items_by_provider.get(key, 0)
        confirmed = session_confirmed_by_provider.get(key, 0)

        # Find any error for this provider in provider_errors
        error_str = next((e for e in provider_errors_raw if e.lower().startswith(key + ":")), None)
        if error_str:
            err_type = error_str.split(":", 2)[1].strip() if ":" in error_str else "unknown_error"
            status = err_type
        elif count > 0:
            status = "ok"
        else:
            status = "empty_response"

        # Alpha Vantage diagnosis: session data is structurally unsupported
        av_session_note = None
        if key in ("alphavantage", "alpha_vantage", "av"):
            av_session_note = "session_data_unsupported_by_csv_endpoint"
            if count > 0:
                status = "ok_no_session"

        report: dict[str, Any] = {
            "provider": name,
            "status": status,
            "items": count,
            "session_confirmed": confirmed,
        }
        if av_session_note:
            report["note"] = av_session_note
        if error_str:
            report["error"] = error_str
        provider_reports.append(report)

    # Conflict and agreement summary
    conflict_count = sum(1 for ev in items if isinstance(ev, dict) and (ev.get("earnings_source_conflict") or ev.get("date_conflict")))
    multi_source_count = sum(1 for ev in items if isinstance(ev, dict) and len(ev.get("sources_seen") or []) >= 2)
    total_count = len(items)
    agreement_pct = round(multi_source_count / total_count * 100, 1) if total_count else 0.0

    return {
        "provider_reports": provider_reports,
        "total_events": total_count,
        "conflict_count": conflict_count,
        "multi_source_count": multi_source_count,
        "agreement_pct": agreement_pct,
        "provider_errors": provider_errors_raw,
        "schema_version": "32B.v1",
    }


def log_earnings_provider_reconciliation(
    reconciliation: dict[str, Any],
    log_print: Callable[[str], None] | None = None,
) -> str:
    """Emit the EARNINGS_PROVIDER_RECONCILIATION log block.

    Format:
      EARNINGS_PROVIDER_RECONCILIATION provider=finnhub status=ok items=N session_confirmed=N
        | provider=alphavantage status=ok_no_session items=N note=session_data_unsupported_by_csv_endpoint
        | conflicts=N multi_source=N agreement=X%
    """
    log = log_print or print
    try:
        parts: list[str] = []
        for rpt in (reconciliation.get("provider_reports") or []):
            seg = f"provider={rpt.get('provider', '?')} status={rpt.get('status', '?')} items={rpt.get('items', 0)} session_confirmed={rpt.get('session_confirmed', 0)}"
            if rpt.get("note"):
                seg += f" note={rpt['note']}"
            parts.append(seg)

        summary = (
            f"conflicts={reconciliation.get('conflict_count', 0)} "
            f"multi_source={reconciliation.get('multi_source_count', 0)} "
            f"agreement={reconciliation.get('agreement_pct', 0)}%"
        )
        parts.append(summary)

        line = "EARNINGS_PROVIDER_RECONCILIATION " + " | ".join(parts)
        try:
            log(line, flush=True)
        except TypeError:
            log(line)
        return line
    except Exception:
        fallback = "EARNINGS_PROVIDER_RECONCILIATION error=log_failed"
        try:
            log(fallback, flush=True)
        except TypeError:
            log(fallback)
        return fallback


def run_earnings_reconciliation(
    earnings_trade_discovery: dict[str, Any],
    configured_provider_names: list[str],
    log_print: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Build reconciliation report and emit log block. Returns the report."""
    try:
        report = build_earnings_reconciliation(earnings_trade_discovery, configured_provider_names)
        log_earnings_provider_reconciliation(report, log_print=log_print)
        return report
    except Exception:
        return {"error": "reconciliation_failed", "schema_version": "32B.v1"}
