"""30B — Normalized row → strategy observation adapter.

Converts 30A normalized strategy rows into compact 30B observation records
for the universal strategy_observations journal. Does not call providers,
mutate strategy logic, or alter Daily Opportunity behavior.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from app.db.strategy_observations import OBSERVATION_SCHEMA_VERSION
from app import config

# Fields that must never appear in journal JSON columns.
_JOURNAL_EXCLUDE: frozenset[str] = frozenset({
    "observation_history", "ff_journal", "raw_chain_data", "raw_json",
    "raw_provider_payload", "full_chain", "options_chain", "chain_snapshot",
    "provider_payload", "debug_trace", "lifecycle_log_full", "payload",
    "scenario_grid", "candidate_selection_audit", "criteria", "requirements",
    "ff_journal_refs", "source_row", "base_calendar_candidate",
})

_MAX_JSON_BYTES = 5000
_MAX_STRING_LEN = 500
_MAX_LIST_LEN = 20


# ─── Public API ───────────────────────────────────────────────────────────────


def build_strategy_observation(
    row: dict[str, Any],
    run_id: str,
    run_date: str,
    strategy_id: str | None = None,
) -> dict[str, Any]:
    """Convert one 30A normalized row into a 30B observation record dict."""
    sid = strategy_id or str(row.get("strategy_id") or "unknown")
    ticker = str(row.get("ticker") or "UNKNOWN").upper().strip()

    gates = list(row.get("gates") or [])
    gate_counts = _count_gates(gates)
    metrics = row.get("metrics") or {}
    reasons = list(row.get("reasons") or [])[:_MAX_LIST_LEN]
    obs_refs = list(row.get("observation_refs") or [])
    obs_key = str(row.get("observation_key") or _fallback_obs_key(sid, ticker))

    # Derive status_bucket from verdict + dry_run policy.
    verdict = str(row.get("verdict") or "")
    status_bucket = _derive_status_bucket(row, sid)

    # Parse observation_key for candidate_type, structure_type, timeframe.
    candidate_type, structure_type, timeframe = _parse_obs_key(obs_key, sid, row)

    # Strategy-specific structure and source summaries.
    structure_summary = _build_structure_summary(row, sid)
    source_summary = _build_source_summary(row, sid)

    return {
        "run_id": run_id,
        "observed_at": str(row.get("observed_at") or datetime.now(timezone.utc).isoformat()),
        "run_date": run_date,
        "strategy_id": sid,
        "strategy_name": str(row.get("strategy_name") or sid),
        "strategy_family": str(row.get("strategy_family") or "unknown"),
        "strategy_row_schema_version": str(
            row.get("strategy_row_schema_version") or "30A.v1"
        ),
        "observation_schema_version": OBSERVATION_SCHEMA_VERSION,
        "ticker": ticker,
        "underlying_symbol": ticker,
        "candidate_type": candidate_type,
        "structure_type": structure_type,
        "timeframe": timeframe,
        "verdict": verdict,
        "friendly_verdict": str(row.get("friendly_verdict") or ""),
        "primary_reason": str(row.get("primary_reason") or "")[:_MAX_STRING_LEN],
        "status_bucket": status_bucket,
        "daily_opportunity_eligible": int(bool(row.get("daily_opportunity_eligible"))),
        "can_trade_live": int(bool(row.get("can_trade_live"))),
        "dry_run": int(bool(row.get("dry_run"))),
        "journal_eligible": int(bool(row.get("journal_eligible"))),
        "data_quality_status": str(row.get("data_quality") or "unknown"),
        "gate_pass_count": gate_counts["pass"],
        "gate_watch_count": gate_counts["watch"],
        "gate_fail_count": gate_counts["fail"],
        "gate_unknown_count": gate_counts["unknown"],
        "gate_skipped_count": gate_counts["skipped"],
        "blocking_gate_count": gate_counts["blocking"],
        "score": _float(row.get("score")),
        "actionability_score": _float(row.get("actionability_score")),
        "observation_key": obs_key,
        "row_hash": _compute_row_hash(sid, ticker, verdict, row),
        # JSON columns
        "metrics_json": _compact_json(metrics),
        "gates_json": _compact_json(_sanitize_gates(gates)),
        "risk_flags_json": _compact_json(_extract_risk_flags(row, sid)),
        "reasons_json": _compact_json([str(r)[:200] for r in reasons]),
        "structure_json": _compact_json(structure_summary),
        "data_quality_json": _compact_json(
            {"status": str(row.get("data_quality") or "unknown")}
        ),
        "observation_refs_json": _compact_json(obs_refs[:10]),
        "source_summary_json": _compact_json(source_summary),
        # Sprint 28 Epic J: provenance and confidence evidence fields
        "provenance_json": _compact_json(_build_provenance_evidence(row, sid)),
        "confidence_evidence_json": _compact_json(_build_confidence_evidence(row, sid)),
    }


def build_observations_from_strategy_results(
    normalized_strategy_results: dict[str, dict[str, Any]],
    run_id: str,
    run_date: str | None = None,
) -> list[dict[str, Any]]:
    """Build observation records from the full normalized_strategy_results dict.

    Works on copies — never mutates source strategy state.
    """
    if not normalized_strategy_results:
        return []
    _run_date = run_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    observations: list[dict[str, Any]] = []
    cap = config.STRATEGY_OBSERVATION_MAX_ROWS_PER_RUN
    for strategy_id, result in (normalized_strategy_results or {}).items():
        if not isinstance(result, dict):
            continue
        rows = list(result.get("canonical_opportunities") or result.get("rows") or [])
        for raw_row in rows:
            if not isinstance(raw_row, dict):
                continue
            if len(observations) >= cap:
                break
            try:
                obs = build_strategy_observation(
                    {**raw_row}, run_id, _run_date, strategy_id=strategy_id
                )
                observations.append(obs)
            except Exception:
                try:
                    observations.append(_error_observation(
                        strategy_id, raw_row, run_id, _run_date
                    ))
                except Exception:
                    pass
    return observations


# ─── status_bucket ────────────────────────────────────────────────────────────


def _derive_status_bucket(row: dict[str, Any], strategy_id: str) -> str:
    """Map normalized row verdict + dry_run policy → broad status bucket."""
    verdict_upper = str(row.get("verdict") or "").upper()
    dry_run = bool(row.get("dry_run"))

    if "SKIPPED" in verdict_upper:
        return "skipped"
    if "FAIL" in verdict_upper or "AVOID" in verdict_upper:
        return "fail"
    if dry_run and strategy_id == "forward_factor_calendar":
        return "dry_run"
    if "PASS" in verdict_upper:
        return "pass"
    # earnings_calendar and stock_momentum use action-based verdicts without "PASS"
    if strategy_id == "earnings_calendar" and "EARNINGS CALENDAR CANDIDATE" in verdict_upper:
        return "pass"
    if strategy_id == "stock_momentum" and verdict_upper in ("CONSIDER ADDING", "ADD", "STRONG ADD"):
        return "pass"
    if "WATCH" in verdict_upper or "NEAR_MISS" in verdict_upper or "TACTICAL" in verdict_upper:
        return "watch"
    # error fallback if gates indicate a hard problem
    if row.get("status_bucket") == "error":
        return "error"
    return "unknown"


# ─── row_hash ─────────────────────────────────────────────────────────────────


def _compute_row_hash(
    strategy_id: str, ticker: str, verdict: str, row: dict[str, Any]
) -> str:
    """Stable sha256 hash of compact, deterministic row content."""
    metrics = row.get("metrics") or {}
    gates = row.get("gates") or []
    # Use only scalar gate fields for hashing — exclude sort_order which can change.
    gates_compact = [
        {"id": g.get("id", ""), "status": g.get("status", ""), "blocking": g.get("blocking", False)}
        for g in gates if isinstance(g, dict)
    ]
    hashable = {
        "strategy_id": strategy_id,
        "ticker": ticker,
        "verdict": verdict,
        "primary_reason": str(row.get("primary_reason") or "")[:200],
        "metrics": {k: v for k, v in sorted(metrics.items()) if not isinstance(v, (dict, list))},
        "gates": sorted(gates_compact, key=lambda g: g.get("id", "")),
    }
    digest = hashlib.sha256(
        json.dumps(hashable, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return digest[:32]


# ─── observation_key parsing ──────────────────────────────────────────────────


def _fallback_obs_key(strategy_id: str, ticker: str) -> str:
    return f"{strategy_id}:{ticker}:unknown:unknown"


def _parse_obs_key(
    obs_key: str, strategy_id: str, row: dict[str, Any]
) -> tuple[str, str, str]:
    """Extract candidate_type, structure_type, timeframe from observation_key."""
    parts = obs_key.split(":")
    candidate_type = parts[2] if len(parts) > 2 else "unknown"
    structure_type = parts[3] if len(parts) > 3 else "unknown"
    timeframe = parts[4] if len(parts) > 4 else ""
    if not timeframe:
        # Try to derive from row fields.
        if strategy_id == "stock_momentum":
            timeframe = "equity"
        elif strategy_id in ("earnings_calendar", "skew_momentum_vertical",
                             "forward_factor_calendar"):
            front = str(row.get("front_expiration") or row.get("front_expiry") or
                        row.get("selected_expiration") or "")
            back = str(row.get("back_expiration") or row.get("back_expiry") or "")
            if front and back:
                timeframe = f"{front}/{back}"
            elif front:
                timeframe = front
    return candidate_type, structure_type, timeframe


# ─── gate helpers ─────────────────────────────────────────────────────────────


def _count_gates(gates: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"pass": 0, "watch": 0, "fail": 0, "unknown": 0, "skipped": 0, "blocking": 0}
    for gate in gates:
        if not isinstance(gate, dict):
            continue
        status = str(gate.get("status") or "unknown").lower()
        if status == "pass":
            counts["pass"] += 1
        elif status in ("watch", "dry_run", "not_applicable"):
            counts["watch"] += 1
        elif status in ("fail", "error"):
            counts["fail"] += 1
        elif status in ("skipped",):
            counts["skipped"] += 1
        else:
            counts["unknown"] += 1
        if gate.get("blocking") and status in ("fail", "error"):
            counts["blocking"] += 1
    return counts


def _sanitize_gates(gates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip any large or raw fields from gate dicts before JSON encoding."""
    return [
        {k: v for k, v in gate.items()
         if k in ("id", "label", "name", "status", "value", "reason", "detail", "blocking", "sort_order")}
        for gate in gates if isinstance(gate, dict)
    ][:30]


# ─── strategy-specific structure & source summaries ──────────────────────────


def _build_structure_summary(row: dict[str, Any], strategy_id: str) -> dict[str, Any]:
    """Extract the Lane-10 strategy-specific structure fields."""
    if strategy_id == "earnings_calendar":
        return {
            "earnings_date": row.get("earnings_date"),
            "earnings_date_confidence": (
                row.get("earnings_date_confidence") or row.get("date_confidence")
            ),
            "earnings_trust_label": row.get("earnings_trust_label"),
            "expiration_pair_diagnostics": _truncate_dict(
                row.get("expiration_pair_diagnostics") or {}, 10
            ),
            "iv_relationship_status": row.get("iv_relationship_status"),
            "liquidity_status": row.get("liquidity_status"),
            "spread_status": row.get("spread_status"),
            "debit_status": row.get("debit_status"),
            "structure_status": row.get("structure_status"),
            "calendar_entry_allowed": row.get("calendar_entry_allowed"),
            "urgent_review": row.get("urgent_review"),
            "front_expiration": row.get("front_expiration"),
            "back_expiration": row.get("back_expiration"),
            "near_miss_reason": _first_near_miss_reason(row),
        }

    if strategy_id == "skew_momentum_vertical":
        return {
            "direction": row.get("direction"),
            "momentum_status": row.get("momentum_status"),
            "skew_status": row.get("skew_status"),
            "atm_iv": row.get("atm_iv"),
            "spread_width": row.get("spread_width"),
            "estimated_debit": row.get("estimated_debit"),
            "structure_status": row.get("structure_status"),
            "selected_expiration": row.get("selected_expiration") or row.get("expiration"),
            "reward_risk": _float(
                (row.get("possible_spread") or {}).get("reward_risk")
                or row.get("reward_risk")
            ),
        }

    if strategy_id == "forward_factor_calendar":
        _row_dry_run = bool(row.get("dry_run", True))
        _rec_mode = str(row.get("recommendation_mode") or ("research" if _row_dry_run else "live_recommendation"))
        return {
            "dry_run": _row_dry_run,
            "recommendation_mode_at_observation": _rec_mode,
            "source_forward_factor": row.get("source_forward_factor"),
            "diagnostic_raw_iv_forward_factor": row.get("diagnostic_raw_iv_forward_factor"),
            "source_qualified": row.get("source_qualified"),
            "cheap_eligible": row.get("cheap_eligible"),
            "chain_approved": row.get("chain_approved"),
            "structure_built": row.get("structure_built"),
            "earnings_contaminated": row.get("earnings_contaminated"),
            "primary_blocker": str(row.get("primary_blocker") or "")[:200],
            "next_action": str(row.get("next_action") or "")[:200],
            "daily_opportunity_eligible": bool(row.get("daily_opportunity_eligible")),
            "can_trade_live": False,
            "ff_candidate_stage": row.get("ff_candidate_stage"),
            # 32C: Four-tier verdict fields
            "near_miss_ff": bool(row.get("near_miss_ff")),
            "watch_zone_ff": bool(row.get("watch_zone_ff")),
            "near_miss_reason": row.get("miss_reason"),
            "miss_distance": row.get("miss_distance"),
        }

    if strategy_id == "stock_momentum":
        return {
            "action": row.get("action"),
            "momentum_score": _float(row.get("momentum_score") or row.get("score")),
            "trend_status": row.get("trend_status"),
            "relative_strength": _float(row.get("relative_strength")),
            "volume_status": row.get("volume_status"),
            "risk_status": row.get("risk_status"),
            "portfolio_status": row.get("portfolio_status"),
            "add_allowed_boolean": row.get("add_allowed_boolean"),
            "gap_suggestion": _truncate_dict(row.get("gap_suggestion") or {}, 5),
        }

    return {}


def _build_source_summary(row: dict[str, Any], strategy_id: str) -> dict[str, Any]:
    """Extract provider/data source provenance fields."""
    if strategy_id == "earnings_calendar":
        return {
            "earnings_source": row.get("earnings_source"),
            "earnings_sources_seen": list(
                (row.get("earnings_sources_seen") or row.get("date_sources") or [])[:6]
            ),
            "date_confidence": row.get("date_confidence") or row.get("earnings_date_confidence"),
            "date_conflict": row.get("date_conflict"),
        }
    if strategy_id == "forward_factor_calendar":
        return {
            "source_qualification": row.get("source_qualification"),
            "source_qualified": row.get("source_qualified"),
            "is_diagnostic_only": row.get("is_diagnostic_only"),
        }
    return {}


# ─── Sprint 28 Epic J: Historical Evidence Builders ───────────────────────────

def _build_provenance_evidence(row: dict[str, Any], strategy_id: str) -> dict[str, Any]:
    """Extract compact provenance evidence for journal persistence.

    Only includes provenance metadata that is useful for historical analysis.
    Never embeds raw payloads or full chain data.
    """
    evidence: dict[str, Any] = {
        "schema_version": "28.J.v1",
    }
    # Universal: attach _ff_provenance if present (FF strategy)
    ff_prov = row.get("_ff_provenance")
    if isinstance(ff_prov, dict):
        evidence["ff_provenance"] = {
            k: v for k, v in ff_prov.items()
            if k in ("calibration_version", "provenance_version", "promotion_active",
                     "dry_run", "can_trade_live")
        }

    # Universal: data diagnostics presence
    diag = row.get("_data_diagnostics")
    if isinstance(diag, dict):
        evidence["data_diagnostics_present"] = True
        evidence["data_complete"] = bool(diag.get("data_complete"))
        evidence["missing_required_fields"] = list(
            (diag.get("missing_required_fields") or [])[:5]
        )
        evidence["overall_confidence"] = diag.get("overall_confidence")

    # Earnings-specific provenance
    if strategy_id == "earnings_calendar":
        conf = row.get("_confidence") or {}
        if conf:
            evidence["earnings_confidence"] = {
                "date_confidence": conf.get("date_confidence"),
                "conflict_detected": conf.get("conflict_detected"),
                "sources_returned_data": list(
                    (conf.get("sources_returned_data") or [])[:4]
                ),
                "trade_allowed": conf.get("trade_allowed"),
            }

    return evidence


def _build_confidence_evidence(row: dict[str, Any], strategy_id: str) -> dict[str, Any]:
    """Extract normalized confidence metadata for the confidence_evidence_json column.

    Captures the snapshot of confidence state at evaluation time for
    historical analysis and trust-over-time tracking.
    """
    ev: dict[str, Any] = {"schema_version": "28.J.v1"}

    # Date confidence (all strategies that track earnings)
    date_conf = row.get("earnings_date_confidence") or row.get("date_confidence")
    if date_conf:
        ev["date_confidence"] = date_conf

    # Provider conflict flag
    conflict = row.get("earnings_source_conflict") or row.get("date_conflict")
    if conflict is not None:
        ev["date_conflict"] = bool(conflict)

    # Source count
    sources = row.get("earnings_sources_seen") or row.get("date_sources") or []
    if sources:
        ev["source_count"] = len(sources)
        ev["sources"] = list(sources[:4])

    # Trust label from earnings trust service
    trust_label = row.get("earnings_trust_label")
    if trust_label:
        ev["trust_label"] = trust_label

    # FF-specific confidence
    if strategy_id == "forward_factor_calendar":
        ev["ff_confidence"] = {
            "forward_factor": _float(row.get("forward_factor")),
            "calibration_version": str(
                getattr(__import__("app.config", fromlist=["config"]), "FF_CALIBRATION_VERSION", "")
            ),
            "near_miss_ff": bool(row.get("near_miss_ff")),
            "watch_zone_ff": bool(row.get("watch_zone_ff")),
            "miss_distance": _float(row.get("miss_distance")),
        }

    return ev


# ─── risk_flags ───────────────────────────────────────────────────────────────


def _extract_risk_flags(row: dict[str, Any], strategy_id: str) -> list[str]:
    flags: list[str] = []
    risks = row.get("risks") or []
    flags.extend(str(r)[:100] for r in risks[:8])
    blockers = row.get("add_blockers") or []
    flags.extend(str(b)[:100] for b in blockers[:5])
    if row.get("date_conflict"):
        flags.append("earnings_date_conflict")
    if row.get("earnings_contaminated"):
        flags.append("earnings_contaminated")
    return flags[:15]


# ─── helpers ──────────────────────────────────────────────────────────────────


def _float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _compact_json(obj: Any) -> str:
    """Serialize to compact JSON, truncated to STRATEGY_OBSERVATION_MAX_JSON_BYTES_PER_ROW."""
    try:
        text = json.dumps(obj, default=str, separators=(",", ":"), sort_keys=True)
        cap = config.STRATEGY_OBSERVATION_MAX_JSON_BYTES_PER_ROW
        if len(text.encode("utf-8")) > cap:
            text = text[:cap] + '"…"}'
        return text
    except Exception:
        return "null"


def _truncate_dict(d: dict[str, Any], max_keys: int) -> dict[str, Any]:
    if not isinstance(d, dict):
        return {}
    keys = list(d.keys())[:max_keys]
    return {k: d[k] for k in keys}


def _first_near_miss_reason(row: dict[str, Any]) -> str | None:
    reasons = row.get("reasons") or []
    for r in reasons[:5]:
        if isinstance(r, str) and ("near" in r.lower() or "miss" in r.lower()):
            return r[:200]
    return None


def _error_observation(
    strategy_id: str, row: dict[str, Any], run_id: str, run_date: str
) -> dict[str, Any]:
    """Minimal safe observation when build_strategy_observation raises."""
    ticker = str(row.get("ticker") or "UNKNOWN").upper()
    return {
        "run_id": run_id,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "run_date": run_date,
        "strategy_id": strategy_id,
        "strategy_name": strategy_id,
        "strategy_family": "unknown",
        "strategy_row_schema_version": str(row.get("strategy_row_schema_version") or "30A.v1"),
        "observation_schema_version": OBSERVATION_SCHEMA_VERSION,
        "ticker": ticker,
        "underlying_symbol": ticker,
        "candidate_type": "unknown",
        "structure_type": "unknown",
        "timeframe": "",
        "verdict": str(row.get("verdict") or ""),
        "friendly_verdict": "",
        "primary_reason": "Observation build failed",
        "status_bucket": "error",
        "daily_opportunity_eligible": 0,
        "can_trade_live": 0,
        "dry_run": int(bool(row.get("dry_run"))),
        "journal_eligible": 0,
        "data_quality_status": "unknown",
        "gate_pass_count": 0, "gate_watch_count": 0, "gate_fail_count": 0,
        "gate_unknown_count": 0, "gate_skipped_count": 0, "blocking_gate_count": 0,
        "score": None,
        "actionability_score": None,
        "observation_key": _fallback_obs_key(strategy_id, ticker),
        "row_hash": _compute_row_hash(strategy_id, ticker, "", row),
        "metrics_json": "null",
        "gates_json": "[]",
        "risk_flags_json": "[]",
        "reasons_json": "[]",
        "structure_json": "null",
        "data_quality_json": "null",
        "observation_refs_json": "[]",
        "source_summary_json": "null",
    }
