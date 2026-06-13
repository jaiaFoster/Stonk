"""Cheap, history-aware candidate ranking before Forward Factor chain fetches."""

from __future__ import annotations

from datetime import date
from typing import Any

from app import config


def score_forward_factor_candidate(
    ticker: str,
    metrics: dict[str, Any] | None = None,
    history: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metrics, history = metrics or {}, history or {}
    price = _number(metrics, "current_price", "price", "last")
    volume = _number(metrics, "average_volume_30d", "avg_volume_30d")
    price_pass = price is None or price >= config.FF_MIN_UNDERLYING_PRICE
    volume_pass = volume is None or volume >= config.FF_MIN_AVERAGE_VOLUME
    hard_blocked = not price_pass or not volume_pass
    expirations = _expirations(metrics)
    front = [value for value in expirations if config.FF_FRONT_DTE_MIN <= _dte(value) <= config.FF_FRONT_DTE_MAX]
    back = [value for value in expirations if config.FF_BACK_DTE_MIN <= _dte(value) <= config.FF_BACK_DTE_MAX]
    has_pair_hint = any(
        config.FF_MIN_EXPIRATION_GAP_DAYS <= _dte(back_value) - _dte(front_value) <= config.FF_MAX_EXPIRATION_GAP_DAYS
        for front_value in front for back_value in back
    )
    failures = history.get("failure_modes", {}) or {}
    no_pair_count = int(failures.get("NO_ELIGIBLE_EXPIRATION_PAIR", 0) or 0)
    liquidity_fail_count = int(failures.get("OPTIONS_ILLIQUID", 0) or 0) + int(failures.get("PACKAGE_SLIPPAGE_TOO_WIDE", 0) or 0)
    valid_pair_seen = bool(history.get("valid_pair_seen"))
    structure_seen = bool(history.get("structure_seen"))
    best_liquidity = str(history.get("best_liquidity_status") or "NOT_EVALUATED").upper()
    options_hint = bool(metrics.get("options_available") or metrics.get("has_options_chain") or expirations)

    score, reasons, warnings, blockers = 0.0, [], [], []
    if price_pass and volume_pass:
        score += 20
        reasons.append("Price and volume clear cheap eligibility.")
    if options_hint:
        score += 15
        reasons.append("Optionability or cached chain metadata is available.")
    if len(expirations) >= 2:
        score += 10
        reasons.append("Cached metadata includes multiple expirations.")
    if front and back:
        score += 25
        reasons.append("Cached metadata includes front and back FF windows.")
    if has_pair_hint or valid_pair_seen:
        score += 10
        reasons.append("A valid FF expiration pair is known or hinted.")
    if structure_seen:
        score += 10
        reasons.append("A matched double-calendar structure was observed.")
    if best_liquidity == "PASS":
        score += 10
        reasons.append("Prior FF structure passed liquidity.")
    elif best_liquidity == "WATCH":
        score += 5
        reasons.append("Prior FF structure reached liquidity review.")
    if no_pair_count >= 3:
        score -= 20
        warnings.append(f"No eligible expiration pair repeated {no_pair_count} times recently.")
    elif no_pair_count:
        score -= 5
        warnings.append(f"No eligible expiration pair seen {no_pair_count} time(s) recently.")
    if liquidity_fail_count >= 3:
        score -= 10
        warnings.append(f"Liquidity failure repeated {liquidity_fail_count} times recently.")
    elif liquidity_fail_count:
        score -= 3
        warnings.append(f"Liquidity failure seen {liquidity_fail_count} time(s) recently.")
    if not price_pass:
        blockers.append("Underlying price is below the configured minimum.")
    if not volume_pass:
        blockers.append("Average volume is below the configured minimum.")
    if price is None or volume is None:
        warnings.append("Cheap price or volume facts are incomplete; final eligibility still required.")
    return {
        "ticker": ticker, "score": round(max(0.0, score), 1), "hard_blocked": hard_blocked,
        "price_pass": price_pass, "volume_pass": volume_pass,
        "optionability_hint": "known" if options_hint else "unknown",
        "expiration_geometry_hint": "valid_pair" if has_pair_hint else "front_and_back" if front and back else "partial_or_unknown",
        "liquidity_hint": best_liquidity, "cached_pair_seen": valid_pair_seen or has_pair_hint,
        "cached_structure_seen": structure_seen, "cached_liquidity_pass_seen": best_liquidity == "PASS",
        "recent_failure_modes": failures, "reasons": reasons, "warnings": warnings, "blockers": blockers,
    }


def select_forward_factor_candidates(
    tickers: list[str],
    market_metrics: dict[str, dict[str, Any]],
    observation_history: dict[str, dict[str, Any]],
    discovery_pool_size: int,
    final_cap: int,
    planner_states: dict[str, str] | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    planner_filter_active = planner_states is not None
    planner_states = planner_states or {}
    scored = [
        score_forward_factor_candidate(ticker, market_metrics.get(ticker), observation_history.get(ticker))
        for ticker in tickers
    ]
    for row in scored:
        row["planner_state"] = planner_states.get(row["ticker"], "UNPLANNED")
        row["planner_approved"] = not planner_filter_active or row["planner_state"] == "APPROVED"
    scored.sort(key=lambda row: (row["hard_blocked"], not row["planner_approved"], -row["score"], row["ticker"]))
    pool = [row for row in scored if not row["hard_blocked"]][:max(1, discovery_pool_size)]
    selected = [row["ticker"] for row in pool if row["planner_approved"]][:max(1, final_cap)]
    pool_names = {row["ticker"] for row in pool}
    for row in scored:
        row["selected_for_discovery_pool"] = row["ticker"] in pool_names
        row["selected_for_cheap_eval"] = row["ticker"] in selected
        row["selected_for_chain_eval"] = False
        row["chain_selection_rank"] = None
        if row["hard_blocked"]:
            row["not_selected_reason"] = "; ".join(row["blockers"]) or "Failed cheap eligibility."
        elif not row["planner_approved"]:
            row["not_selected_reason"] = f"Planner {row['planner_state']} — excluded before final FF selection."
        elif row["ticker"] not in pool_names:
            row["not_selected_reason"] = "Outside FF candidate discovery pool."
        elif row["ticker"] not in selected:
            row["not_selected_reason"] = "Lower candidate-quality score than selected FF candidates."
        else:
            row["not_selected_reason"] = None
    return selected, scored


def what_would_make_positive(row: dict[str, Any]) -> list[str]:
    verdict = str(row.get("verdict") or "").upper()
    items: list[str] = []
    if "NO ELIGIBLE EXPIRATION PAIR" in verdict:
        items.append("Need listed front and back expirations inside the configured FF windows.")
    if str(row.get("liquidity_status") or "").upper() == "FAIL" or "ILLIQUID" in verdict:
        items.append("All four legs and package slippage must pass configured liquidity limits.")
    if "DEBIT TOO LARGE" in verdict:
        items.append("Conservative debit must fall below the configured risk cap.")
    if "NO MATCHED DOUBLE CALENDAR" in verdict:
        items.append("Need matching back-expiration strikes for both approximately 35-delta front legs.")
    if row.get("source_iv_status") != "SOURCE_QUALIFIED":
        items.append("Source-correct ex-earnings IV remains unavailable, so any positive stays diagnostic-only.")
    if not items:
        items.append("Complete all FF signal, structure, liquidity, debit, and source-labeling gates.")
    return items


def _expirations(metrics: dict[str, Any]) -> list[str]:
    for key in ("ff_cached_expirations", "listed_expirations", "options_chain_expirations", "cached_expirations"):
        values = metrics.get(key)
        if isinstance(values, list):
            return [str(value)[:10] for value in values]
    return []


def _dte(value: str) -> int:
    try:
        return (date.fromisoformat(str(value)[:10]) - date.today()).days
    except ValueError:
        return -9999


def _number(metrics: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        try:
            value = metrics.get(key)
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            pass
    return None
