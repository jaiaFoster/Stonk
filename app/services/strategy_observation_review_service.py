"""30C — Strategy observation review layer: summary, blockers, tickers, movement, queue.

Reads from the 30B strategy_observations journal. Does not call market providers,
does not mutate strategy logic, and does not alter Daily Opportunity behavior.

All public functions return dicts with provider_calls_triggered=False.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from app.db.strategy_observations import (
    OBSERVATION_SCHEMA_VERSION,
    global_summary,
    query_for_review,
    query_primary_reason_stats,
    query_ticker_stats,
    query_two_latest_runs,
    run_summary,
)
from app.services.strategy_observation_review_classifier import (
    classify_blocker_category,
    classify_movement,
    classify_review_priority,
    classify_review_type,
)

REVIEW_SCHEMA_VERSION = "30C.v1"

_STRATEGY_NAMES = {
    "earnings_calendar": "Earnings Calendar Spread",
    "skew_momentum_vertical": "Skew Momentum Vertical",
    "forward_factor_calendar": "Forward Factor Calendar",
    "stock_momentum": "Stock Momentum",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_json(text: str | None) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _strategy_name(strategy_id: str) -> str:
    return _STRATEGY_NAMES.get(strategy_id, strategy_id)


def _base_response(**kwargs: Any) -> dict[str, Any]:
    return {
        "provider_calls_triggered": False,
        "read_only": True,
        "review_schema_version": REVIEW_SCHEMA_VERSION,
        "observation_schema_version": OBSERVATION_SCHEMA_VERSION,
        "created_at": _now(),
        **kwargs,
    }


# ─── Lane 1: Review Summary ───────────────────────────────────────────────────


def build_strategy_review_summary(
    days: int = 7,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Rolling review summary for the last N days across all strategies."""
    base = global_summary(days=days, db_path=db_path)
    by_strategy_raw = base.get("by_strategy", {})

    # Per-strategy top primary reasons (SQL aggregation, no JSON parsing).
    reason_rows = query_primary_reason_stats(days=days, db_path=db_path, limit=100)
    reasons_by_strat: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in reason_rows:
        sid = row["strategy_id"]
        if len(reasons_by_strat[sid]) < 5:
            reasons_by_strat[sid].append({
                "reason": row["primary_reason"],
                "count": row["cnt"],
                "pass_count": row["pass_cnt"],
                "issue_count": row["issue_cnt"],
            })

    # Compact blockers inline (capped to 10 for summary weight).
    compact_blockers = _aggregate_blockers(
        query_for_review(days=days, blocking_only=True, limit=150, db_path=db_path),
        limit=10,
    )

    # Build enriched per-strategy map.
    by_strategy = {
        sid: {
            **counts,
            "strategy_name": _strategy_name(sid),
            "top_primary_reasons": reasons_by_strat.get(sid, []),
        }
        for sid, counts in by_strategy_raw.items()
    }

    return _base_response(
        window_days=days,
        total_observations=base.get("total_observations", 0),
        run_count=base.get("runs_recorded", 0),
        strategy_count=len(by_strategy),
        ticker_count=base.get("tickers_observed", 0),
        by_strategy=by_strategy,
        by_status_bucket=base.get("by_status_bucket", {}),
        daily_opportunity_eligible_count=base.get("daily_opportunity_eligible_count", 0),
        dry_run_count=base.get("dry_run_count", 0),
        can_trade_live_count=base.get("can_trade_live_count", 0),
        top_blockers_compact=compact_blockers,
    )


def build_strategy_review_summary_for_run(
    run_id: str,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Per-run review summary."""
    base = run_summary(run_id=run_id, db_path=db_path)
    by_strat = base.get("by_strategy", {})
    by_strat_named = {
        sid: {**v, "strategy_name": _strategy_name(sid)}
        for sid, v in by_strat.items()
    }
    return _base_response(
        run_id=run_id,
        total_observations=base.get("total_observations", 0),
        by_strategy=by_strat_named,
        by_status_bucket=base.get("by_status_bucket", {}),
        daily_opportunity_eligible_count=base.get("daily_opportunity_eligible_count", 0),
        dry_run_count=base.get("dry_run_count", 0),
        can_trade_live_count=base.get("can_trade_live_count", 0),
    )


def build_strategy_review_summary_for_strategy(
    strategy_id: str,
    days: int = 7,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Strategy-specific review summary."""
    reasons = query_primary_reason_stats(days=days, strategy_id=strategy_id, db_path=db_path, limit=10)
    blocking_obs = query_for_review(
        days=days, strategy_id=strategy_id, blocking_only=True, limit=150, db_path=db_path
    )
    blockers = _aggregate_blockers(blocking_obs, limit=10)
    ticker_stats = query_ticker_stats(days=days, db_path=db_path)
    unique_tickers = sum(
        1 for r in ticker_stats
        if strategy_id in (r.get("strategy_ids_csv") or "").split(",")
    )
    return _base_response(
        strategy_id=strategy_id,
        strategy_name=_strategy_name(strategy_id),
        window_days=days,
        unique_tickers=unique_tickers,
        top_primary_reasons=[
            {
                "reason": r["primary_reason"],
                "count": r["cnt"],
                "pass_count": r["pass_cnt"],
                "issue_count": r["issue_cnt"],
            }
            for r in reasons[:5]
        ],
        top_blocking_gates=blockers[:5],
    )


# ─── Lane 2: Repeat Blocker Analysis ─────────────────────────────────────────


def build_repeat_blockers(
    days: int = 7,
    strategy_id: str | None = None,
    limit: int = 50,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Identify repeated blocking gates across recent observations."""
    obs = query_for_review(
        days=days,
        strategy_id=strategy_id,
        blocking_only=True,
        limit=500,
        db_path=db_path,
    )
    blockers = _aggregate_blockers(obs, strategy_id=strategy_id, limit=min(int(limit or 50), 250))
    return _base_response(
        window_days=days,
        strategy_id=strategy_id,
        observations_scanned=len(obs),
        blocker_count=len(blockers),
        blockers=blockers,
    )


def _aggregate_blockers(
    obs_rows: list[dict[str, Any]],
    strategy_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Parse gates_json from observation rows and aggregate blocking gate patterns."""
    # Key: (strategy_id, gate_id, reason) → accumulator
    agg: dict[tuple[str, str, str], dict[str, Any]] = {}

    for row in obs_rows:
        sid = str(row.get("strategy_id") or "")
        if strategy_id and sid != strategy_id:
            continue
        ticker = str(row.get("ticker") or "")
        run_date = str(row.get("run_date") or "")
        gates = _safe_json(row.get("gates_json")) or []
        if not isinstance(gates, list):
            continue
        for gate in gates:
            if not isinstance(gate, dict):
                continue
            if not gate.get("blocking"):
                continue
            if gate.get("status") not in ("fail", "error", "unknown"):
                continue
            gate_id = str(gate.get("id") or gate.get("name") or "")
            gate_label = str(gate.get("label") or gate.get("name") or gate_id)
            reason = str(gate.get("reason") or gate.get("label") or "")[:150]
            key = (sid, gate_id, reason)
            if key not in agg:
                agg[key] = {
                    "strategy_id": sid,
                    "strategy_name": _strategy_name(sid),
                    "gate_id": gate_id,
                    "gate_label": gate_label,
                    "blocking_status": gate.get("status", "fail"),
                    "reason": reason,
                    "count": 0,
                    "unique_tickers": set(),
                    "latest_seen": "",
                    "suggested_category": classify_blocker_category(reason, gate_id),
                }
            acc = agg[key]
            acc["count"] += 1
            acc["unique_tickers"].add(ticker)
            if run_date > acc["latest_seen"]:
                acc["latest_seen"] = run_date

    results = []
    for acc in agg.values():
        tickers = sorted(acc["unique_tickers"])
        results.append({
            **{k: v for k, v in acc.items() if k != "unique_tickers"},
            "unique_tickers": len(tickers),
            "sample_tickers": tickers[:5],
        })

    results.sort(key=lambda x: (-x["count"], x["strategy_id"]))
    return results[:limit]


# ─── Lane 3: Ticker Recurrence Analysis ──────────────────────────────────────


def build_ticker_recurrence(
    days: int = 7,
    ticker: str | None = None,
    limit: int = 50,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Track tickers that recur across strategies or multiple runs."""
    rows = query_ticker_stats(days=days, limit=min(int(limit or 50), 250), db_path=db_path)

    if ticker:
        rows = [r for r in rows if r.get("ticker", "").upper() == ticker.upper().strip()]

    result_rows = []
    for row in rows:
        sid_list = [s for s in (row.get("strategy_ids_csv") or "").split(",") if s]
        bucket_list = [b for b in (row.get("buckets_csv") or "").split(",") if b]
        obs_count = row.get("obs_count") or 0
        strategy_count = row.get("strategy_count") or 0
        pass_count = row.get("pass_count") or 0
        watch_count = row.get("watch_count") or 0
        sample_reason = str(row.get("sample_primary_reason") or "")
        dominant_bucket = _dominant_bucket(pass_count, watch_count,
                                           row.get("fail_count") or 0,
                                           row.get("skipped_count") or 0,
                                           row.get("dry_run_count") or 0)
        review_type = classify_review_type(
            dominant_bucket, "", sample_reason, "",
            sid_list[0] if len(sid_list) == 1 else "",
        )
        review_priority = classify_review_priority(
            dominant_bucket, obs_count, strategy_count, review_type
        )
        result_rows.append({
            "ticker": row.get("ticker", ""),
            "observation_count": obs_count,
            "run_count": row.get("run_count") or 0,
            "strategy_count": strategy_count,
            "strategy_ids_seen": sid_list,
            "status_buckets_seen": bucket_list,
            "pass_count": pass_count,
            "watch_count": watch_count,
            "fail_count": row.get("fail_count") or 0,
            "skipped_count": row.get("skipped_count") or 0,
            "dry_run_count": row.get("dry_run_count") or 0,
            "first_seen": row.get("first_seen", ""),
            "latest_seen": row.get("latest_seen", ""),
            "review_priority": review_priority,
        })

    return _base_response(
        window_days=days,
        ticker_filter=ticker,
        ticker_count=len(result_rows),
        tickers=result_rows,
    )


def _dominant_bucket(pass_c: int, watch_c: int, fail_c: int, skip_c: int, dry_c: int) -> str:
    counts = [
        (pass_c, "pass"), (watch_c, "watch"), (fail_c, "fail"),
        (skip_c, "skipped"), (dry_c, "dry_run"),
    ]
    best = max(counts, key=lambda x: x[0])
    return best[1] if best[0] > 0 else "unknown"


# ─── Lane 4: Run-over-Run Movement Tracking ───────────────────────────────────


def build_run_movement(
    run_id: str | None = None,
    prev_run_id: str | None = None,
    limit: int = 50,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Detect whether candidates improved, degraded, appeared, or disappeared."""
    # Resolve run IDs if not supplied.
    if not run_id or not prev_run_id:
        latest_runs = query_two_latest_runs(db_path=db_path)
        if len(latest_runs) >= 2:
            run_id = run_id or latest_runs[0]
            prev_run_id = prev_run_id or latest_runs[1]
        elif len(latest_runs) == 1:
            run_id = run_id or latest_runs[0]
            prev_run_id = None
        else:
            return _base_response(
                run_id=run_id,
                prev_run_id=prev_run_id,
                status="no_runs",
                movement_count=0,
                movement=[],
            )

    curr_obs = query_for_review(run_id=run_id, limit=500, db_path=db_path) if run_id else []
    prev_obs = query_for_review(run_id=prev_run_id, limit=500, db_path=db_path) if prev_run_id else []

    # Build lookup by (strategy_id, observation_key), fallback to (strategy_id, ticker).
    def _key(row: dict) -> tuple[str, str]:
        return (str(row.get("strategy_id") or ""), str(row.get("observation_key") or row.get("ticker") or ""))

    prev_map = {_key(r): r for r in prev_obs}
    curr_map = {_key(r): r for r in curr_obs}

    movements: list[dict[str, Any]] = []

    # Current → movement from prev.
    for k, curr in curr_map.items():
        prev = prev_map.get(k)
        prev_bucket = prev.get("status_bucket") if prev else None
        curr_bucket = curr.get("status_bucket")
        movement, movement_reason = classify_movement(
            prev_bucket, curr_bucket,
            prev_blocking=int(prev.get("blocking_gate_count") or 0) if prev else 0,
            curr_blocking=int(curr.get("blocking_gate_count") or 0),
            prev_quality=prev.get("data_quality_status") if prev else None,
            curr_quality=curr.get("data_quality_status"),
        )
        score_delta: float | None = None
        if curr.get("score") is not None and prev and prev.get("score") is not None:
            try:
                score_delta = round(float(curr["score"]) - float(prev["score"]), 4)
            except (TypeError, ValueError):
                pass
        movements.append({
            "strategy_id": k[0],
            "strategy_name": _strategy_name(k[0]),
            "ticker": curr.get("ticker", ""),
            "observation_key": curr.get("observation_key", ""),
            "previous_run_id": prev_run_id,
            "current_run_id": run_id,
            "previous_status_bucket": prev_bucket,
            "current_status_bucket": curr_bucket,
            "movement": movement,
            "movement_reason": movement_reason,
            "gate_delta_summary": _gate_delta(prev, curr),
            "score_delta": score_delta,
        })

    # Prev items absent from current → disappeared.
    for k, prev in prev_map.items():
        if k not in curr_map:
            movement, movement_reason = classify_movement(
                prev.get("status_bucket"), None,
                prev_blocking=int(prev.get("blocking_gate_count") or 0),
            )
            movements.append({
                "strategy_id": k[0],
                "strategy_name": _strategy_name(k[0]),
                "ticker": prev.get("ticker", ""),
                "observation_key": prev.get("observation_key", ""),
                "previous_run_id": prev_run_id,
                "current_run_id": run_id,
                "previous_status_bucket": prev.get("status_bucket"),
                "current_status_bucket": None,
                "movement": movement,
                "movement_reason": movement_reason,
                "gate_delta_summary": None,
                "score_delta": None,
            })

    # Sort: improved/degraded first, then new, then disappeared, then unchanged.
    _ORDER = {"improved": 0, "degraded": 1, "new": 2, "reappeared": 2, "disappeared": 3, "unchanged": 4, "unknown": 5}
    movements.sort(key=lambda x: (_ORDER.get(x.get("movement", "unknown"), 5), x.get("strategy_id", ""), x.get("ticker", "")))

    return _base_response(
        run_id=run_id,
        prev_run_id=prev_run_id,
        movement_count=len(movements),
        improved_count=sum(1 for m in movements if m["movement"] == "improved"),
        degraded_count=sum(1 for m in movements if m["movement"] == "degraded"),
        new_count=sum(1 for m in movements if m["movement"] == "new"),
        disappeared_count=sum(1 for m in movements if m["movement"] == "disappeared"),
        unchanged_count=sum(1 for m in movements if m["movement"] == "unchanged"),
        movement=movements[:min(int(limit or 50), 250)],
    )


def _gate_delta(prev: dict | None, curr: dict) -> dict[str, Any] | None:
    if prev is None:
        return None
    return {
        "blocking_gate_delta": int(curr.get("blocking_gate_count") or 0) - int(prev.get("blocking_gate_count") or 0),
        "fail_gate_delta": int(curr.get("gate_fail_count") or 0) - int(prev.get("gate_fail_count") or 0),
        "pass_gate_delta": int(curr.get("gate_pass_count") or 0) - int(prev.get("gate_pass_count") or 0),
    }


# ─── Lane 5: Strategy Review Queue ───────────────────────────────────────────


def build_review_queue(
    days: int = 7,
    strategy_id: str | None = None,
    limit: int = 50,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Compute a prioritized review queue from journal patterns. No new writes."""
    ticker_rows = query_ticker_stats(days=days, limit=250, db_path=db_path)
    recent_obs = query_for_review(
        days=days, strategy_id=strategy_id, blocking_only=False, limit=200, db_path=db_path
    )

    queue: list[dict[str, Any]] = []

    # Build per-(strategy_id, ticker) latest observation lookup.
    latest_obs_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for obs in recent_obs:
        k = (str(obs.get("strategy_id") or ""), str(obs.get("ticker") or ""))
        existing = latest_obs_by_key.get(k)
        if existing is None or (obs.get("created_at") or "") > (existing.get("created_at") or ""):
            latest_obs_by_key[k] = obs

    seen_keys: set[str] = set()

    for row in ticker_rows:
        ticker = row.get("ticker", "")
        sid_list = [s for s in (row.get("strategy_ids_csv") or "").split(",") if s]

        if strategy_id and strategy_id not in sid_list:
            continue

        obs_count = row.get("obs_count") or 0
        strategy_count = row.get("strategy_count") or 0
        pass_count = row.get("pass_count") or 0
        watch_count = row.get("watch_count") or 0
        fail_count = row.get("fail_count") or 0
        skip_count = row.get("skipped_count") or 0
        dry_count = row.get("dry_run_count") or 0
        dominant_bucket = _dominant_bucket(pass_count, watch_count, fail_count, skip_count, dry_count)

        for sid in sid_list:
            if strategy_id and sid != strategy_id:
                continue
            latest = latest_obs_by_key.get((sid, ticker))
            primary_reason = str((latest or {}).get("primary_reason") or "")
            verdict = str((latest or {}).get("verdict") or "")
            primary_blocker = ""
            if latest:
                gates = _safe_json(latest.get("gates_json")) or []
                for g in gates[:10]:
                    if isinstance(g, dict) and g.get("blocking") and g.get("status") in ("fail", "error"):
                        primary_blocker = str(g.get("reason") or g.get("label") or g.get("id") or "")[:100]
                        break

            review_type = classify_review_type(dominant_bucket, verdict, primary_reason, "", sid)
            review_priority = classify_review_priority(dominant_bucket, obs_count, strategy_count, review_type)

            if review_priority == "ignore":
                continue

            # Stable review_id from (strategy_id, ticker, review_type).
            import hashlib
            review_id = hashlib.sha256(
                f"{sid}:{ticker}:{review_type}".encode()
            ).hexdigest()[:12]

            dedup_key = f"{sid}:{ticker}:{review_type}"
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)

            title = _queue_title(review_type, ticker, sid, dominant_bucket)
            queue.append({
                "review_id": review_id,
                "run_id": str((latest or {}).get("run_id") or ""),
                "strategy_id": sid,
                "strategy_name": _strategy_name(sid),
                "ticker": ticker,
                "observation_key": str((latest or {}).get("observation_key") or ""),
                "review_type": review_type,
                "review_priority": review_priority,
                "title": title,
                "reason": primary_reason[:200] if primary_reason else "",
                "latest_verdict": verdict,
                "status_bucket": dominant_bucket,
                "primary_blocker": primary_blocker,
                "observation_count": obs_count,
                "strategy_count": strategy_count,
                "suggested_next_review": "30D outcome computation",
                "created_at": _now(),
            })

    # Sort: high > medium > low, then by obs_count desc.
    _PRIO = {"high": 0, "medium": 1, "low": 2, "ignore": 3}
    queue.sort(key=lambda x: (_PRIO.get(x.get("review_priority", "low"), 2), -x.get("observation_count", 0)))

    return _base_response(
        window_days=days,
        strategy_id=strategy_id,
        queue_count=len(queue),
        queue=queue[:min(int(limit or 50), 250)],
    )


def _queue_title(review_type: str, ticker: str, strategy_id: str, bucket: str) -> str:
    strategy_short = {
        "earnings_calendar": "EC",
        "skew_momentum_vertical": "SKEW",
        "forward_factor_calendar": "FF",
        "stock_momentum": "MOM",
    }.get(strategy_id, strategy_id)
    labels = {
        "repeated_near_miss": f"{ticker} repeated {strategy_short} near-miss",
        "repeated_blocker": f"{ticker} repeated {strategy_short} blocker",
        "cross_strategy_confirmation": f"{ticker} cross-strategy confirmation",
        "ff_research_candidate": f"{ticker} FF research candidate",
        "pass_candidate": f"{ticker} {strategy_short} pass candidate",
        "data_quality_gap": f"{ticker} {strategy_short} data quality gap",
        "provider_budget_gap": f"{ticker} {strategy_short} provider budget gap",
        "lifecycle_consistency_check": f"{ticker} {strategy_short} lifecycle check",
        "portfolio_risk_signal": f"{ticker} portfolio risk signal",
        "unknown": f"{ticker} {strategy_short} review ({bucket})",
    }
    return labels.get(review_type, f"{ticker} {strategy_short} ({review_type})")


# ─── Lane 9: Observation Review Text ──────────────────────────────────────────


def build_observation_review_text(days: int = 7, db_path: str | None = None) -> str:
    """Compact dev-only text summary of the observation review layer."""
    try:
        summary = build_strategy_review_summary(days=days, db_path=db_path)
        blockers = build_repeat_blockers(days=days, limit=5, db_path=db_path)
        queue = build_review_queue(days=days, limit=5, db_path=db_path)

        total = summary.get("total_observations", 0)
        strategy_count = summary.get("strategy_count", 0)
        lines = [f"=== STRATEGY OBSERVATION REVIEW (last {days}d) ==="]
        lines.append(f"Observations: {total} across {strategy_count} strategies")

        top_blockers = blockers.get("blockers", [])[:5]
        if top_blockers:
            lines.append("Top blockers:")
            for b in top_blockers:
                lines.append(f"  - {b.get('strategy_id')}: {b.get('gate_id') or b.get('reason','')} {b.get('count')}x")

        queue_items = queue.get("queue", [])[:5]
        if queue_items:
            lines.append("Review queue:")
            for item in queue_items:
                lines.append(f"  - {item.get('title', '')}")

        return "\n".join(lines)
    except Exception:
        return "=== STRATEGY OBSERVATION REVIEW: unavailable ==="
