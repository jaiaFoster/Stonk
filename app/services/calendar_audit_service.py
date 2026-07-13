"""
ASA Patch 32B — Calendar Discovery Audit Service

Emits structured CALENDAR_DISCOVERY_AUDIT and per-ticker
CALENDAR_TICKER_AUDIT log lines showing the stage-by-stage funnel
from raw earnings events to ranked calendar candidates.

Funnel stages
-------------
Stage 1: raw_events    — raw items from earnings discovery provider
Stage 2: constituent   — passed constituent / universe prescreen
Stage 3: quality       — passed earnings discovery quality filter (optionability)
Stage 4: scanner       — passed full calendar spread scan
Stage 5: strategy      — passed earnings calendar strategy evaluation
Stage 6: ranked        — present in final calendar ranking output
"""

from __future__ import annotations

from typing import Any, Callable

_SCHEMA_VERSION = "32B.v1"


class PipelineStage:
    RAW_EVENT = "RAW_EVENT"
    CONSTITUENT_FILTER = "CONSTITUENT_FILTER"
    OPTIONABILITY = "OPTIONABILITY"
    EARNINGS_CONFIDENCE = "EARNINGS_CONFIDENCE"
    CHAIN_REQUEST = "CHAIN_REQUEST"
    CHAIN_RESPONSE = "CHAIN_RESPONSE"
    EXPIRATION_ENUMERATION = "EXPIRATION_ENUMERATION"
    PAIR_BUILD = "PAIR_BUILD"
    STRIKE_MATCH = "STRIKE_MATCH"
    LIQUIDITY = "LIQUIDITY"
    STRATEGY_GATE = "STRATEGY_GATE"
    RANKING = "RANKING"
    PERSISTENCE = "PERSISTENCE"
    COMPLETE = "COMPLETE"


def build_calendar_audit(
    earnings_trade_discovery: dict[str, Any] | None,
    earnings_discovery_quality: dict[str, Any] | None,
    calendar_candidates: list[dict[str, Any]] | None,
    calendar_ranking: dict[str, Any] | None,
    earnings_calendar_strategy: dict[str, Any] | None = None,
    run_mode: str = "prod",
) -> dict[str, Any]:
    """Build the calendar discovery audit funnel from pipeline stage outputs.

    Returns a dict with per-stage counts, per-ticker details, and summary.
    """
    disc = earnings_trade_discovery or {}
    qual = earnings_discovery_quality or {}
    candidates = list(calendar_candidates or [])
    ranking = calendar_ranking or {}
    strategy_result = earnings_calendar_strategy or {}

    # Stage 1: raw events from discovery
    raw_items: list[dict[str, Any]] = list(disc.get("items") or [])
    raw_count = len(raw_items)

    # Stage 2: constituent-filtered — tracked in quality filter summary
    qual_summary = qual.get("summary") or {}
    constituent_count = int(qual_summary.get("raw_event_count") or raw_count)

    # Stage 3: quality filter passed (optionability precheck)
    passed_items: list[dict[str, Any]] = list(qual.get("passed_items") or [])
    quality_count = len(passed_items)

    # Stage 4: scanner candidates
    scanner_count = len(candidates)

    # Stage 5: strategy evaluated
    strategy_items: list[dict[str, Any]] = list(strategy_result.get("items") or [])
    strategy_count = len(strategy_items)

    # Stage 6: ranked
    ranked_items: list[dict[str, Any]] = list(ranking.get("items") or [])
    ranked_count = len(ranked_items)

    # Per-ticker audit trail
    ticker_audit: dict[str, dict[str, Any]] = {}

    # Seed with raw discovery
    for item in raw_items:
        ticker = str(item.get("ticker") or item.get("symbol") or "").upper().strip()
        if not ticker:
            continue
        ticker_audit.setdefault(ticker, {
            "ticker": ticker,
            "stages": {},
            "exit_stage": None,
            "exit_reason": None,
        })
        ticker_audit[ticker]["stages"]["raw_events"] = True

    # Quality filter: passed vs rejected
    for item in passed_items:
        ticker = str(item.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        ticker_audit.setdefault(ticker, {"ticker": ticker, "stages": {}, "exit_stage": None, "exit_reason": None})
        ticker_audit[ticker]["stages"]["quality_passed"] = True
        ticker_audit[ticker]["precheck_score"] = item.get("score") or item.get("precheck_score")
        ticker_audit[ticker]["entry_window"] = item.get("entry_window_status") or item.get("entry_window")

    rejected_items: list[dict[str, Any]] = list(qual.get("rejected_items") or [])
    for item in rejected_items:
        ticker = str(item.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        ticker_audit.setdefault(ticker, {"ticker": ticker, "stages": {}, "exit_stage": None, "exit_reason": None})
        ticker_audit[ticker]["stages"]["quality_rejected"] = True
        ticker_audit[ticker]["exit_stage"] = "quality"
        ticker_audit[ticker]["exit_reason"] = (
            item.get("rejection_reason") or item.get("precheck_reason") or item.get("primary_rejection_reason") or "quality_filter"
        )

    # Scanner candidates
    scanner_tickers: set[str] = set()
    for cand in candidates:
        ticker = str(cand.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        scanner_tickers.add(ticker)
        ticker_audit.setdefault(ticker, {"ticker": ticker, "stages": {}, "exit_stage": None, "exit_reason": None})
        ticker_audit[ticker]["stages"]["scanner"] = True
        ticker_audit[ticker]["scanner_score"] = cand.get("score") or cand.get("scanner_score")
        ticker_audit[ticker]["scanner_verdict"] = cand.get("scanner_verdict") or cand.get("verdict")

    # Strategy
    for item in strategy_items:
        ticker = str(item.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        ticker_audit.setdefault(ticker, {"ticker": ticker, "stages": {}, "exit_stage": None, "exit_reason": None})
        ticker_audit[ticker]["stages"]["strategy"] = True
        ticker_audit[ticker]["trade_type"] = item.get("trade_type")

    # Ranked
    ranked_tickers: set[str] = set()
    for item in ranked_items:
        ticker = str(item.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        ranked_tickers.add(ticker)
        ticker_audit.setdefault(ticker, {"ticker": ticker, "stages": {}, "exit_stage": None, "exit_reason": None})
        ticker_audit[ticker]["stages"]["ranked"] = True
        ticker_audit[ticker]["final_verdict"] = item.get("final_verdict") or item.get("verdict")
        ticker_audit[ticker]["ranking_score"] = item.get("ranking_score") or item.get("score")

    # Set exit_stage for tickers that made it to scanner but not ranked
    for ticker, entry in ticker_audit.items():
        if entry.get("exit_stage"):
            continue  # already set
        stages = entry.get("stages") or {}
        if stages.get("ranked"):
            entry["exit_stage"] = "ranked"
        elif stages.get("strategy"):
            entry["exit_stage"] = "strategy"
            entry["exit_reason"] = "not_in_top_ranked"
        elif stages.get("scanner"):
            entry["exit_stage"] = "scanner"
            entry["exit_reason"] = "not_passed_strategy"
        elif stages.get("quality_passed"):
            entry["exit_stage"] = "quality"
            entry["exit_reason"] = "not_selected_for_scan"

    # Build set of all tickers seen in quality-filter output (passed OR rejected)
    qual_seen_tickers: set[str] = set()
    for item in passed_items:
        t = str(item.get("ticker") or "").upper().strip()
        if t:
            qual_seen_tickers.add(t)
    for item in rejected_items:
        t = str(item.get("ticker") or "").upper().strip()
        if t:
            qual_seen_tickers.add(t)

    # Assign an explicit exit_stage to any ticker that is still unresolved (was in
    # raw_events but never appeared in quality-filter, scanner, strategy, or ranking output).
    for ticker, entry in ticker_audit.items():
        if entry.get("exit_stage"):
            continue  # already resolved
        stages = entry.get("stages") or {}
        if not stages.get("raw_events"):
            continue  # not a raw-event ticker; skip
        if ticker in qual_seen_tickers:
            # Ticker reached the quality filter but exit_stage was not set by the loop above
            entry["exit_stage"] = PipelineStage.EARNINGS_CONFIDENCE
            if not entry.get("exit_reason"):
                entry["exit_reason"] = "not_selected_for_scan"
        elif run_mode == "dev":
            # In dev mode, budget caps can prevent tickers from reaching the quality filter
            entry["exit_stage"] = PipelineStage.OPTIONABILITY
            entry["exit_reason"] = "DEV_MODE_BUDGET_NOT_SELECTED"
        else:
            entry["exit_stage"] = PipelineStage.CONSTITUENT_FILTER
            entry["exit_reason"] = "constituent_filter_excluded"

    # Count remaining unknown exit stages (defensive: should be zero after the above)
    unknown_exit_count = sum(
        1 for entry in ticker_audit.values() if not entry.get("exit_stage")
    )

    funnel = {
        "raw_events": raw_count,
        "constituent_checked": constituent_count,
        "quality_passed": quality_count,
        "scanner_candidates": scanner_count,
        "strategy_evaluated": strategy_count,
        "ranked": ranked_count,
        "unknown_exit_stages": unknown_exit_count,
    }

    return {
        "funnel": funnel,
        "ticker_audit": ticker_audit,
        "run_mode": run_mode,
        "schema_version": _SCHEMA_VERSION,
    }


def log_calendar_discovery_audit(
    audit: dict[str, Any],
    log_print: Callable[[str], None] | None = None,
) -> str:
    """Emit the CALENDAR_DISCOVERY_AUDIT log line.

    Format:
      CALENDAR_DISCOVERY_AUDIT raw_events=N constituent_checked=N quality_passed=N
        scanner_candidates=N strategy_evaluated=N ranked=N run_mode=X
    """
    log = log_print or print
    try:
        funnel = audit.get("funnel") or {}
        line = (
            f"CALENDAR_DISCOVERY_AUDIT "
            f"raw_events={funnel.get('raw_events', 0)} "
            f"constituent_checked={funnel.get('constituent_checked', 0)} "
            f"quality_passed={funnel.get('quality_passed', 0)} "
            f"scanner_candidates={funnel.get('scanner_candidates', 0)} "
            f"strategy_evaluated={funnel.get('strategy_evaluated', 0)} "
            f"ranked={funnel.get('ranked', 0)} "
            f"unknown_exit_stages={funnel.get('unknown_exit_stages', 0)} "
            f"run_mode={audit.get('run_mode', 'unknown')}"
        )
        try:
            log(line, flush=True)
        except TypeError:
            log(line)
        return line
    except Exception:
        fallback = "CALENDAR_DISCOVERY_AUDIT error=log_failed"
        try:
            log(fallback, flush=True)
        except TypeError:
            log(fallback)
        return fallback


def log_calendar_ticker_audit(
    audit: dict[str, Any],
    log_print: Callable[[str], None] | None = None,
) -> list[str]:
    """Emit per-ticker CALENDAR_TICKER_AUDIT log lines.

    Format:
      CALENDAR_TICKER_AUDIT ticker=X exit_stage=Y exit_reason=Z [score=N] [verdict=V]
    """
    log = log_print or print
    lines: list[str] = []
    try:
        ticker_audit = audit.get("ticker_audit") or {}
        for ticker, entry in sorted(ticker_audit.items()):
            parts = [
                f"CALENDAR_TICKER_AUDIT",
                f"ticker={ticker}",
                f"exit_stage={entry.get('exit_stage') or PipelineStage.CONSTITUENT_FILTER}",
            ]
            if entry.get("exit_reason"):
                parts.append(f"exit_reason={entry['exit_reason']}")
            if entry.get("precheck_score") is not None:
                parts.append(f"precheck_score={entry['precheck_score']}")
            if entry.get("scanner_verdict"):
                parts.append(f"scanner_verdict={entry['scanner_verdict']}")
            if entry.get("final_verdict"):
                parts.append(f"final_verdict={entry['final_verdict']}")
            if entry.get("entry_window"):
                parts.append(f"entry_window={entry['entry_window']}")
            line = " ".join(parts)
            try:
                log(line, flush=True)
            except TypeError:
                log(line)
            lines.append(line)
    except Exception:
        pass
    return lines


def run_calendar_audit(
    earnings_trade_discovery: dict[str, Any] | None,
    earnings_discovery_quality: dict[str, Any] | None,
    calendar_candidates: list[dict[str, Any]] | None,
    calendar_ranking: dict[str, Any] | None,
    earnings_calendar_strategy: dict[str, Any] | None = None,
    run_mode: str = "prod",
    log_print: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Build calendar audit and emit all log lines. Returns the audit dict."""
    try:
        audit = build_calendar_audit(
            earnings_trade_discovery=earnings_trade_discovery,
            earnings_discovery_quality=earnings_discovery_quality,
            calendar_candidates=calendar_candidates,
            calendar_ranking=calendar_ranking,
            earnings_calendar_strategy=earnings_calendar_strategy,
            run_mode=run_mode,
        )
        log_calendar_discovery_audit(audit, log_print=log_print)
        log_calendar_ticker_audit(audit, log_print=log_print)
        return audit
    except Exception:
        return {"error": "audit_failed", "schema_version": _SCHEMA_VERSION}
