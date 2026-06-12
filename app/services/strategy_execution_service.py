"""Registry result collection isolated from legacy strategy math."""

from __future__ import annotations

from typing import Any

from app.strategies.registry import enabled_strategies, normalize_strategy_results


def execute_strategy_registry(context: Any, evaluators: dict[str, Any], log_print=None) -> dict[str, dict[str, Any]]:
    """Execute registered compatibility adapters with per-plugin failure isolation."""
    log = log_print or (lambda message: None)
    raw_results: dict[str, dict[str, Any]] = {}
    plugins = enabled_strategies()
    log(f"StrategyRegistry: {len(plugins)} enabled strategy plugin(s)")
    for plugin in plugins:
        log(f"StrategyRegistry: executing {plugin.strategy_id}")
        try:
            evaluator = evaluators.get(plugin.strategy_id)
            if evaluator is None:
                raise RuntimeError("strategy evaluator not registered")
            raw_results[plugin.strategy_id] = evaluator() or {}
        except Exception as exc:
            raw_results[plugin.strategy_id] = {"items": [], "errors": [str(exc)], "execution_failed": True}
        normalized = plugin.normalize_result(raw_results[plugin.strategy_id], context)
        log(
            f"StrategyRegistry: {plugin.strategy_id} complete "
            f"pass={normalized.pass_count} watch={normalized.watch_count} fail={normalized.fail_count}"
        )
    return normalize_strategy_results(context, raw_results)


def collect_strategy_results(context: Any, raw_results: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Normalize every registered strategy independently.

    Existing services still evaluate their own math. This service is the
    migration boundary that keeps report assembly strategy-agnostic.
    """
    return normalize_strategy_results(context, raw_results)
