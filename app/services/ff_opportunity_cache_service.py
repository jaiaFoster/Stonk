"""Forward Factor Opportunity Cache — 32C.

Persists PASS / WATCH / NEAR MISS rows with full fields for analysis.
Read-only externally; written only by build_forward_factor_strategy.
No broker writes, no execution triggers.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

_CACHE_FILE = os.environ.get("FF_OPPORTUNITY_CACHE_PATH", "/tmp/ff_opportunity_cache.json")
_MAX_ENTRIES = int(os.environ.get("FF_OPPORTUNITY_CACHE_MAX_ENTRIES", "500"))
_TRACKED_STATUSES = {"dry_run_excluded", "conditional", "near_miss"}
_TRACKED_VERDICT_PREFIXES = ("PASS", "WATCH", "NEAR MISS")

_PERSIST_FIELDS = (
    "ticker", "verdict", "forward_factor", "front_raw_iv", "back_raw_iv",
    "front_ex_earnings_iv", "back_ex_earnings_iv", "conservative_debit",
    "debit_at_risk", "liquidity_pass", "liquidity_status", "structure_status",
    "structure_quality_score", "signal_score", "eligibility_status",
    "ff_candidate_stage", "near_miss_ff", "watch_zone_ff", "miss_distance",
    "miss_reason", "front_expiration", "back_expiration", "front_dte", "back_dte",
    "primary_blocker", "strategy_actionable", "execution_enabled",
)


def _load() -> list[dict[str, Any]]:
    try:
        with open(_CACHE_FILE, "r") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _save(entries: list[dict[str, Any]]) -> None:
    try:
        with open(_CACHE_FILE, "w") as f:
            json.dump(entries[-_MAX_ENTRIES:], f, default=str)
    except OSError:
        pass


def cache_ff_run_results(rows: list[dict[str, Any]], run_id: str | None = None, run_date: str | None = None) -> int:
    """Persist PASS/WATCH/NEAR MISS rows from a completed FF run. Returns count written."""
    now = datetime.now(timezone.utc).isoformat()
    trackable = [
        row for row in rows
        if any(str(row.get("verdict") or "").upper().startswith(pfx) for pfx in _TRACKED_VERDICT_PREFIXES)
    ]
    if not trackable:
        return 0
    existing = _load()
    new_entries = [
        {
            **{field: row.get(field) for field in _PERSIST_FIELDS},
            "run_id": run_id,
            "run_date": run_date or now[:10],
            "cached_at": now,
        }
        for row in trackable
    ]
    _save(existing + new_entries)
    return len(new_entries)


def read_opportunity_cache(
    verdict_prefix: str | None = None,
    ticker: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return cached FF opportunity rows, newest first. Filters by verdict prefix or ticker."""
    entries = _load()
    if verdict_prefix:
        pfx = verdict_prefix.upper()
        entries = [e for e in entries if str(e.get("verdict") or "").upper().startswith(pfx)]
    if ticker:
        t = ticker.upper()
        entries = [e for e in entries if str(e.get("ticker") or "").upper() == t]
    return list(reversed(entries))[:limit]


def cache_summary() -> dict[str, Any]:
    """Return aggregate stats from the opportunity cache."""
    entries = _load()
    if not entries:
        return {"total": 0, "by_verdict_class": {}, "unique_tickers": 0}
    by_class: dict[str, int] = {}
    for e in entries:
        v = str(e.get("verdict") or "")
        cls = "pass" if v.upper().startswith("PASS") else "watch" if v.upper().startswith("WATCH") else "near_miss" if v.upper().startswith("NEAR MISS") else "other"
        by_class[cls] = by_class.get(cls, 0) + 1
    return {
        "total": len(entries),
        "by_verdict_class": by_class,
        "unique_tickers": len({e.get("ticker") for e in entries if e.get("ticker")}),
        "cache_file": _CACHE_FILE,
    }
