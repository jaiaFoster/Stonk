"""Compact coverage summaries for reports and strategy decisions."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


def build_data_coverage(context: Any) -> dict[str, Any]:
    sources = Counter(row.get("source", "unknown") for row in context.fetch_audit)
    states = Counter(row.get("state", "COMPLETE") for row in context.fetch_audit)
    per_strategy: dict[str, Counter] = defaultdict(Counter)
    for row in context.fetch_audit:
        strategy = row.get("strategy_id")
        if strategy:
            per_strategy[strategy][row.get("state", "COMPLETE")] += 1
    counters = {
        "requested": len(context.fetch_audit),
        "run_context_hits": sources.get("run_cache", 0),
        "sqlite_cache_hits": sources.get("sqlite_cache", 0),
        "provider_fetches": sources.get("provider", 0),
        "stale_cache_fallbacks": states.get("STALE_CACHE_USED", 0),
        "provider_failures": states.get("MISSING_PROVIDER_FAILED", 0),
        "provider_failures_suppressed": sources.get("provider_failure_suppressed", 0),
        "skipped_dev_cap": states.get("SKIPPED_DEV_CAP", 0),
        "skipped_provider_budget": states.get("SKIPPED_PROVIDER_BUDGET", 0),
        "duplicate_fetches_prevented": sources.get("run_cache", 0),
        "optional_deferred": states.get("MISSING_NOT_REQUESTED", 0),
    }
    strategy_summary = {}
    for strategy, values in per_strategy.items():
        strategy_summary[strategy] = {
            "complete": values.get("COMPLETE", 0),
            "partial": values.get("PARTIAL", 0) + values.get("STALE_CACHE_USED", 0),
            "skipped": values.get("SKIPPED_DEV_CAP", 0) + values.get("SKIPPED_PROVIDER_BUDGET", 0),
            "failed": values.get("MISSING_PROVIDER_FAILED", 0),
        }
    return {
        "run_id": context.run_id,
        "mode": context.mode,
        "requested_tickers": len({row.get("ticker") for row in context.fetch_audit if row.get("ticker")}),
        "records": {
            "quotes": len(context.quotes), "candles": len(context.candles),
            "options_chains": len(context.options_chains), "earnings_events": len(context.earnings_events),
            "derived_metrics": len(context.derived_metrics),
        },
        "sources": dict(sources),
        "states": dict(states),
        "per_strategy": {key: dict(value) for key, value in per_strategy.items()},
        "per_strategy_summary": strategy_summary,
        "counters": counters,
        "audit_count": len(context.fetch_audit),
    }
