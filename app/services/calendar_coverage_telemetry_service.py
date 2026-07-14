"""Compact calendar coverage accounting for Patch 34A."""

from __future__ import annotations

from collections import Counter
from typing import Any

from app.models.strategy_definition import CandidateGenerationResult


def build_calendar_coverage_funnel(
    *,
    earnings_trade_discovery: dict[str, Any] | None,
    earnings_discovery_quality: dict[str, Any] | None,
    calendar_candidates: list[dict[str, Any]] | None,
    calendar_projection: dict[str, Any] | None,
) -> dict[str, Any]:
    discovery_items = _items(earnings_trade_discovery)
    quality_items = _items(earnings_discovery_quality)
    projected_rows = list((calendar_projection or {}).get("new_trade_rows") or [])
    candidates = [row for row in (calendar_candidates or []) if isinstance(row, dict)]
    failure_by_code: Counter[str] = Counter()
    valid_pair_rows = 0
    for row in projected_rows:
        if row.get("expiration_pair") or row.get("front_expiration"):
            valid_pair_rows += 1
        for rejected in row.get("rejected_expirations") or []:
            if isinstance(rejected, dict):
                code = rejected.get("primary_rejection_code") or rejected.get("reason") or rejected.get("code")
                if code:
                    failure_by_code[str(code)] += 1
        code = row.get("blocker_code") or row.get("entry_window_status") or row.get("exit_reason")
        if code:
            failure_by_code[str(code)] += 1
    result = CandidateGenerationResult(
        strategy_id="earnings_calendar",
        raw_events=len(discovery_items),
        merged_events=len(discovery_items),
        quality_eligible=len([row for row in quality_items if _is_quality_eligible(row)]),
        quality_rejected=len([row for row in quality_items if not _is_quality_eligible(row)]),
        optionable_candidates=len(candidates),
        budget_approved=len(candidates),
        budget_deferred=len([row for row in projected_rows if row.get("disposition_code") == "DEV_MODE_BUDGET_NOT_SELECTED"]),
        chain_sets_requested=len(candidates),
        chain_sets_acquired=len(candidates),
        tickers_with_expirations=len([row for row in projected_rows if row.get("available_expirations")]),
        tickers_with_valid_pairs=valid_pair_rows,
        valid_pairs=valid_pair_rows,
        rejected_pairs=sum(failure_by_code.values()),
        terminal_rows=len(projected_rows),
        failure_by_code=dict(sorted(failure_by_code.items())),
    )
    payload = result.to_dict()
    payload["policy_version"] = "34A.calendar_coverage.v1"
    return payload


def format_calendar_coverage_log(coverage: dict[str, Any]) -> str:
    return (
        "CALENDAR_COVERAGE_FUNNEL "
        f"raw_events={coverage.get('raw_events', 0)} "
        f"quality_eligible={coverage.get('quality_eligible', 0)} "
        f"quality_rejected={coverage.get('quality_rejected', 0)} "
        f"optionable={coverage.get('optionable_candidates', 0)} "
        f"budget_approved={coverage.get('budget_approved', 0)} "
        f"budget_deferred={coverage.get('budget_deferred', 0)} "
        f"tickers_with_expirations={coverage.get('tickers_with_expirations', 0)} "
        f"tickers_with_valid_pairs={coverage.get('tickers_with_valid_pairs', 0)} "
        f"valid_pairs={coverage.get('valid_pairs', 0)} "
        f"rejected_pairs={coverage.get('rejected_pairs', 0)} "
        f"failure_by_code={coverage.get('failure_by_code', {})}"
    )


def _items(value: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [row for row in ((value or {}).get("items") or []) if isinstance(row, dict)]


def _is_quality_eligible(row: dict[str, Any]) -> bool:
    if row.get("eligible") is False or row.get("passed") is False:
        return False
    if row.get("exit_reason") or row.get("primary_rejection_reason"):
        return False
    return True
