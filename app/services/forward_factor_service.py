"""Forward Factor Calendar v1 source-math and dry-run scanner."""

from __future__ import annotations

from datetime import date
from math import sqrt
from typing import Any

from app import config
from app.services.forward_factor_ranking_service import rank_forward_factor
from app.services.forward_factor_verdict_service import apply_forward_factor_verdict


def calculate_forward_factor(front_iv: float, back_iv: float, front_dte: int, back_dte: int) -> dict[str, float]:
    """Calculate source-defined annualized forward volatility using calendar days."""
    sigma1, sigma2 = float(front_iv), float(back_iv)
    if not (0 < sigma1 <= 5 and 0 < sigma2 <= 5):
        raise ValueError("IV inputs must be annualized decimals, not percentage points.")
    t1, t2 = float(front_dte) / 365.0, float(back_dte) / 365.0
    if t2 <= t1:
        raise ValueError("Far expiration must be later than near expiration.")
    forward_variance = ((sigma2 * sigma2 * t2) - (sigma1 * sigma1 * t1)) / (t2 - t1)
    if forward_variance <= 0:
        raise ValueError("Implied forward variance must be positive.")
    forward_iv = sqrt(forward_variance)
    if forward_iv <= 0:
        raise ValueError("Implied forward volatility must be positive.")
    return {
        "front_time_years": t1,
        "back_time_years": t2,
        "forward_variance": forward_variance,
        "forward_iv": forward_iv,
        "forward_factor": sigma1 / forward_iv - 1.0,
    }


def eligible_expiration_pairs(expirations: list[str], today: date | None = None) -> list[tuple[str, str, int, int]]:
    now = today or date.today()
    dated = sorted((value, (date.fromisoformat(str(value)[:10]) - now).days) for value in expirations)
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
    if not all(_valid_market(leg) for leg in legs.values()):
        return None
    conservative = back_put["ask"] - put["bid"] + back_call["ask"] - call["bid"]
    mid = _mid(back_put) - _mid(put) + _mid(back_call) - _mid(call)
    slippage = max(0.0, conservative - mid) / max(abs(mid), 0.01) * 100
    liquidity = all(_liquid(leg) for leg in legs.values()) and slippage <= config.FF_MAX_PACKAGE_SLIPPAGE_PCT
    return {
        "put_strike": float(put["strike"]), "call_strike": float(call["strike"]),
        "put_delta": float(put["delta"]), "call_delta": float(call["delta"]),
        "put_delta_deviation": abs(float(put["delta"]) - config.FF_TARGET_PUT_DELTA),
        "call_delta_deviation": abs(float(call["delta"]) - config.FF_TARGET_CALL_DELTA),
        "conservative_debit": round(conservative, 4), "mid_debit": round(mid, 4),
        "debit_at_risk": round(conservative * 100, 2), "package_slippage_pct": round(slippage, 2),
        "liquidity_pass": liquidity, "legs": legs,
    }


def build_forward_factor_strategy(
    universe: list[str], market_metrics: dict[str, dict[str, Any]], data_hub: Any,
    run_mode: str = "prod", log_print=None,
) -> dict[str, Any]:
    log = log_print or (lambda message: None)
    if not config.FORWARD_FACTOR_STRATEGY_ENABLED:
        return {"strategy_id": "forward_factor_calendar", "strategy_label": "Forward Factor Calendar", "version": "v1", "enabled": False, "dry_run": True, "items": [], "rows": [], "scanned_tickers": [], "stage_counts": {}}
    cap = config.FF_DEV_MAX_TICKERS_PER_RUN if run_mode == "dev" else config.FF_MAX_TICKERS_PER_RUN
    tickers = list(dict.fromkeys(str(t).upper() for t in universe if t))[:cap]
    rows: list[dict[str, Any]] = []
    stage = {"universe": len(universe), "cheap_pass": 0, "chain_fetch": 0, "expiration_pairs": 0, "valid_forward_variance": 0, "structures": 0}
    for ticker in tickers:
        metrics = market_metrics.get(ticker, {})
        if not metrics.get("required_market_data_complete") or float(metrics.get("current_price") or 0) < config.FF_MIN_UNDERLYING_PRICE or float(metrics.get("average_volume_30d") or 0) < config.FF_MIN_AVERAGE_VOLUME:
            rows.append(_blocked(ticker, "FAIL / DATA STALE", "Cheap eligibility requires fresh quote, candles, price, and average volume."))
            continue
        stage["cheap_pass"] += 1
        record = data_hub.get_options_chain(ticker, min_dte=config.FF_FRONT_DTE_MIN, max_dte=config.FF_BACK_DTE_MAX, expirations=6, required=True, strategy_id="forward_factor_calendar")
        payload = _payload(record)
        stage["chain_fetch"] += 1
        pairs = eligible_expiration_pairs(payload.get("expirations", []))
        if not pairs:
            rows.append(_blocked(ticker, "FAIL / NO ELIGIBLE EXPIRATION PAIR", "No listed expiration pair fits configured source-target windows."))
            continue
        for front, back, front_dte, back_dte in pairs:
            stage["expiration_pairs"] += 1
            metrics_by_exp = payload.get("expiration_metrics", {})
            front_meta, back_meta = metrics_by_exp.get(front, {}), metrics_by_exp.get(back, {})
            front_ex, back_ex = front_meta.get("ex_earnings_iv"), back_meta.get("ex_earnings_iv")
            if front_ex is None or back_ex is None:
                rows.append(_blocked(ticker, "FAIL / EX-EARNINGS IV UNAVAILABLE", "Source-correct ex-earnings IV is unavailable; raw IV is diagnostic only.", front_expiration=front, back_expiration=back, front_dte=front_dte, back_dte=back_dte))
                continue
            try:
                formula = calculate_forward_factor(front_ex, back_ex, front_dte, back_dte)
                stage["valid_forward_variance"] += 1
            except ValueError as exc:
                rows.append(_blocked(ticker, "FAIL / INVALID FORWARD VARIANCE", str(exc), front_expiration=front, back_expiration=back))
                continue
            structure = construct_double_calendar((payload.get("chains") or {}).get(front, []), (payload.get("chains") or {}).get(back, []))
            if not structure:
                rows.append(_blocked(ticker, "FAIL / NO MATCHED DOUBLE CALENDAR", "Matched-strike ±35-delta put and call calendars could not be formed.", front_expiration=front, back_expiration=back, **formula))
                continue
            stage["structures"] += 1
            row = {
                "strategy_id": "forward_factor_calendar", "strategy_label": "Forward Factor Calendar", "ticker": ticker,
                "structure_type": "double_calendar", "front_expiration": front, "back_expiration": back,
                "front_dte": front_dte, "back_dte": back_dte, "front_ex_earnings_iv": front_ex, "back_ex_earnings_iv": back_ex,
                "formula_version": config.FF_FORMULA_VERSION, "source_spec_version": config.FF_SOURCE_SPEC_VERSION,
                "dry_run": True, **formula, **structure,
            }
            row["ranking"] = rank_forward_factor(row)
            row["signal_score"] = row["ranking"]["total_score"]
            rows.append(apply_forward_factor_verdict(row))
    rows.sort(key=lambda row: -float(row.get("signal_score") or row.get("score") or 0))
    result = {"strategy_id": "forward_factor_calendar", "strategy_label": "Forward Factor Calendar", "version": "v1", "enabled": True, "dry_run": True, "items": rows, "rows": rows, "scanned_tickers": tickers, "stage_counts": stage}
    log(f"FF: universe={stage['universe']} cheap_pass={stage['cheap_pass']} chain_fetch={stage['chain_fetch']}")
    log(f"FF: expiration_pairs={stage['expiration_pairs']} valid_forward_variance={stage['valid_forward_variance']} structures={stage['structures']}")
    return result


def _blocked(ticker: str, verdict: str, blocker: str, **fields: Any) -> dict[str, Any]:
    return {"strategy_id": "forward_factor_calendar", "strategy_label": "Forward Factor Calendar", "ticker": ticker, "verdict": verdict, "primary_blocker": blocker, "next_action": "MANUAL REVIEW REQUIRED — SOURCE DOES NOT SPECIFY AUTOMATIC EXIT", "actionability_score": 0, "dry_run": True, "formula_version": config.FF_FORMULA_VERSION, "source_spec_version": config.FF_SOURCE_SPEC_VERSION, **fields}


def _closest_delta(chain: list[dict[str, Any]], option_type: str, target: float) -> dict[str, Any] | None:
    rows = [row for row in chain if str(row.get("option_type") or "").lower() == option_type and row.get("delta") is not None]
    if not rows:
        return None
    row = min(rows, key=lambda item: abs(float(item["delta"]) - target))
    return row if abs(float(row["delta"]) - target) <= config.FF_DELTA_TOLERANCE else None


def _matching_strike(chain: list[dict[str, Any]], option_type: str, strike: Any) -> dict[str, Any] | None:
    return next((row for row in chain if str(row.get("option_type") or "").lower() == option_type and float(row.get("strike") or 0) == float(strike)), None)


def _valid_market(row: dict[str, Any]) -> bool:
    return row.get("bid") is not None and row.get("ask") is not None and float(row["bid"]) >= 0 and float(row["ask"]) > 0 and float(row["ask"]) >= float(row["bid"])


def _liquid(row: dict[str, Any]) -> bool:
    spread = (float(row["ask"]) - float(row["bid"])) / max(_mid(row), 0.01) * 100
    return float(row.get("open_interest") or 0) >= config.FF_MIN_LEG_OPEN_INTEREST and float(row.get("volume") or 0) >= config.FF_MIN_LEG_VOLUME and spread <= config.FF_MAX_LEG_BID_ASK_PCT and float(row["bid"]) > 0


def _mid(row: dict[str, Any]) -> float:
    return (float(row["bid"]) + float(row["ask"])) / 2


def _payload(record: Any) -> dict[str, Any]:
    return (record.get("payload") or record) if isinstance(record, dict) else {}
