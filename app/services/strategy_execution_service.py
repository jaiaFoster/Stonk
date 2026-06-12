"""Registry result collection isolated from legacy strategy math."""

from __future__ import annotations

from typing import Any

from app.strategies.registry import normalize_strategy_results


def collect_strategy_results(context: Any, raw_results: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Normalize every registered strategy independently.

    Existing services still evaluate their own math. This service is the
    migration boundary that keeps report assembly strategy-agnostic.
    """
    return normalize_strategy_results(context, raw_results)
