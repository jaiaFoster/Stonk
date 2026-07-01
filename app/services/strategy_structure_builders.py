"""Shared option structure builders over OptionChainIndex."""

from __future__ import annotations

from app.models.option_chain_index import IndexedOption, OptionChainIndex
from app.models.strategy_opportunity_models import StrategyLeg, StrategyPricing, StrategyRisk, StrategyStructure


def _leg(option: IndexedOption, leg_id: str, position: str) -> StrategyLeg:
    return StrategyLeg(
        leg_id=leg_id, position=position, option_type=option.option_type, strike=option.strike,
        expiration=option.expiration, dte=option.dte, bid=option.bid, ask=option.ask, mid=option.mid,
        iv=option.iv, delta=option.delta, open_interest=option.open_interest, volume=option.volume,
        current_price=None, average_price=None,
    )


def _blocked(index: OptionChainIndex, structure_type: str, reason_code: str, reason_label: str) -> StrategyStructure:
    return StrategyStructure(
        structure_type=structure_type, ticker=index.ticker, legs=[], status="BLOCKED_STRUCTURE",
        reason_code=reason_code, reason_label=reason_label,
        diagnostics={"chain_quality": index.chain_quality_summary()},
    )


def _debit_pricing(long_options: list[IndexedOption], short_options: list[IndexedOption]) -> StrategyPricing:
    mids = [item.mid for item in long_options + short_options]
    mid = None if any(value is None for value in mids) else sum(item.mid or 0 for item in long_options) - sum(item.mid or 0 for item in short_options)
    conservative = None
    if all(item.ask is not None for item in long_options) and all(item.bid is not None for item in short_options):
        conservative = sum(item.ask or 0 for item in long_options) - sum(item.bid or 0 for item in short_options)
    slippage = None
    if mid not in (None, 0) and conservative is not None:
        slippage = (conservative - mid) / abs(mid) * 100
    return StrategyPricing(
        mid_debit=_round(mid), conservative_debit=_round(conservative),
        slippage_pct=_round(slippage), pricing_status="complete" if conservative is not None else "incomplete",
    )


def build_calendar_structure(
    index: OptionChainIndex, front_exp: str, back_exp: str, option_type: str,
    target_strike: float | None = None,
) -> StrategyStructure:
    front = index.nearest_strike(front_exp, option_type, target_strike) if target_strike is not None else None
    if front is None:
        strikes = sorted(set(index.available_strikes(front_exp, option_type)) & set(index.available_strikes(back_exp, option_type)))
        front = index.option(front_exp, option_type, strikes[0]) if strikes else None
    pair = index.same_strike_pair(front_exp, back_exp, option_type, front.strike) if front else None
    if not pair:
        return _blocked(index, "calendar", "NO_MATCHED_STRIKES", "No same-strike front/back calendar pair.")
    short_front, long_back = pair
    pricing = _debit_pricing([long_back], [short_front])
    risk = StrategyRisk(max_risk=(pricing.conservative_debit * 100 if pricing.conservative_debit is not None else None), risk_status="bounded")
    return StrategyStructure("calendar", index.ticker, [_leg(short_front, "short_front", "short"), _leg(long_back, "long_back", "long")], pricing=pricing, risk=risk)


def build_vertical_structure(
    index: OptionChainIndex, expiration: str, option_type: str,
    buy_delta_target: float, sell_delta_target: float,
) -> StrategyStructure:
    long_option = index.nearest_delta(expiration, option_type, buy_delta_target)
    choices = [index.option(expiration, option_type, strike) for strike in index.available_strikes(expiration, option_type)]
    choices = [item for item in choices if item and item.delta is not None and (long_option is None or item.strike != long_option.strike)]
    short_option = min(choices, key=lambda item: abs(abs(item.delta or 0) - abs(sell_delta_target))) if choices else None
    if not long_option or not short_option:
        return _blocked(index, "vertical", "NO_MATCHED_STRIKES", "No distinct delta-matched vertical legs.")
    pricing = _debit_pricing([long_option], [short_option])
    width = abs(long_option.strike - short_option.strike)
    debit = pricing.conservative_debit
    risk = StrategyRisk(
        max_risk=(debit * 100 if debit is not None else None),
        max_reward=((width - debit) * 100 if debit is not None else None), risk_status="bounded",
    )
    return StrategyStructure("vertical", index.ticker, [_leg(long_option, "long", "long"), _leg(short_option, "short", "short")], pricing=pricing, risk=risk)


def build_double_calendar_structure(
    index: OptionChainIndex, front_exp: str, back_exp: str,
    target_put_delta: float = -0.35, target_call_delta: float = 0.35,
) -> StrategyStructure:
    pairs = []
    for option_type, target in (("put", target_put_delta), ("call", target_call_delta)):
        front = index.nearest_delta(front_exp, option_type, target)
        pair = index.same_strike_pair(front_exp, back_exp, option_type, front.strike) if front else None
        if not pair:
            return _blocked(index, "double_calendar", "NO_MATCHED_STRIKES", f"No matched {option_type} calendar near target delta.")
        pairs.append(pair)
    shorts = [pair[0] for pair in pairs]
    longs = [pair[1] for pair in pairs]
    pricing = _debit_pricing(longs, shorts)
    legs = [
        _leg(shorts[0], "short_front_put", "short"), _leg(longs[0], "long_back_put", "long"),
        _leg(shorts[1], "short_front_call", "short"), _leg(longs[1], "long_back_call", "long"),
    ]
    return StrategyStructure(
        "double_calendar", index.ticker, legs, pricing=pricing,
        risk=StrategyRisk(max_risk=(pricing.conservative_debit * 100 if pricing.conservative_debit is not None else None), risk_status="bounded"),
    )


def _round(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None
