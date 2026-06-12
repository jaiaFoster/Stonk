"""Diagnostic Forward Factor ranking. Source signal remains dominant."""

from app import config


def rank_forward_factor(row: dict) -> dict:
    ff = float(row.get("forward_factor") or 0)
    factor = min(40.0, max(0.0, ff / max(config.FF_MIN_FORWARD_FACTOR, 0.001) * 32))
    timing = max(0.0, 15 - (abs(int(row.get("front_dte") or 0) - 60) + abs(int(row.get("back_dte") or 0) - 90)) * 0.25)
    source = 15.0 if row.get("front_ex_earnings_iv") is not None and row.get("back_ex_earnings_iv") is not None else 0.0
    liquidity = 15.0 if row.get("liquidity_pass") else 0.0
    call_deviation = float(row["call_delta_deviation"]) if row.get("call_delta_deviation") is not None else 1.0
    put_deviation = float(row["put_delta_deviation"]) if row.get("put_delta_deviation") is not None else 1.0
    delta = max(0.0, 10 - (call_deviation + put_deviation) * 100)
    data = 5.0
    total = round(factor + timing + source + liquidity + delta + data, 1)
    return {"total_score": total, "forward_factor_score": round(factor, 1), "expiration_pair_score": round(timing, 1), "ex_earnings_confidence_score": source, "liquidity_score": liquidity, "delta_accuracy_score": round(delta, 1), "data_freshness_score": data}
