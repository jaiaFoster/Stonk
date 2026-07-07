"""Forward Factor Calendar v1 source math and staged scanner."""

from __future__ import annotations

from datetime import date, datetime, timezone
from math import erf, exp, log, sqrt
from statistics import median
from typing import Any

from app import config
from app.services.forward_factor_candidate_selection_service import score_forward_factor_candidate, select_forward_factor_candidates, what_would_make_positive
from app.services.forward_factor_data_eligibility_service import validate_required_data
from app.services.forward_factor_ranking_service import rank_forward_factor
from app.services.forward_factor_signal_gate_service import evaluate_forward_factor_signal_gate
from app.services.forward_factor_verdict_service import apply_forward_factor_verdict
from app.services.strategy_row_normalization_service import normalize_strategy_row
from app.services.earnings_trust_service import normalize_earnings_trust


def _is_earnings_contaminated(
    expiration_date: str,
    earnings_date_str: str | None,
    window_days: int | None = None,
) -> tuple[bool, str | None]:
    """Check if an expiration is within the earnings contamination window.

    Bidirectional: IV stays elevated 1-2 days post-earnings and builds ~5 days before.
    """
    if not earnings_date_str:
        return False, None
    window = window_days if window_days is not None else int(getattr(config, "FF_EARNINGS_CONTAMINATION_WINDOW_DAYS", 4) or 4)
    try:
        exp_dt = date.fromisoformat(str(expiration_date)[:10])
        earn_dt = date.fromisoformat(str(earnings_date_str)[:10])
        delta = abs((exp_dt - earn_dt).days)
        if delta <= window:
            return True, str(earnings_date_str)[:10]
    except (ValueError, TypeError):
        return False, None
    return False, None


def _find_atm_straddle(chain: list[dict[str, Any]], underlying_price: float) -> dict[str, float] | None:
    """Find ATM call+put mid prices closest to underlying price."""
    calls = [c for c in chain if str(c.get("option_type") or "").lower() == "call" and c.get("strike") is not None and c.get("bid") is not None and c.get("ask") is not None]
    puts = [c for c in chain if str(c.get("option_type") or "").lower() == "put" and c.get("strike") is not None and c.get("bid") is not None and c.get("ask") is not None]
    if not calls or not puts:
        return None
    atm_call = min(calls, key=lambda c: abs(float(c["strike"]) - underlying_price))
    atm_put = min(puts, key=lambda c: abs(float(c["strike"]) - underlying_price))
    call_mid = (float(atm_call["bid"]) + float(atm_call["ask"])) / 2
    put_mid = (float(atm_put["bid"]) + float(atm_put["ask"])) / 2
    if call_mid <= 0 and put_mid <= 0:
        return None
    return {"call_mid": call_mid, "put_mid": put_mid, "call_strike": float(atm_call["strike"]), "put_strike": float(atm_put["strike"]), "straddle_mid": call_mid + put_mid}


def _derive_ex_earnings_iv(
    raw_iv: float | None,
    dte: int,
    is_contaminated: bool,
    chain: list[dict[str, Any]],
    underlying_price: float,
) -> tuple[float | None, str]:
    """Two-stage ex-earnings IV derivation.

    Returns (ex_earnings_iv, derivation_method).
    Path B (clean): raw_iv passthrough.
    Path A (contaminated): ATM straddle variance stripping, haircut fallback.
    """
    if raw_iv is None:
        return None, "raw_iv_unavailable"
    if not is_contaminated:
        return raw_iv, "path_b_clean"
    straddle = _find_atm_straddle(chain, underlying_price)
    if straddle and underlying_price > 0 and dte > 0:
        implied_move = 0.85 * straddle["straddle_mid"] / underlying_price
        front_total_var = (raw_iv ** 2) * (dte / 252.0)
        earnings_var = implied_move ** 2
        ex_var = front_total_var - earnings_var
        if ex_var > 1e-12:
            ex_iv = sqrt(ex_var / (dte / 252.0))
            return ex_iv, "path_a_straddle_strip"
    haircut = config.FF_EARNINGS_IV_HAIRCUT_PCT
    return raw_iv * (1.0 - haircut), "path_a_haircut_fallback"


def calculate_forward_factor(front_iv: float, back_iv: float, front_dte: int, back_dte: int) -> dict[str, float]:
    sigma1, sigma2 = float(front_iv), float(back_iv)
    if not (0 < sigma1 <= 5 and 0 < sigma2 <= 5):
        raise ValueError("IV inputs must be annualized decimals, not percentage points.")
    t1, t2 = float(front_dte) / 365.0, float(back_dte) / 365.0
    if t2 <= t1:
        raise ValueError("INVALID_EXPIRATION_ORDER: far expiration must be later than near expiration.")
    forward_variance = ((sigma2 * sigma2 * t2) - (sigma1 * sigma1 * t1)) / (t2 - t1)
    if forward_variance <= 0:
        raise ValueError("INVALID_FORWARD_VARIANCE: implied forward variance must be positive.")
    forward_iv = sqrt(forward_variance)
    if forward_iv <= 0:
        raise ValueError("INVALID_FORWARD_VOLATILITY: implied forward volatility must be positive.")
    return {
        "front_time_years": t1, "back_time_years": t2, "T1": t1, "T2": t2,
        "forward_variance": forward_variance, "forward_iv": forward_iv,
        "forward_factor": sigma1 / forward_iv - 1.0, "threshold": config.FF_MIN_FORWARD_FACTOR,
    }


def eligible_expiration_pairs(expirations: list[str], today: date | None = None) -> list[dict[str, Any]]:
    now = today or date.today()
    dated = sorted((str(value)[:10], (date.fromisoformat(str(value)[:10]) - now).days) for value in expirations)
    pairs = []
    for front, front_dte in dated:
        if front_dte < int(config.FF_MIN_FRONT_LEG_DTE):
            continue
        if not config.FF_FRONT_DTE_MIN <= front_dte <= config.FF_FRONT_DTE_MAX:
            continue
        for back, back_dte in dated:
            gap = back_dte - front_dte
            if config.FF_BACK_DTE_MIN <= back_dte <= config.FF_BACK_DTE_MAX and config.FF_MIN_EXPIRATION_GAP_DAYS <= gap <= config.FF_MAX_EXPIRATION_GAP_DAYS:
                pairs.append({
                    "front_expiration": front, "back_expiration": back,
                    "front_dte": front_dte, "back_dte": back_dte, "gap_days": gap,
                    "distance_from_target": abs(front_dte - config.FF_FRONT_TARGET_DTE) + abs(back_dte - config.FF_BACK_TARGET_DTE),
                })
    pairs.sort(key=lambda row: row["distance_from_target"])
    return pairs[: max(1, config.FF_EXPIRATION_PAIRS_PER_TICKER)]


def construct_double_calendar(front_chain: list[dict[str, Any]], back_chain: list[dict[str, Any]]) -> dict[str, Any] | None:
    result = build_forward_factor_double_calendar_structure(front_chain, back_chain)
    return result if result["structure_status"] == "COMPLETE" else None


def build_forward_factor_double_calendar_structure(front_chain: list[dict[str, Any]], back_chain: list[dict[str, Any]]) -> dict[str, Any]:
    call = _closest_delta(front_chain, "call", config.FF_TARGET_CALL_DELTA)
    put = _closest_delta(front_chain, "put", config.FF_TARGET_PUT_DELTA)
    if not call or not put:
        return _structure_failure("DELTA_DATA_UNAVAILABLE", "Usable front-expiration ±35-delta put and call legs were not available.")
    back_call = _matching_strike(back_chain, "call", call["strike"])
    back_put = _matching_strike(back_chain, "put", put["strike"])
    if not back_call or not back_put:
        return _structure_failure("NO_MATCHED_DOUBLE_CALENDAR", "Matching back-expiration put and call strikes were not both available.", put, call, back_put, back_call)
    legs = {"front_call": call, "back_call": back_call, "front_put": put, "back_put": back_put}
    if not (
        _valid_market(call, require_short_bid=True)
        and _valid_market(put, require_short_bid=True)
        and _valid_market(back_call, require_long_ask=True)
        and _valid_market(back_put, require_long_ask=True)
    ):
        return _structure_failure("INVALID_QUOTES", "One or more four-leg markets had missing, zero, or crossed required quotes.", put, call, back_put, back_call)
    conservative = float(back_put["ask"]) - float(put["bid"]) + float(back_call["ask"]) - float(call["bid"])
    mid = _mid(back_put) - _mid(put) + _mid(back_call) - _mid(call)
    if conservative <= 0 or mid <= 0:
        return _structure_failure("INVALID_DEBIT", "Four-leg package produced a non-positive conservative or mid debit.", put, call, back_put, back_call)
    slippage = max(0.0, conservative - mid) / max(abs(mid), 0.01) * 100
    liquidity = _liquidity_result(legs, slippage)
    return {
        "structure_status": "COMPLETE", "structure_reason": "Matched-strike ±35-delta double calendar constructed.",
        "matched_put_calendar": True, "matched_call_calendar": True,
        "put_strike": float(put["strike"]), "call_strike": float(call["strike"]),
        "front_put_delta": float(put["delta"]), "front_call_delta": float(call["delta"]),
        "put_delta": float(put["delta"]), "call_delta": float(call["delta"]),
        "put_delta_deviation": abs(float(put["delta"]) - config.FF_TARGET_PUT_DELTA),
        "call_delta_deviation": abs(float(call["delta"]) - config.FF_TARGET_CALL_DELTA),
        "front_put_contract": _contract_id(put), "back_put_contract": _contract_id(back_put),
        "front_call_contract": _contract_id(call), "back_call_contract": _contract_id(back_call),
        "front_put_symbol": _contract_id(put), "back_put_symbol": _contract_id(back_put),
        "front_call_symbol": _contract_id(call), "back_call_symbol": _contract_id(back_call),
        "front_put_bid": float(put["bid"]), "back_put_ask": float(back_put["ask"]),
        "front_call_bid": float(call["bid"]), "back_call_ask": float(back_call["ask"]),
        "conservative_debit": round(conservative, 4), "mid_debit": round(mid, 4),
        "package_bid_ask_width": round(max(0.0, conservative - mid) * 2, 4),
        "debit_at_risk": round(conservative * 100, 2), "package_slippage_pct": round(slippage, 2),
        "liquidity_status": liquidity["status"], "liquidity_pass": liquidity["status"] == "PASS",
        "liquidity_result": liquidity, "liquidity_checks": liquidity["leg_checks"], "legs": legs,
    }


def build_forward_factor_strategy(
    universe: list[str], market_metrics: dict[str, dict[str, Any]], data_hub: Any,
    run_mode: str = "prod", log_print=None, requirement_plan: dict[str, Any] | None = None,
    observation_history: dict[str, dict[str, Any]] | None = None,
    run_id: str | None = None, run_date: str | None = None,
) -> dict[str, Any]:
    log_print = log_print or (lambda message: None)
    if not config.FORWARD_FACTOR_STRATEGY_ENABLED:
        return _finalize([], [], {}, [], False)
    unique_tickers = sorted(set(str(ticker).upper() for ticker in universe if ticker))
    _prescore = {
        t: score_forward_factor_candidate(t, market_metrics.get(t), (observation_history or {}).get(t)).get("score", 0.0)
        for t in unique_tickers
    }
    ordered = sorted(unique_tickers, key=lambda t: (-_prescore.get(t, 0.0), t))
    is_dev = run_mode == "dev"
    cap_label = "dev" if is_dev else "strategy"
    selection_label = "dev" if is_dev else "production"
    cap = config.FF_DEV_MAX_TICKERS_PER_RUN if run_mode == "dev" else config.FF_MAX_TICKERS_PER_RUN
    supported = [ticker for ticker in ordered if _supported_equity(ticker, market_metrics.get(ticker) or {})]
    plan_by_ticker = (requirement_plan or {}).get("by_ticker", {}) or {}
    planner_states = {
        ticker: str((plan_by_ticker.get(ticker) or {}).get("state") or "UNPLANNED")
        for ticker in supported
    } if requirement_plan is not None else None
    selected, candidate_audit = select_forward_factor_candidates(
        supported, market_metrics, observation_history or {},
        config.FF_CANDIDATE_DISCOVERY_POOL_SIZE, cap,
        planner_states=planner_states,
    )
    audit_by_ticker = {row["ticker"]: row for row in candidate_audit}
    rows, cheap_pass, pair_audit = [], [], []
    stage = {
        "universe": len(ordered), "cheap_approved": len(selected), "cheap_evaluated": 0, "cheap_pass": 0,
        "skipped_dev_cap": max(0, len(supported) - len(selected)), "skipped_provider_budget": 0,
        "unsupported": max(0, len(ordered) - len(supported)),
        "chain_approved": 0, "chain_fetch": 0, "chain_sets": 0, "expiration_coverage_pass": 0,
        "expiration_pairs": 0, "valid_forward_variance": 0, "ff_calculated": 0,
        "source_ff_calculated": 0, "diagnostic_formula_calculated": 0, "structures": 0,
        "structure_attempts": 0, "liquidity_complete": 0, "diagnostic_only": 0, "earnings_contaminated": 0, "earnings_clean": 0,
        "planner_blocked": 0, "recent_fail_skipped": 0, "discovery_overrides": 0, "near_miss_ff": 0,
        "prefilter_supported_equities": len(supported),
        "prefilter_price_pass": sum(_known_price_pass(market_metrics.get(ticker) or {}) for ticker in supported),
        "prefilter_volume_pass": sum(_known_volume_pass(market_metrics.get(ticker) or {}) for ticker in supported),
        "candidate_pool_size": sum(bool(row.get("selected_for_discovery_pool")) for row in candidate_audit),
        "planner_approved_candidates": sum(bool(row.get("planner_approved")) for row in candidate_audit),
        "final_selected": len(selected), "pre_eval_skipped": 0,
        "repeat_no_pair_candidates": sum(int((row.get("recent_failure_modes") or {}).get("NO_ELIGIBLE_EXPIRATION_PAIR", 0) or 0) >= 3 for row in candidate_audit),
        "repeat_liquidity_fail_candidates": sum(
            int((row.get("recent_failure_modes") or {}).get("OPTIONS_ILLIQUID", 0) or 0)
            + int((row.get("recent_failure_modes") or {}).get("PACKAGE_SLIPPAGE_TOO_WIDE", 0) or 0) >= 3
            for row in candidate_audit
        ),
    }
    selected_states = ", ".join(f"{ticker}={(plan_by_ticker.get(ticker) or {}).get('state', 'UNPLANNED')}" for ticker in selected)
    log_print(f"FF service universe count={len(ordered)} selected={selected}")
    _score_labels = ", ".join(f"{t}({_prescore.get(t, 0.0):.0f})" for t in selected)
    _excluded_count = max(0, len(supported) - len(selected))
    log_print(f"FF dev cap: selected [{_score_labels}] by score; excluded {_excluded_count} others")
    log_print(f"FF selected ticker planner states: {selected_states or 'none'}")
    log_print(
        "FF candidate selection: pool="
        f"{stage['candidate_pool_size']} selected={selected}; "
        + "; ".join(f"{row['ticker']}={row['score']}" for row in candidate_audit if row.get("selected_for_cheap_eval"))
    )
    selected_nonapproved = [
        row for row in candidate_audit
        if row.get("selected_for_cheap_eval") and not row.get("planner_approved")
    ]
    if selected_nonapproved:
        log_print(
            "FF selector validation failed: removing non-approved final candidates "
            + ", ".join(f"{row['ticker']}={row['planner_state']}" for row in selected_nonapproved)
        )
        selected = [ticker for ticker in selected if (audit_by_ticker.get(ticker) or {}).get("planner_approved")]
    selected_state_counts = {
        state: sum((audit_by_ticker.get(ticker) or {}).get("planner_state") == state for ticker in selected)
        for state in ("APPROVED", "SKIPPED_DEV_CAP", "SKIPPED_PROVIDER_BUDGET")
    }
    log_print(
        f"FF selector validation: final_selected={len(selected)} approved={selected_state_counts['APPROVED'] if requirement_plan is not None else len(selected)} "
        f"skipped_dev_cap={selected_state_counts['SKIPPED_DEV_CAP']} skipped_provider_budget={selected_state_counts['SKIPPED_PROVIDER_BUDGET']} "
        f"invalid={len(selected_nonapproved)}"
    )
    for ticker in ordered:
        if ticker not in supported:
            rows.append(_blocked(ticker, "FAIL / UNSUPPORTED SECURITY", "Asset type is unsupported for Forward Factor.", data_state="UNSUPPORTED", ff_candidate_stage="cap_skip"))
            continue
        if ticker not in selected:
            _unsel_state = (plan_by_ticker.get(ticker) or {}).get("state") if plan_by_ticker else None
            if _unsel_state == "SKIPPED_PROVIDER_BUDGET":
                stage["skipped_provider_budget"] += 1
                rows.append(_blocked(ticker, "SKIPPED / PROVIDER BUDGET", "Shared provider budget excluded ticker before FF candidate selection.", data_state=_unsel_state, ff_candidate_stage="budget_skipped"))
            else:
                verdict = "SKIPPED / DEV CAP" if is_dev else "SKIPPED / STRATEGY CAP"
                rows.append(_blocked(ticker, verdict, f"Ticker was outside the deterministic FF {cap_label} cap.", data_state="SKIPPED_DEV_CAP" if is_dev else "SKIPPED_STRATEGY_CAP", ff_candidate_stage="cap_skip"))
            continue
        planned_state = (plan_by_ticker.get(ticker) or {}).get("state")
        is_discovery_override = bool((audit_by_ticker.get(ticker) or {}).get("discovery_override"))
        if planned_state == "SKIPPED_DEV_CAP" and not is_discovery_override:
            stage["planner_blocked"] += 1
            stage["pre_eval_skipped"] += 1
            log_print(f"FF {ticker} skipped before evaluation: state={planned_state} reason=global dev planner cap DEV_MAX_TICKERS={config.DEV_MAX_TICKERS}")
            rows.append(_blocked(ticker, "SKIPPED / DEV CAP", "Shared planner did not approve required cheap facts.", data_state=planned_state, ff_candidate_stage="cap_skip"))
            continue
        if is_discovery_override:
            log_print(f"FF {ticker} discovery override: planner_state={planned_state} score={(audit_by_ticker.get(ticker) or {}).get('score', 0)}")
            stage["discovery_overrides"] = stage.get("discovery_overrides", 0) + 1
        if planned_state == "SKIPPED_PROVIDER_BUDGET":
            stage["planner_blocked"] += 1
            stage["pre_eval_skipped"] += 1
            stage["skipped_provider_budget"] += 1
            log_print(f"FF {ticker} skipped before evaluation: state={planned_state} reason=shared provider budget")
            rows.append(_blocked(ticker, "SKIPPED / PROVIDER BUDGET", "Shared provider budget did not approve required cheap facts.", data_state=planned_state, ff_candidate_stage="budget_skipped"))
            continue
        quote = data_hub.get_quote(ticker, required=True, strategy_id="forward_factor_calendar")
        candles = data_hub.get_daily_candles(ticker, min_bars=240, required=True, strategy_id="forward_factor_calendar")
        derived = data_hub.get_derived_metrics(ticker, metrics=["average_volume_30d", "realized_volatility_30d"], required=True, strategy_id="forward_factor_calendar")
        eligibility = validate_required_data(quote, candles, derived, datetime.now(timezone.utc), planned_state)
        stage["cheap_evaluated"] += 1
        if not eligibility["eligible"]:
            rows.append(_eligibility_row(ticker, eligibility))
            continue
        cheap_pass.append((ticker, eligibility))
        stage["cheap_pass"] += 1
    chain_cap = config.FF_DEV_MAX_CHAIN_TICKERS_PER_RUN if is_dev else config.FF_MAX_CHAIN_TICKERS_PER_RUN
    reserve = min(chain_cap, int((requirement_plan or {}).get("forward_factor_chain_reserve", chain_cap)))
    log_print(f"FF chain cap: chain_cap={chain_cap} reserve_from_plan={requirement_plan.get('forward_factor_chain_reserve') if requirement_plan else None} effective_reserve={reserve} cheap_pass_count={len(cheap_pass)}")
    if config.FF_SKIP_IF_ALREADY_FAILED_RECENTLY:
        non_repeat_pass = []
        for ticker, eligibility in cheap_pass:
            recent_modes = (audit_by_ticker.get(ticker) or {}).get("recent_failure_modes") or {}
            repeat_mode = next((mode for mode, count in recent_modes.items() if int(count or 0) >= config.FF_RECENT_FAIL_SKIP_THRESHOLD), None)
            if repeat_mode:
                stage["recent_fail_skipped"] = stage.get("recent_fail_skipped", 0) + 1
                rows.append(_blocked(ticker, "SKIPPED / RECENT REPEAT FAILURE", f"Ticker has {recent_modes[repeat_mode]} recent {repeat_mode} failures; skipped to preserve chain budget.", data_state="RECENT_FAIL_SKIP", data_eligibility=eligibility, ff_candidate_stage="recent_fail_skip"))
            else:
                non_repeat_pass.append((ticker, eligibility))
        cheap_pass = non_repeat_pass
    stage["chain_approved"] = min(chain_cap, reserve, len(cheap_pass))
    cheap_pass.sort(key=lambda item: (-float((audit_by_ticker.get(item[0]) or {}).get("score") or 0), item[0]))
    for rank, (ticker, _) in enumerate(cheap_pass[:stage["chain_approved"]], start=1):
        audit = audit_by_ticker.get(ticker) or {}
        audit["selected_for_chain_eval"] = True
        audit["chain_selection_rank"] = rank
    for ticker, _ in cheap_pass[stage["chain_approved"]:]:
        audit = audit_by_ticker.get(ticker) or {}
        audit["not_selected_reason"] = "FF expensive-chain cap reached after cheap eligibility."
    log_print(f"FF universe: raw={len(ordered)} unsupported={stage['unsupported']} supported_equities={len(supported)}")
    log_print(f"FF candidate prefilter: universe={len(ordered)} supported equities={stage['prefilter_supported_equities']} price-pass={stage['prefilter_price_pass']} volume-pass={stage['prefilter_volume_pass']} selected-for-{selection_label}={len(selected)}")
    log_print(f"FF selected for {selection_label}: {', '.join(selected) or 'none'}; priority=known complete facts, liquidity, stable ticker")
    log_print(f"FF planner: universe={len(ordered)} {cap_label} candidate cap={cap} cheap-data approved={len(selected)} skipped {cap_label} cap={stage['skipped_dev_cap']}")
    log_print(f"FF cheap filter: evaluated={stage['cheap_evaluated']} passed={stage['cheap_pass']} failed={stage['cheap_evaluated'] - stage['cheap_pass']}")
    log_print(f"FF evaluation reconciliation: final_selected={len(selected)} evaluated={stage['cheap_evaluated']} pre_eval_skipped={stage['pre_eval_skipped']}")
    log_print(f"FF expensive-data plan: chain-approved={stage['chain_approved']} chain-skipped-budget={max(0, len(cheap_pass) - chain_cap)}")
    for index, (ticker, eligibility) in enumerate(cheap_pass):
        if index >= stage["chain_approved"]:
            rows.append(_blocked(ticker, "SKIPPED / PROVIDER BUDGET", "FF expensive-chain cap reached after cheap eligibility.", data_state="SKIPPED_PROVIDER_BUDGET", data_eligibility=eligibility, ff_candidate_stage="cheap_eligible"))
            stage["skipped_provider_budget"] += 1
            continue
        log_print(f"FF {ticker}: requesting chain set {config.FF_FRONT_DTE_MIN}-{config.FF_BACK_DTE_MAX} DTE, max_expirations={config.FF_CHAIN_EXPIRATIONS_PER_TICKER}")
        record = data_hub.get_options_chain_set(
            ticker, min_dte=config.FF_FRONT_DTE_MIN, max_dte=config.FF_BACK_DTE_MAX,
            max_expirations=config.FF_CHAIN_EXPIRATIONS_PER_TICKER, required=True, strategy_id="forward_factor_calendar",
        )
        payload = _payload(record)
        if not payload:
            state = _last_hub_state(data_hub, ticker, "options_chain_set")
            if state == "SKIPPED_PROVIDER_BUDGET":
                rows.append(_blocked(ticker, "SKIPPED / PROVIDER BUDGET", "Shared provider budget blocked required FF chain set.", data_state=state, data_eligibility=eligibility, ff_candidate_stage="budget_skipped"))
                stage["skipped_provider_budget"] += 1
            else:
                rows.append(_blocked(ticker, "FAIL / REQUIRED CHAIN DATA UNAVAILABLE", "Required multi-expiration chain set could not be acquired.", data_state=state or "MISSING_PROVIDER_FAILED", data_eligibility=eligibility, ff_candidate_stage="provider_failed"))
            continue
        stage["chain_fetch"] += 1
        stage["chain_sets"] += 1
        expirations = payload.get("expirations", []) or payload.get("retained_expirations", []) or []
        chains = payload.get("chains_by_expiration") or payload.get("chains") or {}
        if not isinstance(chains, dict):
            rows.append(_blocked(ticker, "FAIL / CHAIN DATA QUALITY", "Multi-expiration chain set did not preserve contracts by expiration.", data_eligibility=eligibility, ff_candidate_stage="incomplete"))
            continue
        pairs = eligible_expiration_pairs(expirations)
        front_dates = [value for value in expirations if config.FF_FRONT_DTE_MIN <= _dte(value) <= config.FF_FRONT_DTE_MAX]
        back_dates = [value for value in expirations if config.FF_BACK_DTE_MIN <= _dte(value) <= config.FF_BACK_DTE_MAX]
        log_print(f"FF {ticker}: chain set acquired provider={record.get('provider') or payload.get('provider') or 'unknown'}")
        log_print(f"FF {ticker}: listed expirations={len(payload.get('listed_expirations', []) or expirations)} retained expirations={len(expirations)}")
        log_print(f"FF {ticker}: front-window matches={len(front_dates)} back-window matches={len(back_dates)} valid pairs={len(pairs)}")
        if not pairs:
            rows.append(_blocked(ticker, "FAIL / NO ELIGIBLE EXPIRATION PAIR", "No listed expiration pair fits configured source-target windows.", data_eligibility=eligibility, ff_candidate_stage="no_pair"))
            continue
        stage["expiration_coverage_pass"] += 1
        earnings_record = data_hub.get_earnings_event(
            ticker, lookahead_days=config.FF_EARNINGS_LOOKAHEAD_DAYS,
            required=False, strategy_id="forward_factor_calendar",
        )
        _earn_payload = _payload(earnings_record)
        earnings_trust = normalize_earnings_trust(_earn_payload)
        _earn_date_str = _earn_payload.get("earnings_date") or _earn_payload.get("event_date") or _earn_payload.get("date")
        _earn_date_str = str(_earn_date_str)[:10] if _earn_date_str else None
        ticker_rows = []
        for pair in pairs:
            front, back = pair["front_expiration"], pair["back_expiration"]
            front_dte, back_dte = pair["front_dte"], pair["back_dte"]
            stage["expiration_pairs"] += 1
            front_chain, back_chain = chains.get(front, []), chains.get(back, [])
            if not isinstance(front_chain, list) or not isinstance(back_chain, list) or not front_chain or not back_chain:
                row = _blocked(ticker, "FAIL / CHAIN DATA QUALITY", "One or both selected expiration chains were empty or malformed.", **pair, data_eligibility=eligibility, ff_candidate_stage="incomplete")
                ticker_rows.append(row)
                pair_audit.append(_pair_audit(row, "not selected — chain data quality"))
                continue
            front_contaminated, front_earn = _is_earnings_contaminated(front, _earn_date_str)
            back_contaminated, back_earn = _is_earnings_contaminated(back, _earn_date_str)
            is_contaminated = front_contaminated or back_contaminated
            contamination_reason = None
            if front_contaminated:
                contamination_reason = f"front expiry {front} within {config.FF_EARNINGS_CONTAMINATION_WINDOW_DAYS}d of earnings {front_earn}"
            elif back_contaminated:
                contamination_reason = f"back expiry {back} within {config.FF_EARNINGS_CONTAMINATION_WINDOW_DAYS}d of earnings {back_earn}"
            if is_contaminated:
                stage["earnings_contaminated"] += 1
            else:
                stage["earnings_clean"] += 1
            source_qualification = "earnings_contaminated" if is_contaminated else "clean"
            iv = _expiration_iv_inputs(
                payload, front, back, front_chain, back_chain,
                front_contaminated=front_contaminated, back_contaminated=back_contaminated,
                underlying_price=eligibility["price"], front_dte=front_dte, back_dte=back_dte,
            )
            log_print(f"[FF] {ticker}: front={front} back={back} → {'earnings_contaminated' if is_contaminated else 'source_qualified=True'}{' (' + contamination_reason + ')' if contamination_reason else ' (no earnings contamination)'} front_iv_method={iv.get('front_iv_derivation_method')} back_iv_method={iv.get('back_iv_derivation_method')}")
            base = {
                **pair, "data_eligibility": eligibility, "earnings_context": _earnings_context(earnings_record, front, back),
                **earnings_trust,
                **iv,
                "earnings_contaminated": is_contaminated,
                "earnings_contamination_reason": contamination_reason,
                "source_qualification": source_qualification,
            }
            raw_formula = _try_formula(iv.get("front_raw_iv"), iv.get("back_raw_iv"), front_dte, back_dte)
            if raw_formula and is_contaminated:
                haircut_front_iv = (iv.get("front_raw_iv") or 0) * (1.0 - config.FF_EARNINGS_IV_HAIRCUT_PCT)
                haircut_ff = _try_formula(haircut_front_iv, iv.get("back_raw_iv"), front_dte, back_dte)
                if haircut_ff and haircut_ff["forward_factor"] + 1e-12 < config.FF_MIN_FORWARD_FACTOR * config.FF_HAIRCUT_GATE_MULTIPLIER:
                    row = _blocked(ticker, "FAIL / HAIRCUT GATE", f"Even with {config.FF_EARNINGS_IV_HAIRCUT_PCT:.0%} IV haircut, FF {haircut_ff['forward_factor']:.4f} < gate {config.FF_MIN_FORWARD_FACTOR * config.FF_HAIRCUT_GATE_MULTIPLIER:.4f}.", **base, haircut_forward_factor=haircut_ff["forward_factor"], ff_candidate_stage="haircut_gate_fail")
                    ticker_rows.append(row)
                    pair_audit.append(_pair_audit(row, "not selected — haircut gate fail"))
                    continue
            if raw_formula:
                base["diagnostic_raw_iv_forward_factor"] = raw_formula["forward_factor"]
                base["diagnostic_raw_iv_formula"] = raw_formula
                base.update({key: raw_formula[key] for key in ("T1", "T2", "forward_variance", "forward_iv")})
                base["diagnostic_only"] = True
                stage["diagnostic_formula_calculated"] += 1
                stage["ff_calculated"] += 1
            front_ex, back_ex = iv.get("front_ex_earnings_iv"), iv.get("back_ex_earnings_iv")
            if front_ex is None or back_ex is None:
                structure = {}
                if raw_formula and config.FF_ALLOW_DIAGNOSTIC_STRUCTURE_WITHOUT_SOURCE_IV:
                    stage["structure_attempts"] += 1
                    structure = build_forward_factor_double_calendar_structure(front_chain, back_chain)
                    if structure.get("structure_status") == "COMPLETE":
                        stage["structures"] += 1
                        stage["liquidity_complete"] += 1
                stage["diagnostic_only"] += int(bool(raw_formula))
                verdict = _diagnostic_structure_verdict(raw_formula, structure)
                blocker = "Source-correct ex-earnings IV is unavailable; raw-IV FF and structure are diagnostic only."
                if structure and structure.get("structure_status") != "COMPLETE":
                    blocker += f" Structure: {structure.get('structure_reason')}"
                row = _blocked(ticker, verdict, blocker, **base, **structure, ff_candidate_stage="incomplete")
                ticker_rows.append(row)
                pair_audit.append(_pair_audit(row, "not selected — source input unavailable"))
                continue
            try:
                formula = calculate_forward_factor(front_ex, back_ex, front_dte, back_dte)
                stage["valid_forward_variance"] += 1
                stage["source_ff_calculated"] += 1
                if not raw_formula:
                    stage["ff_calculated"] += 1
                log_print(f"FF {ticker} {front}/{back}: front_iv={front_ex:.4f} back_iv={back_ex:.4f} forward_variance={formula['forward_variance']:.6f} forward_iv={formula['forward_iv']:.4f} FF={formula['forward_factor']:.4f} threshold={config.FF_MIN_FORWARD_FACTOR:.2f}")
            except ValueError as exc:
                verdict = "FAIL / INVALID EXPIRATION ORDER" if "INVALID_EXPIRATION_ORDER" in str(exc) else "FAIL / INVALID FORWARD VARIANCE"
                row = _blocked(ticker, verdict, str(exc), **base, ff_candidate_stage="incomplete")
                ticker_rows.append(row)
                pair_audit.append(_pair_audit(row, "not selected — invalid variance"))
                continue
            stage["structure_attempts"] += 1
            structure = build_forward_factor_double_calendar_structure(front_chain, back_chain)
            if structure.get("structure_status") == "COMPLETE":
                stage["structures"] += 1
                stage["liquidity_complete"] += 1
            if formula["forward_factor"] + 1e-12 < config.FF_MIN_FORWARD_FACTOR:
                near_miss = formula["forward_factor"] >= config.FF_MIN_FORWARD_FACTOR - config.FF_NEAR_MISS_WINDOW
                if near_miss:
                    stage["near_miss_ff"] += 1
                    log_print(f"FF {ticker} {front}/{back}: near-miss FF={formula['forward_factor']:.4f} threshold={config.FF_MIN_FORWARD_FACTOR:.2f} window={config.FF_NEAR_MISS_WINDOW:.2f}")
                row = _blocked(ticker, "FAIL / FORWARD FACTOR BELOW THRESHOLD", "Forward Factor is below source-reported 0.20 threshold.", **{**base, **formula, **structure}, near_miss_ff=near_miss, ff_candidate_stage="fetched")
                ticker_rows.append(row)
                pair_audit.append(_pair_audit(row, "selected — below threshold (near miss)" if near_miss else "selected — below threshold"))
                continue
            if structure.get("structure_status") != "COMPLETE":
                row = _blocked(ticker, _source_structure_verdict(structure), structure.get("structure_reason") or "Double-calendar structure unavailable.", **{**base, **formula, **structure}, ff_candidate_stage="fetched")
                ticker_rows.append(row)
                pair_audit.append(_pair_audit(row, "not selected — structure incomplete"))
                continue
            row = {
                **_base(ticker), **base, **formula, **structure,
                "ff_candidate_stage": "selected",
                "structure_type": "double_calendar", "scenario_grid": build_scenario_grid(
                    eligibility["price"], structure["put_strike"], structure["call_strike"],
                    structure["conservative_debit"], max(back_dte - front_dte, 1), formula["forward_iv"],
                ),
            }
            _net_debit = float(structure.get("conservative_debit") or 0)
            _fwd_iv = float(formula.get("forward_iv") or 0)
            if _net_debit > 0 and _fwd_iv > 0 and front_ex:
                _margin = _net_debit * 100
                _edge = max(0, (float(front_ex) - _fwd_iv) / _fwd_iv) * _net_debit * 100
                row["edge_on_margin"] = round(_edge / _margin * 100, 2) if _margin > 0 else None
            else:
                row["edge_on_margin"] = None
            row["ranking"] = rank_forward_factor(row)
            row["signal_score"] = row["ranking"]["total_score"]
            row = apply_forward_factor_verdict(row)
            ticker_rows.append(row)
            pair_audit.append(_pair_audit(row, "selected candidate"))
        if not ticker_rows:
            ticker_rows.append(_blocked(ticker, "FAIL / CHAIN DATA QUALITY", "No terminal result was produced from the evaluated chain set.", data_eligibility=eligibility, ff_candidate_stage="incomplete"))
        ticker_rows.sort(key=_terminal_rank)
        rows.append(ticker_rows[0])
    gated_rows = []
    for row in rows:
        ticker = str(row.get("ticker") or "")
        audit = audit_by_ticker.get(ticker) or {}
        enriched = {
            **row,
            "candidate_quality_score": audit.get("score", 0.0),
            "candidate_selection_reasons": audit.get("reasons", []),
            "candidate_selection_warnings": audit.get("warnings", []),
            "recent_failure_modes": audit.get("recent_failure_modes", {}),
            "planner_state": audit.get("planner_state", "UNPLANNED"),
            "selected_for_cheap_eval": bool(audit.get("selected_for_cheap_eval")),
            "selected_for_chain_eval": bool(audit.get("selected_for_chain_eval")),
            "chain_selection_rank": audit.get("chain_selection_rank"),
            "not_selected_reason": audit.get("not_selected_reason"),
        }
        gated = {**enriched, **evaluate_forward_factor_signal_gate(enriched)}
        trust = normalize_earnings_trust(gated)
        gated.update(trust)
        if trust["earnings_trust_label"] in {"conflict_do_not_trade", "unknown_research_only"}:
            warnings = list(gated.get("warnings") or [])
            warnings.append("Earnings contamination trust failed: " + trust["earnings_trust_reason"])
            gated["warnings"] = list(dict.fromkeys(warnings))
            gated["can_trade_live"] = False
            gated["can_enter_daily_opportunity"] = False
        gated["what_would_make_positive"] = what_would_make_positive(gated)
        gated["ff_gates"] = _ff_gates(gated)
        # 29.8: normalized compact fields for pre-30A readiness
        _fg = gated["ff_gates"]
        gated.setdefault("source_qualified", bool(_fg.get("source_qualified")))
        gated.setdefault("chain_approved", bool(_fg.get("chain_approved")))
        gated.setdefault("structure_built", bool(_fg.get("structure_built")))
        gated.setdefault("diagnostic_model", bool(_fg.get("diagnostic_model")))
        gated.setdefault("cheap_eligible", bool(_fg.get("cheap_eligible")))
        gated.setdefault("earnings_contaminated", bool(_fg.get("earnings_contaminated")))
        gated.setdefault("source_qualification", _fg.get("source_qualification") or "not_evaluated")
        gated.setdefault("front_iv", gated.get("front_ex_earnings_iv") or gated.get("front_raw_iv"))
        gated.setdefault("back_iv", gated.get("back_ex_earnings_iv") or gated.get("back_raw_iv"))
        gated.setdefault("ex_earnings_iv", gated.get("front_ex_earnings_iv"))
        gated.setdefault("dry_run", bool(config.FORWARD_FACTOR_DRY_RUN))
        gated.setdefault("can_enter_daily_opportunity", False)
        gated.setdefault("can_trade_live", False)
        normalize_strategy_row(gated, "forward_factor_calendar")
        gated_rows.append(gated)
    if config.FF_JOURNAL_ENABLED:
        try:
            from app.db.ff_journal import historical_ivs as _hist_ivs
            for row in gated_rows:
                _ticker = str(row.get("ticker") or "")
                _current_iv = float(row.get("front_raw_iv") or 0) or None
                if not _ticker or not _current_iv:
                    row["iv_percentile"] = None
                    row["iv_percentile_note"] = "Current IV unavailable"
                    continue
                _history = _hist_ivs(_ticker)
                if len(_history) < 5:
                    row["iv_percentile"] = None
                    row["iv_percentile_note"] = f"Insufficient history ({len(_history)} observations)"
                    continue
                _below = sum(1 for iv in _history if iv <= _current_iv)
                row["iv_percentile"] = round(_below / len(_history) * 100, 1)
                row["iv_percentile_note"] = f"Rank {row['iv_percentile']}% across {len(_history)} journal observations"
        except Exception:
            for row in gated_rows:
                row.setdefault("iv_percentile", None)
                row.setdefault("iv_percentile_note", "Journal unavailable")
    else:
        for row in gated_rows:
            row["iv_percentile"] = None
            row["iv_percentile_note"] = "Journal disabled"
    result = _finalize(gated_rows, ordered, stage, pair_audit, True, candidate_audit)
    log_print(f"FF: expiration_pairs={stage['expiration_pairs']} valid_forward_variance={stage['valid_forward_variance']} FF calculated={stage['ff_calculated']} source-qualified={stage['source_ff_calculated']} diagnostic={stage['diagnostic_formula_calculated']} earnings_clean={stage['earnings_clean']} earnings_contaminated={stage['earnings_contaminated']}")
    log_print(f"FF: structure_attempts={stage['structure_attempts']} structures={stage['structures']} liquidity_complete={stage['liquidity_complete']} pass/watch/fail/skipped={result['summary']['pass_count']}/{result['summary']['watch_count']}/{result['summary']['fail_count']}/{result['summary']['skipped_count']} near_miss_ff={stage['near_miss_ff']} discovery_overrides={stage['discovery_overrides']}")
    log_print(f"FF chain reconciliation: cheap_pass={stage['cheap_pass']} chain_approved={stage['chain_approved']} chain_skipped_budget={max(0, stage['cheap_pass'] - stage['chain_approved'])} chain_sets={stage['chain_sets']}")
    if config.FF_JOURNAL_ENABLED:
        from app.db.ff_journal import write_run, journal_summary
        _journal_run_id = run_id or "unknown"
        _journal_run_date = run_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        written = write_run(_journal_run_id, _journal_run_date, gated_rows)
        log_print(f"FF journal: wrote {written} candidate row(s) for run {_journal_run_id}")
        result["ff_journal"] = journal_summary()
    return result


def build_scenario_grid(underlying: float, put_strike: float, call_strike: float, debit: float, remaining_back_dte: int, volatility: float) -> list[dict[str, Any]]:
    rows = []
    for pct in range(-25, 26, 5):
        price = underlying * (1 + pct / 100)
        front_intrinsic = max(put_strike - price, 0) + max(price - call_strike, 0)
        back_value = _bs(price, put_strike, remaining_back_dte / 365, volatility, False) + _bs(price, call_strike, remaining_back_dte / 365, volatility, True)
        package = back_value - front_intrinsic
        rows.append({"underlying_change_pct": pct, "underlying_price": round(price, 2), "estimated_package_value": round(package, 2), "estimated_pnl_dollars": round((package - debit) * 100, 2), "label": "MODEL ESTIMATE — NOT GUARANTEED"})
    return rows


def _expiration_iv_inputs(
    payload, front, back, front_chain, back_chain,
    front_contaminated: bool = False, back_contaminated: bool = False,
    underlying_price: float = 0.0, front_dte: int = 0, back_dte: int = 0,
) -> dict[str, Any]:
    metadata = payload.get("expiration_metrics", {}) or {}
    front_meta, back_meta = metadata.get(front, {}) or {}, metadata.get(back, {}) or {}
    front_raw = front_meta.get("raw_iv") or _median_field(front_chain, "iv")
    back_raw = back_meta.get("raw_iv") or _median_field(back_chain, "iv")
    front_ex = front_meta.get("ex_earnings_iv") or _median_field(front_chain, "ex_earnings_iv")
    back_ex = back_meta.get("ex_earnings_iv") or _median_field(back_chain, "ex_earnings_iv")
    front_method, back_method = "explicit_source_field" if front_ex is not None else None, "explicit_source_field" if back_ex is not None else None
    front_implied_move, back_implied_move = None, None
    if front_ex is None and front_raw is not None:
        derived, method = _derive_ex_earnings_iv(front_raw, front_dte, front_contaminated, front_chain, underlying_price)
        front_ex = derived
        front_method = method
        if method == "path_a_straddle_strip":
            straddle = _find_atm_straddle(front_chain, underlying_price)
            if straddle and underlying_price > 0:
                front_implied_move = 0.85 * straddle["straddle_mid"] / underlying_price
    if back_ex is None and back_raw is not None:
        derived, method = _derive_ex_earnings_iv(back_raw, back_dte, back_contaminated, back_chain, underlying_price)
        back_ex = derived
        back_method = method
        if method == "path_a_straddle_strip":
            straddle = _find_atm_straddle(back_chain, underlying_price)
            if straddle and underlying_price > 0:
                back_implied_move = 0.85 * straddle["straddle_mid"] / underlying_price
    has_derived = front_ex is not None and back_ex is not None
    adj_method = front_meta.get("adjustment_method") or back_meta.get("adjustment_method") or _first_field(front_chain + back_chain, "iv_adjustment_method")
    if not adj_method:
        adj_method = front_method or back_method or ("SOURCE_UNAVAILABLE" if not has_derived else "derived")
    adj_confidence = front_meta.get("adjustment_confidence") or back_meta.get("adjustment_confidence")
    if not adj_confidence:
        if has_derived and (front_method or "").startswith("path_a"):
            adj_confidence = "medium"
        elif has_derived:
            adj_confidence = "high"
        else:
            adj_confidence = "unavailable"
    return {
        "front_raw_iv": front_raw, "back_raw_iv": back_raw,
        "front_ex_earnings_iv": front_ex, "back_ex_earnings_iv": back_ex,
        "earnings_variance_removed": front_meta.get("earnings_variance_removed") or back_meta.get("earnings_variance_removed"),
        "adjustment_method": adj_method,
        "adjustment_version": front_meta.get("adjustment_version") or back_meta.get("adjustment_version") or _first_field(front_chain + back_chain, "iv_adjustment_version") or "SOURCE_UNSPECIFIED",
        "adjustment_confidence": adj_confidence,
        "front_iv_derivation_method": front_method,
        "back_iv_derivation_method": back_method,
        "front_implied_earnings_move": front_implied_move,
        "back_implied_earnings_move": back_implied_move,
    }


def _eligibility_row(ticker: str, eligibility: dict[str, Any]) -> dict[str, Any]:
    state = eligibility["data_state"]
    if state == "SKIPPED_DEV_CAP":
        verdict = "SKIPPED / DEV CAP"
        stage = "cap_skip"
    elif state == "SKIPPED_PROVIDER_BUDGET":
        verdict = "SKIPPED / PROVIDER BUDGET"
        stage = "budget_skipped"
    elif state == "STALE":
        verdict = "FAIL / DATA STALE"
        stage = "incomplete"
    elif state == "PRICE_BELOW_MINIMUM":
        verdict = "FAIL / PRICE BELOW MINIMUM"
        stage = "incomplete"
    elif state == "AVERAGE_VOLUME_BELOW_MINIMUM":
        verdict = "FAIL / AVERAGE VOLUME BELOW MINIMUM"
        stage = "incomplete"
    elif state == "UNSUPPORTED":
        verdict = "SKIPPED / UNSUPPORTED SECURITY"
        stage = "cap_skip"
    else:
        verdict = "FAIL / DATA UNAVAILABLE"
        stage = "incomplete"
    detail = "Missing: " + ", ".join(eligibility["missing_fields"]) if eligibility["missing_fields"] else "Stale: " + ", ".join(eligibility["stale_fields"])
    return _blocked(ticker, verdict, detail or "Required FF cheap-stage data unavailable.", data_state=state, data_eligibility=eligibility, ff_candidate_stage=stage)


def _market_number(metrics: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        try:
            value = metrics.get(key)
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            pass
    return None


def _supported_equity(ticker: str, metrics: dict[str, Any]) -> bool:
    asset_type = str(metrics.get("asset_type") or metrics.get("security_type") or "equity").lower()
    return ticker.upper() not in config.FF_EXCLUDED_TICKERS and asset_type not in {"crypto", "cryptocurrency", "otc", "forex"}


def _known_price_pass(metrics: dict[str, Any]) -> bool:
    price = _market_number(metrics, "current_price", "price", "last")
    return price is not None and price >= config.FF_MIN_UNDERLYING_PRICE


def _known_volume_pass(metrics: dict[str, Any]) -> bool:
    volume = _market_number(metrics, "average_volume_30d", "avg_volume_30d")
    return volume is not None and volume >= config.FF_MIN_AVERAGE_VOLUME


def _known_cheap_eligible(metrics: dict[str, Any]) -> bool:
    return _known_price_pass(metrics) and _known_volume_pass(metrics)


def _known_cheap_failure(metrics: dict[str, Any]) -> bool:
    price = _market_number(metrics, "current_price", "price", "last")
    volume = _market_number(metrics, "average_volume_30d", "avg_volume_30d")
    return (price is not None and price < config.FF_MIN_UNDERLYING_PRICE) or (volume is not None and volume < config.FF_MIN_AVERAGE_VOLUME)


def _candidate_rank(ticker: str, metrics: dict[str, Any]) -> tuple[Any, ...]:
    return (
        not _known_cheap_eligible(metrics),
        not bool(metrics.get("has_data")),
        not bool(metrics.get("options_available", True)),
        ticker,
    )


def _finalize(rows, scanned, stage, pair_audit, enabled, candidate_audit=None):
    def verdict(row): return str(row.get("verdict") or "").upper()
    summary = {
        "pass_count": sum("PASS" in verdict(row) and "SKIPPED" not in verdict(row) for row in rows),
        "watch_count": sum(verdict(row).startswith("WATCH") for row in rows),
        "skipped_count": sum(verdict(row).startswith("SKIPPED") for row in rows),
    }
    summary["fail_count"] = len(rows) - summary["pass_count"] - summary["watch_count"] - summary["skipped_count"]
    summary["universe_count"] = len(scanned)
    summary["terminal_count"] = len(rows)
    summary["counts_reconcile"] = len(rows) == len(scanned) and summary["pass_count"] + summary["watch_count"] + summary["fail_count"] + summary["skipped_count"] == len(scanned)
    if not summary["counts_reconcile"]:
        summary["accounting_warning"] = f"Terminal rows {len(rows)} did not reconcile to universe {len(scanned)}."
    summary["calculation_complete_observations"] = int((stage or {}).get("ff_calculated", 0))
    summary["positive_signal_count"] = sum(bool(row.get("is_positive_signal")) for row in rows)
    summary["source_qualified_positive_count"] = sum(row.get("signal_tier") == "SOURCE_QUALIFIED_POSITIVE" for row in rows)
    summary["diagnostic_positive_count"] = sum(row.get("signal_tier") == "DIAGNOSTIC_POSITIVE" for row in rows)
    summary["near_positive_count"] = sum(row.get("signal_tier") == "WATCH_NEAR_POSITIVE" for row in rows)
    summary["failed_liquidity_count"] = sum(str(row.get("liquidity_status") or "").upper() == "FAIL" for row in rows)
    readiness = _readiness(stage, summary)
    summary["stage_counts"] = stage
    summary["readiness"] = readiness
    summary["pair_audit"] = pair_audit
    summary["candidate_selection_audit"] = candidate_audit or []
    summary["best_near_positive_ticker"] = _best_near_positive(rows)
    return {"strategy_id": "forward_factor_calendar", "strategy_label": "Forward Factor Calendar", "version": "v1", "enabled": enabled, "dry_run": bool(config.FORWARD_FACTOR_DRY_RUN), "items": rows, "rows": rows, "scanned_tickers": scanned, "stage_counts": stage, "pair_audit": pair_audit, "candidate_selection_audit": candidate_audit or [], "summary": summary, "readiness": readiness}


def _readiness(stage, summary):
    return {
        "formula_fixtures": "pass", "ex_earnings_iv_fixtures": "pass — two-stage derivation active",
        "multi_expiration_retrieval": "pass", "delta_structure_construction": "pass", "liquidity_checks": "pass",
        "live_dry_run_observations": int((stage or {}).get("cheap_evaluated", 0)),
        "calculation_complete_observations": summary.get("calculation_complete_observations", 0),
        "structure_attempt_observations": int((stage or {}).get("structure_attempts", 0)),
        "structure_complete_observations": int((stage or {}).get("structures", 0)),
        "liquidity_complete_observations": int((stage or {}).get("liquidity_complete", 0)),
        "source_qualified_observations": int((stage or {}).get("source_ff_calculated", 0)),
        "diagnostic_only_observations": int((stage or {}).get("diagnostic_only", 0)),
        "dry_run_pass_observations": summary.get("pass_count", 0),
        "backtest_reproduction": "blocked — historical options data unavailable",
    }


def _base(ticker):
    return {"strategy_id": "forward_factor_calendar", "strategy_label": "Forward Factor Calendar", "ticker": ticker, "dry_run": bool(config.FORWARD_FACTOR_DRY_RUN), "formula_version": config.FF_FORMULA_VERSION, "source_spec_version": config.FF_SOURCE_SPEC_VERSION}


def _blocked(ticker: str, verdict: str, blocker: str, ff_candidate_stage: str = "", **fields: Any) -> dict[str, Any]:
    return {**_base(ticker), "verdict": verdict, "primary_blocker": blocker, "next_action": "MANUAL REVIEW REQUIRED — SOURCE DOES NOT SPECIFY AUTOMATIC EXIT", "actionability_score": 0, "ff_candidate_stage": ff_candidate_stage, **fields}


def _best_near_positive(rows):
    candidates = [row for row in rows if row.get("signal_tier") in {"DIAGNOSTIC_POSITIVE", "SOURCE_QUALIFIED_POSITIVE", "WATCH_NEAR_POSITIVE"}]
    if not candidates:
        return None
    return max(candidates, key=lambda row: float(row.get("signal_score") or 0)).get("ticker")


def _ff_gates(row: dict[str, Any]) -> dict[str, Any]:
    stage = str(row.get("ff_candidate_stage") or "")
    cheap = stage not in {"cap_skip", "budget_skipped", "recent_fail_skip"}
    chain = bool(row.get("selected_for_chain_eval"))
    sq = row.get("front_ex_earnings_iv") is not None and row.get("back_ex_earnings_iv") is not None
    dm = row.get("diagnostic_raw_iv_forward_factor") is not None
    sb = row.get("structure_status") == "COMPLETE"
    contaminated = bool(row.get("earnings_contaminated"))
    reason = None
    if not cheap:
        reason = "cheap_eligible"
    elif not chain:
        reason = "chain_approved"
    elif not sb:
        reason = "structure_built"
    elif contaminated:
        reason = "earnings_contaminated"
    return {
        "cheap_eligible": cheap, "chain_approved": chain,
        "source_qualified": bool(sq) and not contaminated, "diagnostic_model": bool(dm),
        "structure_built": bool(sb), "gate_fail_reason": reason,
        "earnings_contaminated": contaminated,
        "source_qualification": row.get("source_qualification"),
        "contamination_reason": row.get("earnings_contamination_reason"),
    }
def _pair_audit(row, disposition): return {"ticker": row.get("ticker"), "front_expiration": row.get("front_expiration"), "back_expiration": row.get("back_expiration"), "forward_factor": row.get("forward_factor"), "diagnostic_raw_iv_forward_factor": row.get("diagnostic_raw_iv_forward_factor"), "verdict": row.get("verdict"), "disposition": disposition}
def _try_formula(front, back, front_dte, back_dte):
    try: return calculate_forward_factor(front, back, front_dte, back_dte) if front is not None and back is not None else None
    except ValueError: return None
def _dte(value): return (date.fromisoformat(str(value)[:10]) - date.today()).days
def _earnings_context(record, front, back):
    payload = _payload(record)
    raw_date = payload.get("earnings_date") or payload.get("event_date") or payload.get("date")
    position = "UNKNOWN"
    if raw_date:
        try:
            event = date.fromisoformat(str(raw_date)[:10])
            front_date, back_date = date.fromisoformat(str(front)[:10]), date.fromisoformat(str(back)[:10])
            position = "BEFORE_FRONT" if event <= front_date else "BETWEEN_FRONT_AND_BACK" if event <= back_date else "AFTER_BACK"
        except ValueError:
            position = "UNKNOWN"
    return {
        "earnings_date": raw_date, "earnings_time": payload.get("earnings_time") or payload.get("time"),
        "earnings_position": position, "source": record.get("provider") if isinstance(record, dict) else None,
        "confidence": record.get("confidence") if isinstance(record, dict) else "unknown",
        "lookahead_days": config.FF_EARNINGS_LOOKAHEAD_DAYS,
    }
def _median_field(rows, field):
    values = [float(row[field]) for row in rows if isinstance(row, dict) and row.get(field) is not None]
    return median(values) if values else None
def _first_field(rows, field): return next((row.get(field) for row in rows if isinstance(row, dict) and row.get(field) is not None), None)
def _last_hub_state(hub, ticker, data_type):
    audit = list(getattr(getattr(hub, "context", None), "fetch_audit", []) or [])
    return next((row.get("state") for row in reversed(audit) if row.get("ticker") == ticker and row.get("data_type") == data_type), None)
def _chains_have_deltas(front_chain, back_chain):
    return any(row.get("delta") is not None for row in front_chain if isinstance(row, dict)) and any(row.get("delta") is not None for row in back_chain if isinstance(row, dict))
def _diagnostic_structure_verdict(raw_formula, structure):
    if not raw_formula:
        return "FAIL / EX-EARNINGS IV UNAVAILABLE"
    status = structure.get("structure_status") if structure else None
    if status == "DELTA_DATA_UNAVAILABLE":
        return "FAIL / DELTA DATA UNAVAILABLE"
    if status == "NO_MATCHED_DOUBLE_CALENDAR":
        return "FAIL / NO MATCHED DOUBLE CALENDAR"
    if status in {"INVALID_QUOTES", "INVALID_DEBIT"}:
        return "FAIL / OPTIONS ILLIQUID"
    if status == "COMPLETE" and structure.get("liquidity_status") == "FAIL":
        return "FAIL / OPTIONS ILLIQUID"
    if status == "COMPLETE" and float(structure.get("debit_at_risk") or 0) > config.FF_MAX_DEBIT_DOLLARS:
        return "FAIL / DEBIT TOO LARGE"
    return "WATCH / EX-EARNINGS IV UNAVAILABLE"
def _source_structure_verdict(structure):
    status = structure.get("structure_status")
    if status == "DELTA_DATA_UNAVAILABLE":
        return "FAIL / DELTA DATA UNAVAILABLE"
    if status == "NO_MATCHED_DOUBLE_CALENDAR":
        return "FAIL / NO MATCHED DOUBLE CALENDAR"
    return "FAIL / OPTIONS ILLIQUID"
def _structure_failure(status, reason, front_put=None, front_call=None, back_put=None, back_call=None):
    return {
        "structure_status": status, "structure_reason": reason,
        "matched_put_calendar": bool(front_put and back_put), "matched_call_calendar": bool(front_call and back_call),
        "put_strike": float(front_put["strike"]) if front_put else None,
        "call_strike": float(front_call["strike"]) if front_call else None,
        "front_put_delta": float(front_put["delta"]) if front_put and front_put.get("delta") is not None else None,
        "front_call_delta": float(front_call["delta"]) if front_call and front_call.get("delta") is not None else None,
        "front_put_symbol": _contract_id(front_put) if front_put else None,
        "back_put_symbol": _contract_id(back_put) if back_put else None,
        "front_call_symbol": _contract_id(front_call) if front_call else None,
        "back_call_symbol": _contract_id(back_call) if back_call else None,
        "liquidity_status": "NOT_EVALUATED", "liquidity_pass": False,
    }
def _liquidity_result(legs, package_slippage):
    blockers, warnings, checks = [], [], {}
    for name, leg in legs.items():
        spread = round(_spread_pct(leg), 2)
        oi, volume = leg.get("open_interest"), leg.get("volume")
        leg_blockers, leg_warnings = [], []
        if spread > config.FF_MAX_LEG_BID_ASK_PCT:
            leg_blockers.append("bid/ask spread too wide")
        if oi is None:
            leg_warnings.append("open interest unavailable")
        elif float(oi) < config.FF_MIN_LEG_OPEN_INTEREST:
            leg_blockers.append("open interest below minimum")
        if volume is None:
            leg_warnings.append("volume unavailable")
        elif float(volume) < config.FF_MIN_LEG_VOLUME:
            leg_blockers.append("volume below minimum")
        blockers.extend(f"{name}: {item}" for item in leg_blockers)
        warnings.extend(f"{name}: {item}" for item in leg_warnings)
        checks[name] = {"pass": not leg_blockers, "spread_pct": spread, "open_interest": oi, "volume": volume, "blockers": leg_blockers, "warnings": leg_warnings}
    if package_slippage > config.FF_MAX_PACKAGE_SLIPPAGE_PCT:
        blockers.append("package slippage above maximum")
    elif package_slippage > config.FF_WARN_PACKAGE_SLIPPAGE_PCT:
        warnings.append("package slippage above warning threshold")
    status = "FAIL" if blockers else "WATCH" if warnings else "PASS"
    numeric_oi = [float(leg["open_interest"]) for leg in legs.values() if leg.get("open_interest") is not None]
    numeric_volume = [float(leg["volume"]) for leg in legs.values() if leg.get("volume") is not None]
    legs_passing = sum(1 for item in checks.values() if item["pass"])
    if status == "PASS" and legs_passing == len(checks):
        liquidity_quality = "GOOD" if package_slippage <= config.FF_WARN_PACKAGE_SLIPPAGE_PCT else "ACCEPTABLE"
    elif status == "WATCH":
        liquidity_quality = "MARGINAL"
    else:
        liquidity_quality = "POOR"
    return {
        "status": status, "blockers": blockers, "warnings": warnings, "leg_checks": checks,
        "min_open_interest": min(numeric_oi) if numeric_oi else None,
        "min_volume": min(numeric_volume) if numeric_volume else None,
        "max_leg_spread_pct": max((item["spread_pct"] for item in checks.values()), default=None),
        "package_slippage_pct": round(package_slippage, 2),
        "liquidity_quality": liquidity_quality,
        "legs_passing": legs_passing,
        "legs_total": len(checks),
    }
def _terminal_rank(row):
    verdict = str(row.get("verdict") or "")
    return (
        0 if row.get("forward_factor") is not None else 1 if row.get("diagnostic_raw_iv_forward_factor") is not None else 2,
        0 if verdict.startswith("DRY RUN PASS") else 1 if verdict.startswith("WATCH") else 2,
        -float(row.get("forward_factor") or row.get("diagnostic_raw_iv_forward_factor") or -999),
    )
def _closest_delta(chain, option_type, target):
    rows = [row for row in chain if str(row.get("option_type") or "").lower() == option_type and row.get("delta") is not None]
    if not rows: return None
    row = min(rows, key=lambda item: abs(float(item["delta"]) - target))
    return row if abs(float(row["delta"]) - target) <= config.FF_DELTA_TOLERANCE else None
def _matching_strike(chain, option_type, strike): return next((row for row in chain if str(row.get("option_type") or "").lower() == option_type and float(row.get("strike") or 0) == float(strike)), None)
def _valid_market(row, require_short_bid=False, require_long_ask=False):
    if row.get("bid") is None or row.get("ask") is None:
        return False
    bid, ask = float(row["bid"]), float(row["ask"])
    if bid < 0 or ask <= 0 or ask < bid:
        return False
    if require_short_bid and config.FF_REQUIRE_NONZERO_SHORT_BID and bid <= 0:
        return False
    if require_long_ask and config.FF_REQUIRE_VALID_LONG_ASK and ask <= 0:
        return False
    return True
def _spread_pct(row): return (float(row["ask"]) - float(row["bid"])) / max(_mid(row), .01) * 100
def _liquid(row): return float(row.get("open_interest") or 0) >= config.FF_MIN_LEG_OPEN_INTEREST and float(row.get("volume") or 0) >= config.FF_MIN_LEG_VOLUME and _spread_pct(row) <= config.FF_MAX_LEG_BID_ASK_PCT and float(row["bid"]) > 0
def _mid(row): return (float(row["bid"]) + float(row["ask"])) / 2
def _contract_id(row): return row.get("symbol") or row.get("contract_symbol") or row.get("id") or f"{row.get('option_type')}:{row.get('strike')}"
def _payload(record): return (record.get("payload") or record) if isinstance(record, dict) else {}
def _cdf(value): return .5 * (1 + erf(value / sqrt(2)))
def _bs(spot, strike, time_years, volatility, call):
    if time_years <= 0 or volatility <= 0: return max(spot - strike, 0) if call else max(strike - spot, 0)
    d1 = (log(spot / strike) + .5 * volatility * volatility * time_years) / (volatility * sqrt(time_years))
    d2 = d1 - volatility * sqrt(time_years)
    return spot * _cdf(d1) - strike * exp(0) * _cdf(d2) if call else strike * exp(0) * _cdf(-d2) - spot * _cdf(-d1)
