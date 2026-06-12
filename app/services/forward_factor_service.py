"""Forward Factor Calendar v1 source math and staged dry-run scanner."""

from __future__ import annotations

from datetime import date, datetime, timezone
from math import erf, exp, log, sqrt
from statistics import median
from typing import Any

from app import config
from app.services.forward_factor_data_eligibility_service import validate_required_data
from app.services.forward_factor_ranking_service import rank_forward_factor
from app.services.forward_factor_verdict_service import apply_forward_factor_verdict


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


def eligible_expiration_pairs(expirations: list[str], today: date | None = None) -> list[tuple[str, str, int, int]]:
    now = today or date.today()
    dated = sorted((str(value)[:10], (date.fromisoformat(str(value)[:10]) - now).days) for value in expirations)
    pairs = []
    for front, front_dte in dated:
        if not config.FF_FRONT_DTE_MIN <= front_dte <= config.FF_FRONT_DTE_MAX:
            continue
        for back, back_dte in dated:
            gap = back_dte - front_dte
            if config.FF_BACK_DTE_MIN <= back_dte <= config.FF_BACK_DTE_MAX and config.FF_MIN_EXPIRATION_GAP_DAYS <= gap <= config.FF_MAX_EXPIRATION_GAP_DAYS:
                pairs.append((front, back, front_dte, back_dte))
    pairs.sort(key=lambda row: abs(row[2] - config.FF_FRONT_TARGET_DTE) + abs(row[3] - config.FF_BACK_TARGET_DTE))
    return pairs[: max(1, config.FF_EXPIRATION_PAIRS_PER_TICKER)]


def construct_double_calendar(front_chain: list[dict[str, Any]], back_chain: list[dict[str, Any]]) -> dict[str, Any] | None:
    call = _closest_delta(front_chain, "call", config.FF_TARGET_CALL_DELTA)
    put = _closest_delta(front_chain, "put", config.FF_TARGET_PUT_DELTA)
    if not call or not put:
        return None
    back_call = _matching_strike(back_chain, "call", call["strike"])
    back_put = _matching_strike(back_chain, "put", put["strike"])
    if not back_call or not back_put:
        return None
    legs = {"front_call": call, "back_call": back_call, "front_put": put, "back_put": back_put}
    if not (
        _valid_market(call, require_short_bid=True)
        and _valid_market(put, require_short_bid=True)
        and _valid_market(back_call, require_long_ask=True)
        and _valid_market(back_put, require_long_ask=True)
    ):
        return None
    conservative = float(back_put["ask"]) - float(put["bid"]) + float(back_call["ask"]) - float(call["bid"])
    mid = _mid(back_put) - _mid(put) + _mid(back_call) - _mid(call)
    if conservative <= 0 or mid <= 0:
        return None
    slippage = max(0.0, conservative - mid) / max(abs(mid), 0.01) * 100
    liquidity_checks = {
        name: {"pass": _liquid(leg), "spread_pct": round(_spread_pct(leg), 2), "open_interest": leg.get("open_interest"), "volume": leg.get("volume")}
        for name, leg in legs.items()
    }
    liquidity = all(item["pass"] for item in liquidity_checks.values()) and slippage <= config.FF_MAX_PACKAGE_SLIPPAGE_PCT
    return {
        "put_strike": float(put["strike"]), "call_strike": float(call["strike"]),
        "front_put_delta": float(put["delta"]), "front_call_delta": float(call["delta"]),
        "put_delta": float(put["delta"]), "call_delta": float(call["delta"]),
        "put_delta_deviation": abs(float(put["delta"]) - config.FF_TARGET_PUT_DELTA),
        "call_delta_deviation": abs(float(call["delta"]) - config.FF_TARGET_CALL_DELTA),
        "front_put_contract": _contract_id(put), "back_put_contract": _contract_id(back_put),
        "front_call_contract": _contract_id(call), "back_call_contract": _contract_id(back_call),
        "conservative_debit": round(conservative, 4), "mid_debit": round(mid, 4),
        "debit_at_risk": round(conservative * 100, 2), "package_slippage_pct": round(slippage, 2),
        "liquidity_pass": liquidity, "liquidity_checks": liquidity_checks, "legs": legs,
    }


def build_forward_factor_strategy(
    universe: list[str], market_metrics: dict[str, dict[str, Any]], data_hub: Any,
    run_mode: str = "prod", log_print=None, requirement_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    log_print = log_print or (lambda message: None)
    if not config.FORWARD_FACTOR_STRATEGY_ENABLED:
        return _finalize([], [], {}, [], False)
    ordered = sorted(set(str(ticker).upper() for ticker in universe if ticker))
    cap = config.FF_DEV_MAX_TICKERS_PER_RUN if run_mode == "dev" else config.FF_MAX_TICKERS_PER_RUN
    supported = [ticker for ticker in ordered if _supported_equity(market_metrics.get(ticker) or {})]
    ranked = sorted(supported, key=lambda ticker: _candidate_rank(ticker, market_metrics.get(ticker) or {}))
    known_eligible = [ticker for ticker in ranked if _known_cheap_eligible(market_metrics.get(ticker) or {})]
    unknown = [ticker for ticker in ranked if ticker not in known_eligible and not _known_cheap_failure(market_metrics.get(ticker) or {})]
    known_failures = [ticker for ticker in ranked if _known_cheap_failure(market_metrics.get(ticker) or {})]
    selected = (known_eligible + unknown + known_failures)[:cap]
    rows, cheap_pass, pair_audit = [], [], []
    stage = {
        "universe": len(ordered), "cheap_approved": len(selected), "cheap_evaluated": 0, "cheap_pass": 0,
        "skipped_dev_cap": max(0, len(supported) - len(selected)), "skipped_provider_budget": 0,
        "unsupported": max(0, len(ordered) - len(supported)),
        "chain_approved": 0, "chain_fetch": 0, "expiration_pairs": 0, "valid_forward_variance": 0,
        "ff_calculated": 0, "diagnostic_formula_calculated": 0, "structures": 0,
        "prefilter_supported_equities": len(supported),
        "prefilter_price_pass": sum(_known_price_pass(market_metrics.get(ticker) or {}) for ticker in supported),
        "prefilter_volume_pass": sum(_known_volume_pass(market_metrics.get(ticker) or {}) for ticker in supported),
    }
    plan_by_ticker = (requirement_plan or {}).get("by_ticker", {}) or {}
    for ticker in ordered:
        if ticker not in supported:
            rows.append(_blocked(ticker, "SKIPPED / UNSUPPORTED SECURITY", "Asset type is unsupported for Forward Factor.", data_state="UNSUPPORTED"))
            continue
        if ticker not in selected:
            rows.append(_blocked(ticker, "SKIPPED / DEV CAP", "Ticker was outside the deterministic FF run cap.", data_state="SKIPPED_DEV_CAP"))
            continue
        planned_state = (plan_by_ticker.get(ticker) or {}).get("state")
        if planned_state == "SKIPPED_DEV_CAP":
            rows.append(_blocked(ticker, "SKIPPED / DEV CAP", "Shared planner did not approve required cheap facts.", data_state=planned_state))
            continue
        if planned_state == "SKIPPED_PROVIDER_BUDGET":
            stage["skipped_provider_budget"] += 1
            rows.append(_blocked(ticker, "SKIPPED / PROVIDER BUDGET", "Shared provider budget did not approve required cheap facts.", data_state=planned_state))
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
    chain_cap = config.FF_DEV_MAX_CHAIN_TICKERS_PER_RUN if run_mode == "dev" else config.FF_MAX_TICKERS_PER_RUN
    stage["chain_approved"] = min(chain_cap, len(cheap_pass))
    log_print(f"FF candidate prefilter: universe={len(ordered)} supported equities={stage['prefilter_supported_equities']} price-pass={stage['prefilter_price_pass']} volume-pass={stage['prefilter_volume_pass']} selected-for-dev={len(selected)}")
    log_print(f"FF selected for dev: {', '.join(selected) or 'none'}; priority=known complete facts, liquidity, stable ticker")
    log_print(f"FF planner: universe={len(ordered)} dev candidate cap={cap} cheap-data approved={len(selected)} skipped dev cap={stage['skipped_dev_cap']}")
    log_print(f"FF cheap filter: evaluated={stage['cheap_evaluated']} passed={stage['cheap_pass']} failed={stage['cheap_evaluated'] - stage['cheap_pass']}")
    log_print(f"FF expensive-data plan: chain-approved={stage['chain_approved']} chain-skipped-budget={max(0, len(cheap_pass) - chain_cap)}")
    for index, (ticker, eligibility) in enumerate(cheap_pass):
        if index >= chain_cap:
            rows.append(_blocked(ticker, "SKIPPED / PROVIDER BUDGET", "FF expensive-chain cap reached after cheap eligibility.", data_state="SKIPPED_PROVIDER_BUDGET", data_eligibility=eligibility))
            stage["skipped_provider_budget"] += 1
            continue
        if getattr(getattr(data_hub, "budget", None), "remaining", 1) <= 0:
            rows.append(_blocked(ticker, "SKIPPED / PROVIDER BUDGET", "Shared provider budget exhausted before FF chain request.", data_state="SKIPPED_PROVIDER_BUDGET", data_eligibility=eligibility))
            stage["skipped_provider_budget"] += 1
            continue
        record = data_hub.get_options_chain_set(
            ticker, min_dte=config.FF_FRONT_DTE_MIN, max_dte=config.FF_BACK_DTE_MAX,
            max_expirations=config.FF_CHAIN_EXPIRATIONS_PER_TICKER, required=True, strategy_id="forward_factor_calendar",
        )
        payload = _payload(record)
        if not payload:
            rows.append(_blocked(ticker, "FAIL / DATA UNAVAILABLE", "Shared multi-expiration chain request returned no data.", data_state="MISSING_PROVIDER_FAILED", data_eligibility=eligibility))
            continue
        stage["chain_fetch"] += 1
        expirations = payload.get("expirations", []) or []
        pairs = eligible_expiration_pairs(expirations)
        front_dates = [value for value in expirations if config.FF_FRONT_DTE_MIN <= _dte(value) <= config.FF_FRONT_DTE_MAX]
        back_dates = [value for value in expirations if config.FF_BACK_DTE_MIN <= _dte(value) <= config.FF_BACK_DTE_MAX]
        log_print(f"FF {ticker}: listed expirations={len(expirations)}")
        log_print(f"FF {ticker}: eligible front expirations={front_dates}")
        log_print(f"FF {ticker}: eligible back expirations={back_dates}")
        log_print(f"FF {ticker}: evaluating {len(pairs)} expiration pairs")
        if not pairs:
            rows.append(_blocked(ticker, "FAIL / NO ELIGIBLE EXPIRATION PAIR", "No listed expiration pair fits configured source-target windows.", data_eligibility=eligibility))
            continue
        ticker_rows = []
        for front, back, front_dte, back_dte in pairs:
            stage["expiration_pairs"] += 1
            front_chain, back_chain = (payload.get("chains") or {}).get(front, []), (payload.get("chains") or {}).get(back, [])
            iv = _expiration_iv_inputs(payload, front, back, front_chain, back_chain)
            base = {
                "front_expiration": front, "back_expiration": back, "front_dte": front_dte, "back_dte": back_dte,
                "data_eligibility": eligibility, "earnings_context": _earnings_context(payload, front, back),
                **iv,
            }
            raw_formula = _try_formula(iv.get("front_raw_iv"), iv.get("back_raw_iv"), front_dte, back_dte)
            if raw_formula:
                base["diagnostic_raw_iv_forward_factor"] = raw_formula["forward_factor"]
                base["diagnostic_raw_iv_formula"] = raw_formula
                stage["diagnostic_formula_calculated"] += 1
            front_ex, back_ex = iv.get("front_ex_earnings_iv"), iv.get("back_ex_earnings_iv")
            if front_ex is None or back_ex is None:
                row = _blocked(ticker, "FAIL / EX-EARNINGS IV UNAVAILABLE", "Source-correct ex-earnings IV is unavailable; raw-IV FF is diagnostic only.", **base)
                ticker_rows.append(row)
                pair_audit.append(_pair_audit(row, "not selected — source input unavailable"))
                continue
            try:
                formula = calculate_forward_factor(front_ex, back_ex, front_dte, back_dte)
                stage["valid_forward_variance"] += 1
                stage["ff_calculated"] += 1
            except ValueError as exc:
                verdict = "FAIL / INVALID EXPIRATION ORDER" if "INVALID_EXPIRATION_ORDER" in str(exc) else "FAIL / INVALID FORWARD VARIANCE"
                row = _blocked(ticker, verdict, str(exc), **base)
                ticker_rows.append(row)
                pair_audit.append(_pair_audit(row, "not selected — invalid variance"))
                continue
            structure = construct_double_calendar(front_chain, back_chain)
            if not structure:
                row = _blocked(ticker, "FAIL / NO MATCHED DOUBLE CALENDAR", "Matched-strike ±35-delta put and call calendars could not be formed.", **base, **formula)
                ticker_rows.append(row)
                pair_audit.append(_pair_audit(row, "not selected — no matched structure"))
                continue
            stage["structures"] += 1
            row = {
                **_base(ticker), **base, **formula, **structure,
                "structure_type": "double_calendar", "scenario_grid": build_scenario_grid(
                    eligibility["price"], structure["put_strike"], structure["call_strike"],
                    structure["conservative_debit"], max(back_dte - front_dte, 1), formula["forward_iv"],
                ),
            }
            row["ranking"] = rank_forward_factor(row)
            row["signal_score"] = row["ranking"]["total_score"]
            row = apply_forward_factor_verdict(row)
            ticker_rows.append(row)
            pair_audit.append(_pair_audit(row, "selected candidate"))
        ticker_rows.sort(key=lambda row: -float(row.get("signal_score") or 0))
        rows.extend(ticker_rows[: config.FF_MAX_CANDIDATES_PER_TICKER])
    result = _finalize(rows, ordered, stage, pair_audit, True)
    log_print(f"FF: expiration_pairs={stage['expiration_pairs']} valid_forward_variance={stage['valid_forward_variance']} FF calculated={stage['ff_calculated']}")
    log_print(f"FF: structures={stage['structures']} dry pass/watch/fail/skipped={result['summary']['pass_count']}/{result['summary']['watch_count']}/{result['summary']['fail_count']}/{result['summary']['skipped_count']}")
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


def _expiration_iv_inputs(payload, front, back, front_chain, back_chain) -> dict[str, Any]:
    metadata = payload.get("expiration_metrics", {}) or {}
    front_meta, back_meta = metadata.get(front, {}) or {}, metadata.get(back, {}) or {}
    front_ex = front_meta.get("ex_earnings_iv") or _median_field(front_chain, "ex_earnings_iv")
    back_ex = back_meta.get("ex_earnings_iv") or _median_field(back_chain, "ex_earnings_iv")
    return {
        "front_raw_iv": front_meta.get("raw_iv") or _median_field(front_chain, "iv"),
        "back_raw_iv": back_meta.get("raw_iv") or _median_field(back_chain, "iv"),
        "front_ex_earnings_iv": front_ex, "back_ex_earnings_iv": back_ex,
        "earnings_variance_removed": front_meta.get("earnings_variance_removed") or back_meta.get("earnings_variance_removed"),
        "adjustment_method": front_meta.get("adjustment_method") or back_meta.get("adjustment_method") or _first_field(front_chain + back_chain, "iv_adjustment_method") or ("explicit_source_field" if front_ex is not None and back_ex is not None else "SOURCE_UNAVAILABLE"),
        "adjustment_version": front_meta.get("adjustment_version") or back_meta.get("adjustment_version") or _first_field(front_chain + back_chain, "iv_adjustment_version") or "SOURCE_UNSPECIFIED",
        "adjustment_confidence": front_meta.get("adjustment_confidence") or back_meta.get("adjustment_confidence") or ("high" if front_ex is not None and back_ex is not None else "unavailable"),
    }


def _eligibility_row(ticker: str, eligibility: dict[str, Any]) -> dict[str, Any]:
    state = eligibility["data_state"]
    if state == "SKIPPED_DEV_CAP":
        verdict = "SKIPPED / DEV CAP"
    elif state == "SKIPPED_PROVIDER_BUDGET":
        verdict = "SKIPPED / PROVIDER BUDGET"
    elif state == "STALE":
        verdict = "FAIL / DATA STALE"
    elif state == "PRICE_BELOW_MINIMUM":
        verdict = "FAIL / PRICE BELOW MINIMUM"
    elif state == "AVERAGE_VOLUME_BELOW_MINIMUM":
        verdict = "FAIL / AVERAGE VOLUME BELOW MINIMUM"
    elif state == "UNSUPPORTED":
        verdict = "SKIPPED / UNSUPPORTED SECURITY"
    else:
        verdict = "FAIL / DATA UNAVAILABLE"
    detail = "Missing: " + ", ".join(eligibility["missing_fields"]) if eligibility["missing_fields"] else "Stale: " + ", ".join(eligibility["stale_fields"])
    return _blocked(ticker, verdict, detail or "Required FF cheap-stage data unavailable.", data_state=state, data_eligibility=eligibility)


def _market_number(metrics: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        try:
            value = metrics.get(key)
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            pass
    return None


def _supported_equity(metrics: dict[str, Any]) -> bool:
    asset_type = str(metrics.get("asset_type") or metrics.get("security_type") or "equity").lower()
    return asset_type not in {"crypto", "cryptocurrency", "otc", "forex"}


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


def _finalize(rows, scanned, stage, pair_audit, enabled):
    def verdict(row): return str(row.get("verdict") or "").upper()
    summary = {
        "pass_count": sum("DRY RUN PASS" in verdict(row) for row in rows),
        "watch_count": sum(verdict(row).startswith("WATCH") for row in rows),
        "skipped_count": sum(verdict(row).startswith("SKIPPED") for row in rows),
    }
    summary["fail_count"] = len(rows) - summary["pass_count"] - summary["watch_count"] - summary["skipped_count"]
    summary["calculation_complete_observations"] = int((stage or {}).get("ff_calculated", 0))
    readiness = _readiness(stage, summary)
    summary["stage_counts"] = stage
    summary["readiness"] = readiness
    summary["pair_audit"] = pair_audit
    return {"strategy_id": "forward_factor_calendar", "strategy_label": "Forward Factor Calendar", "version": "v1", "enabled": enabled, "dry_run": True, "items": rows, "rows": rows, "scanned_tickers": scanned, "stage_counts": stage, "pair_audit": pair_audit, "summary": summary, "readiness": readiness}


def _readiness(stage, summary):
    return {"formula_fixtures": "pass", "ex_earnings_iv_fixtures": "partial — source screener missing", "multi_expiration_retrieval": "pass", "delta_structure_construction": "pass", "liquidity_checks": "pass", "live_dry_run_observations": int((stage or {}).get("cheap_evaluated", 0)), "calculation_complete_observations": summary.get("calculation_complete_observations", 0), "dry_run_pass_observations": summary.get("pass_count", 0), "backtest_reproduction": "blocked — historical options data unavailable"}


def _base(ticker):
    return {"strategy_id": "forward_factor_calendar", "strategy_label": "Forward Factor Calendar", "ticker": ticker, "dry_run": True, "formula_version": config.FF_FORMULA_VERSION, "source_spec_version": config.FF_SOURCE_SPEC_VERSION}


def _blocked(ticker: str, verdict: str, blocker: str, **fields: Any) -> dict[str, Any]:
    return {**_base(ticker), "verdict": verdict, "primary_blocker": blocker, "next_action": "MANUAL REVIEW REQUIRED — SOURCE DOES NOT SPECIFY AUTOMATIC EXIT", "actionability_score": 0, **fields}


def _pair_audit(row, disposition): return {"ticker": row.get("ticker"), "front_expiration": row.get("front_expiration"), "back_expiration": row.get("back_expiration"), "forward_factor": row.get("forward_factor"), "diagnostic_raw_iv_forward_factor": row.get("diagnostic_raw_iv_forward_factor"), "verdict": row.get("verdict"), "disposition": disposition}
def _try_formula(front, back, front_dte, back_dte):
    try: return calculate_forward_factor(front, back, front_dte, back_dte) if front is not None and back is not None else None
    except ValueError: return None
def _dte(value): return (date.fromisoformat(str(value)[:10]) - date.today()).days
def _earnings_context(payload, front, back): return {"earnings_date": payload.get("earnings_date"), "earnings_time": payload.get("earnings_time"), "timing": "unknown" if not payload.get("earnings_date") else "provider_supplied"}
def _median_field(rows, field):
    values = [float(row[field]) for row in rows if isinstance(row, dict) and row.get(field) is not None]
    return median(values) if values else None
def _first_field(rows, field): return next((row.get(field) for row in rows if isinstance(row, dict) and row.get(field) is not None), None)
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
