from app.models.option_chain_index import OptionChainIndex
from app.services.strategy_structure_builders import (
    build_calendar_structure,
    build_double_calendar_structure,
    build_vertical_structure,
)


def _index():
    rows = []
    for expiration in ("2026-08-01", "2026-09-01"):
        for option_type, deltas in (("call", (0.45, 0.35, 0.25)), ("put", (-0.45, -0.35, -0.25))):
            for strike, delta in zip((95, 100, 105), deltas):
                price_shift = 0.3 if expiration == "2026-09-01" else 0
                rows.append({"expiration": expiration, "option_type": option_type, "strike": strike,
                             "bid": 1.0 + price_shift, "ask": 1.2 + price_shift,
                             "mid": 1.1 + price_shift, "delta": delta, "iv": 0.3,
                             "open_interest": 100, "volume": 20})
    return OptionChainIndex.from_chain_payload("TEST", rows, "2026-07-01")


def test_builds_call_calendar_and_pricing():
    result = build_calendar_structure(_index(), "2026-08-01", "2026-09-01", "call", 100)
    assert result.status == "BUILT"
    assert [leg.position for leg in result.legs] == ["short", "long"]
    assert result.pricing.mid_debit == 0.3
    assert result.pricing.conservative_debit == 0.5
    assert result.pricing.slippage_pct > 0


def test_builds_call_and_put_vertical():
    for option_type, buy, sell in (("call", 0.45, 0.25), ("put", -0.45, -0.25)):
        result = build_vertical_structure(_index(), "2026-08-01", option_type, buy, sell)
        assert result.status == "BUILT"
        assert len(result.legs) == 2
        assert result.risk.max_risk is not None


def test_builds_35_delta_double_calendar():
    result = build_double_calendar_structure(_index(), "2026-08-01", "2026-09-01")
    assert result.status == "BUILT"
    assert len(result.legs) == 4
    assert {leg.option_type for leg in result.legs} == {"put", "call"}


def test_returns_clear_block_when_impossible():
    empty = OptionChainIndex.from_chain_payload("TEST", [], "2026-07-01")
    result = build_calendar_structure(empty, "2026-08-01", "2026-09-01", "call", 100)
    assert result.status == "BLOCKED_STRUCTURE"
    assert result.reason_code == "NO_MATCHED_STRIKES"
