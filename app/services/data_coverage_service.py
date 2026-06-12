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
        "run_context_hits": sources.get("run_cache", 0),
        "sqlite_cache_hits": sources.get("sqlite_cache", 0),
        "provider_fetches": sources.get("provider", 0),
        "stale_fallbacks": states.get("STALE_CACHE_USED", 0),
        "provider_failures": states.get("MISSING_PROVIDER_FAILED", 0),
        "skipped_dev_cap": states.get("SKIPPED_DEV_CAP", 0),
        "skipped_provider_budget": states.get("SKIPPED_PROVIDER_BUDGET", 0),
        "duplicate_fetches_prevented": sources.get("run_cache", 0),
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
        "counters": counters,
        "audit_count": len(context.fetch_audit),
    }
