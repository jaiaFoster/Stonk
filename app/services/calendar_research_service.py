"""Stateless calendar research helpers for /research/calendar-backtest."""

from __future__ import annotations

from html import escape
from typing import Any

from app.services.earnings_mini_backtest_service import build_manual_calendar_backtest


def run_calendar_backtest_research(params: dict[str, Any], log_print=None) -> dict[str, Any]:
    ticker = str(params.get("ticker") or "").upper().strip()
    mode = str(params.get("mode") or "diagnostic").lower().strip()
    if mode not in {"diagnostic", "eligibility"}:
        mode = "diagnostic"
    result = build_manual_calendar_backtest(ticker=ticker, mode=mode, params=params, log_print=log_print)
    result["disclaimer"] = "Research only. This does not create, track, modify, or close a trade."
    return result


def render_calendar_backtest_research_html(report: dict[str, Any]) -> str:
    ticker = escape(str(report.get("ticker") or "UNKNOWN"))
    mode = escape(str(report.get("mode") or "diagnostic"))
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    errors = [str(e) for e in (report.get("errors") or [])]
    interpretation = escape(str(summary.get("interpretation") or report.get("diagnostic_interpretation") or "No interpretation available."))
    rows = ""
    for event in report.get("events", []) or []:
        rows += (
            "<tr>"
            f"<td>{escape(str(event.get('earnings_date') or 'Unknown'))}</td>"
            f"<td>{escape(str(event.get('session_label') or 'Unknown'))}</td>"
            f"<td>{escape(str(event.get('pre_event_runup_pct') or '—'))}%</td>"
            f"<td>{escape(str(event.get('earnings_gap_pct') or '—'))}%</td>"
            f"<td>{escape(str(event.get('max_abs_event_move_pct') or '—'))}%</td>"
            "</tr>"
        )
    if not rows:
        rows = '<tr><td colspan="5">No historical events/candles available for this diagnostic.</td></tr>'

    error_html = ""
    if errors:
        error_html = "<h2>Errors / Skips</h2><ul>" + "".join(f"<li>{escape(e)}</li>" for e in errors[:5]) + "</ul>"

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{ticker} Calendar Backtest Research</title>
  <style>
    body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #17202a; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 16px; }}
    th, td {{ border: 1px solid #d6dde6; padding: 8px; text-align: left; }}
    th {{ background: #f4f6f8; }}
    .pill {{ display: inline-block; padding: 4px 8px; border-radius: 6px; background: #e8eef7; }}
    .note {{ color: #5d6d7e; }}
  </style>
</head>
<body>
  <h1>{ticker} Calendar Backtest Research</h1>
  <p><span class="pill">Mode: {mode}</span> <span class="pill">Status: {escape(str(report.get('mode_status') or report.get('mode') or 'diagnostic'))}</span></p>
  <h2>Historical Earnings Move Summary</h2>
  <p>{interpretation}</p>
  <p class="note">Events: {escape(str(summary.get('event_count', 0)))} | Avg abs move: {escape(str(summary.get('avg_abs_event_move_pct', '—')))}% | Max abs move: {escape(str(summary.get('max_abs_event_move_pct', '—')))}% | Small-move rate: {escape(str(summary.get('small_move_rate_pct', '—')))}%</p>
  {error_html}
  <h2>Historical Events</h2>
  <table>
    <thead><tr><th>Date</th><th>Session</th><th>Pre-run</th><th>Gap</th><th>Max abs move</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
  <p class="note">{escape(str(report.get('disclaimer') or 'Research only; not a tracked trade.'))}</p>
</body>
</html>"""
