"""
app/services/earnings_mini_backtest_service.py — Candle-based Earnings Mini-Backtest v1.

This is intentionally not a historical options P/L simulator. Tradier does not
provide historical option-chain surfaces in this app yet, so this module uses
historical stock candles around prior earnings to estimate whether the underlying
has tended to gap, trend, fade, or stay inside a manageable range.

Important project rule: this module only runs for calendar candidates that pass
all ranking criteria. It does not waste API calls backtesting failed setups.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from statistics import mean
from typing import Any, Callable

from app import config
from app.providers.earnings_provider import (
    EarningsAuthError,
    EarningsProviderError,
    EarningsRateLimitError,
    get_provider,
)
from app.providers.tradier_provider import TradierProvider
from app.utils.log_safety import sanitize_for_log

LogFn = Callable[[str], None]


def build_earnings_mini_backtest(
    calendar_ranking: dict[str, Any] | None,
    log_print: LogFn | None = None,
) -> dict[str, Any]:
    logger = log_print or (lambda msg: print(msg, flush=True))
    result: dict[str, Any] = {
        "source": "earnings_mini_backtest_v1",
        "enabled": bool(getattr(config, "CALENDAR_BACKTEST_ENABLED", True)),
        "has_data": False,
        "items": [],
        "summary": {},
        "errors": [],
        "notes": [
            "Candle-based underlying move study only; not historical option P/L.",
            "Eligibility mode runs only for candidates that pass Calendar Ranking v2 and final-verdict criteria.",
        ],
    }

    if not result["enabled"]:
        result["errors"].append("CALENDAR_BACKTEST_ENABLED=false")
        logger("Earnings Mini-Backtest v1 disabled by CALENDAR_BACKTEST_ENABLED=false.")
        return _finalize(result)

    eligible = [row for row in ((calendar_ranking or {}).get("eligible_for_backtest", []) or []) if isinstance(row, dict)]
    if not eligible:
        result["errors"].append("No candidates passed the full criteria gate; mini-backtest intentionally skipped.")
        logger("Earnings Mini-Backtest v1 skipped: no fully-qualified calendar candidates.")
        if bool(getattr(config, "CALENDAR_DIAGNOSTIC_BACKTEST_ENABLED", True)):
            diagnostic_rows = _diagnostic_rows(calendar_ranking, eligible)
            for row in diagnostic_rows[: max(1, int(getattr(config, "CALENDAR_BACKTEST_MAX_CANDIDATES", 3) or 3))]:
                result["items"].append(_diagnostic_item(row))
            logger(f"Diagnostic mini-backtest generated for {len(result['items'])} candidate(s).")
        return _finalize(result)

    provider = get_provider()
    tradier = TradierProvider()
    if not provider.is_configured:
        result["errors"].append("No earnings provider configured for historical earnings lookup.")
        logger("Earnings Mini-Backtest v1 skipped: no earnings provider configured.")
        return _finalize(result)
    if not tradier.is_configured:
        result["errors"].append("TRADIER_ACCESS_TOKEN not configured for historical candles.")
        logger("Earnings Mini-Backtest v1 skipped: TRADIER_ACCESS_TOKEN not configured.")
        return _finalize(result)

    max_candidates = max(1, int(getattr(config, "CALENDAR_BACKTEST_MAX_CANDIDATES", 3) or 3))
    for row in eligible[:max_candidates]:
        item = _backtest_one(row, provider, tradier, logger)
        item["mode"] = "eligibility"
        item["mode_status"] = "eligibility"
        result["items"].append(item)

    if bool(getattr(config, "CALENDAR_DIAGNOSTIC_BACKTEST_ENABLED", True)):
        diagnostic_rows = _diagnostic_rows(calendar_ranking, eligible)
        for row in diagnostic_rows[:max_candidates]:
            result["items"].append(_diagnostic_item(row))
        logger(f"Diagnostic mini-backtest generated for {len(diagnostic_rows[:max_candidates])} candidate(s).")

    return _finalize(result)


def build_manual_calendar_backtest(
    ticker: str,
    mode: str = "diagnostic",
    params: dict[str, Any] | None = None,
    log_print: LogFn | None = None,
) -> dict[str, Any]:
    logger = log_print or (lambda msg: print(msg, flush=True))
    row = {"ticker": ticker, "rank_score": None}
    provider = get_provider()
    tradier = TradierProvider()
    if not ticker:
        return {"ticker": ticker, "mode": mode, "mode_status": "skipped_no_candidate", "has_data": False, "events": [], "summary": {}, "errors": ["Missing ticker."]}
    if not provider.is_configured or not tradier.is_configured:
        return {
            "ticker": ticker,
            "mode": mode,
            "mode_status": "skipped_no_candidate",
            "has_data": False,
            "events": [],
            "summary": {},
            "errors": ["Earnings provider and TRADIER_ACCESS_TOKEN are required for manual historical diagnostics."],
        }
    out = _backtest_one(row, provider, tradier, logger)
    out["mode"] = mode
    out["mode_status"] = mode
    out["requested_params"] = dict(params or {})
    out["diagnostic_interpretation"] = (out.get("summary") or {}).get("interpretation") or "Historical movement diagnostic only; no candidate was persisted or tracked."
    return out


def _diagnostic_rows(calendar_ranking: dict[str, Any] | None, eligible: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not bool(getattr(config, "CALENDAR_DIAGNOSTIC_BACKTEST_ALLOW_FAILED_CANDIDATES", True)):
        return []
    eligible_tickers = {str(row.get("ticker") or "").upper() for row in eligible}
    rows = []
    for row in ((calendar_ranking or {}).get("items", []) or []):
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").upper()
        if not ticker or ticker in eligible_tickers:
            continue
        rows.append(row)
    return rows


def _diagnostic_item(row: dict[str, Any]) -> dict[str, Any]:
    final = row.get("final_verdict") if isinstance(row.get("final_verdict"), dict) else {}
    blocker = str(final.get("main_blocker") or row.get("main_blocker") or "")
    hard = str(final.get("hard_fail_reason") or "")
    untradeable = "spread" in hard.lower() or "liquidity" in hard.lower() or "untradeable" in blocker.lower()
    status = "skipped_untradeable" if untradeable and bool(getattr(config, "CALENDAR_DIAGNOSTIC_BACKTEST_SKIP_IF_UNTRADEABLE", True)) else "diagnostic"
    explanation = "Diagnostic backtest not run. Main blocker is execution quality." if status == "skipped_untradeable" else "Diagnostic context available; failed candidate remains ineligible."
    return {
        "ticker": row.get("ticker"),
        "ranking_score": row.get("rank_score"),
        "mode": "diagnostic",
        "mode_status": status,
        "has_data": False,
        "events": [],
        "summary": {"event_count": 0, "interpretation": explanation},
        "errors": [hard or blocker or "Candidate failed final verdict criteria."],
    }


def _backtest_one(row: dict[str, Any], earnings_provider: Any, tradier: TradierProvider, logger: LogFn) -> dict[str, Any]:
    ticker = str(row.get("ticker") or "").upper().strip()
    lookback_days = int(getattr(config, "CALENDAR_BACKTEST_LOOKBACK_DAYS", 900) or 900)
    max_events = int(getattr(config, "CALENDAR_BACKTEST_MAX_EVENTS", 10) or 10)
    start = date.today() - timedelta(days=lookback_days)
    end = date.today() - timedelta(days=1)
    out: dict[str, Any] = {
        "ticker": ticker,
        "ranking_score": row.get("rank_score"),
        "has_data": False,
        "events": [],
        "summary": {},
        "errors": [],
    }

    if not ticker:
        out["errors"].append("Missing ticker.")
        return out

    try:
        earnings = earnings_provider.get_earnings_calendar(ticker, start, end)
    except (EarningsRateLimitError, EarningsAuthError, EarningsProviderError, Exception) as exc:
        safe = sanitize_for_log(exc, [config.FINNHUB_API_KEY, config.ALPHA_VANTAGE_API_KEY, config.RUN_TOKEN])
        out["errors"].append(str(safe))
        logger(f"Earnings Mini-Backtest {ticker}: earnings history unavailable — {safe}")
        return out

    parsed_events = []
    for event in earnings or []:
        event_date = _parse_date(event.get("earnings_date") or event.get("date"))
        if event_date and event_date < date.today():
            parsed_events.append((event_date, event))
    parsed_events.sort(key=lambda pair: pair[0], reverse=True)
    parsed_events = parsed_events[:max_events]
    if not parsed_events:
        out["errors"].append("No historical earnings events returned by provider.")
        logger(f"Earnings Mini-Backtest {ticker}: no historical events returned.")
        return out

    history_start = (parsed_events[-1][0] - timedelta(days=14)).isoformat()
    history_end = (parsed_events[0][0] + timedelta(days=7)).isoformat()
    try:
        candles = tradier.get_historical_quotes(ticker, history_start, history_end, interval="daily")
    except Exception as exc:
        safe = sanitize_for_log(exc, [config.TRADIER_ACCESS_TOKEN, config.RUN_TOKEN])
        out["errors"].append(str(safe))
        logger(f"Earnings Mini-Backtest {ticker}: candle history unavailable — {safe}")
        return out

    candle_by_date = {_parse_date(c.get("date")): c for c in candles if _parse_date(c.get("date"))}
    rows = []
    for event_date, event in parsed_events:
        analyzed = _analyze_event(event_date, event, candle_by_date)
        if analyzed:
            rows.append(analyzed)

    out["events"] = rows
    out["has_data"] = bool(rows)
    out["summary"] = _summarize(rows)
    logger(f"Earnings Mini-Backtest {ticker}: analyzed {len(rows)}/{len(parsed_events)} historical event(s).")
    return out


def _analyze_event(event_date: date, event: dict[str, Any], candle_by_date: dict[date, dict[str, Any]]) -> dict[str, Any] | None:
    entry_days_before = int(getattr(config, "CALENDAR_BACKTEST_ENTRY_DAYS_BEFORE", 7) or 7)
    exit_days_after = int(getattr(config, "CALENDAR_BACKTEST_EXIT_DAYS_AFTER", 1) or 1)
    entry_candle = _nearest_candle_on_or_before(candle_by_date, event_date - timedelta(days=entry_days_before), max_back=5)
    pre_candle = _nearest_candle_on_or_before(candle_by_date, event_date - timedelta(days=1), max_back=5)
    event_candle = _nearest_candle_on_or_after(candle_by_date, event_date, max_forward=3)
    exit_candle = _nearest_candle_on_or_after(candle_by_date, event_date + timedelta(days=exit_days_after), max_forward=5)
    if not entry_candle or not pre_candle or not event_candle:
        return None

    entry_close = _num(entry_candle.get("close"))
    pre_close = _num(pre_candle.get("close"))
    event_open = _num(event_candle.get("open"))
    event_close = _num(event_candle.get("close"))
    exit_close = _num(exit_candle.get("close")) if exit_candle else event_close
    if not all(v is not None and v > 0 for v in [entry_close, pre_close, event_open, event_close]):
        return None

    runup_pct = (pre_close - entry_close) / entry_close * 100.0
    gap_pct = (event_open - pre_close) / pre_close * 100.0
    event_close_move_pct = (event_close - pre_close) / pre_close * 100.0
    exit_move_pct = None if exit_close is None else (exit_close - pre_close) / pre_close * 100.0
    max_abs_event_move = max(abs(gap_pct), abs(event_close_move_pct), abs(exit_move_pct or 0.0))

    return {
        "earnings_date": event_date.isoformat(),
        "session_label": event.get("session_label") or "Unknown",
        "entry_close": entry_close,
        "pre_earnings_close": pre_close,
        "event_open": event_open,
        "event_close": event_close,
        "exit_close": exit_close,
        "pre_event_runup_pct": round(runup_pct, 2),
        "earnings_gap_pct": round(gap_pct, 2),
        "event_close_move_pct": round(event_close_move_pct, 2),
        "exit_move_pct": None if exit_move_pct is None else round(exit_move_pct, 2),
        "max_abs_event_move_pct": round(max_abs_event_move, 2),
    }


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"event_count": 0}
    abs_moves = [abs(float(r.get("max_abs_event_move_pct") or 0)) for r in rows]
    gaps = [abs(float(r.get("earnings_gap_pct") or 0)) for r in rows]
    runups = [float(r.get("pre_event_runup_pct") or 0) for r in rows]
    small_move_count = sum(1 for v in abs_moves if v <= 8)
    return {
        "event_count": len(rows),
        "avg_abs_event_move_pct": round(mean(abs_moves), 2),
        "max_abs_event_move_pct": round(max(abs_moves), 2),
        "avg_abs_gap_pct": round(mean(gaps), 2),
        "avg_pre_event_runup_pct": round(mean(runups), 2),
        "small_move_rate_pct": round(small_move_count / len(rows) * 100.0, 1),
        "interpretation": _interpret(abs_moves, gaps, runups),
    }


def _interpret(abs_moves: list[float], gaps: list[float], runups: list[float]) -> str:
    avg_abs = mean(abs_moves) if abs_moves else 0
    avg_gap = mean(gaps) if gaps else 0
    avg_runup = mean(runups) if runups else 0
    if avg_abs <= 6 and avg_gap <= 4:
        return "Historically muted earnings moves; supportive for ATM calendar structures if live IV/liquidity pass."
    if avg_abs >= 12 or avg_gap >= 8:
        return "Historically large earnings moves; require wider breakeven/strong IV edge or avoid single ATM calendar."
    if avg_runup > 5:
        return "Meaningful pre-earnings run-up tendency; avoid chasing late entries near highs."
    return "Mixed historical behavior; use as context, not a standalone entry signal."


def _finalize(result: dict[str, Any]) -> dict[str, Any]:
    items = result.get("items", []) or []
    result["has_data"] = any(item.get("has_data") for item in items)
    result["summary"] = {
        "candidate_count": len(items),
        "with_history_count": sum(1 for item in items if item.get("has_data")),
        "skipped_reason": None if items else "No fully-qualified candidates passed to backtest.",
    }
    return result


def _nearest_candle_on_or_before(candles: dict[date, dict[str, Any]], target: date, max_back: int) -> dict[str, Any] | None:
    for i in range(max_back + 1):
        candle = candles.get(target - timedelta(days=i))
        if candle:
            return candle
    return None


def _nearest_candle_on_or_after(candles: dict[date, dict[str, Any]], target: date, max_forward: int) -> dict[str, Any] | None:
    for i in range(max_forward + 1):
        candle = candles.get(target + timedelta(days=i))
        if candle:
            return candle
    return None


def _parse_date(value: Any) -> date | None:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _num(value: Any) -> float | None:
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
