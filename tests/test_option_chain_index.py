from app.models.option_chain_index import OptionChainIndex


def _chain():
    rows = []
    for expiration, dte in (("2026-08-01", 31), ("2026-09-01", 62)):
        for option_type, deltas in (("call", (0.45, 0.35, 0.25)), ("put", (-0.45, -0.35, -0.25))):
            for strike, delta in zip((95, 100, 105), deltas):
                rows.append({"expiration_date": expiration, "option_type": option_type, "strike": strike,
                             "bid": 1.0, "ask": 1.2, "iv": 0.3, "delta": delta,
                             "open_interest": 100, "volume": 10})
    return rows


def test_indexes_fixture_chain():
    index = OptionChainIndex.from_chain_payload("test", _chain(), "2026-07-01")
    assert index.expirations_between(30, 65) == ["2026-08-01", "2026-09-01"]
    assert index.available_strikes("2026-08-01", "call") == [95.0, 100.0, 105.0]
    assert index.chain_quality_summary()["option_count"] == 12


def test_finds_positive_and_negative_35_delta():
    index = OptionChainIndex.from_chain_payload("TEST", _chain(), "2026-07-01")
    assert index.nearest_delta("2026-08-01", "call", 0.35).strike == 100
    assert index.nearest_delta("2026-08-01", "put", -0.35).strike == 100


def test_finds_same_strike_front_back_pair():
    index = OptionChainIndex.from_chain_payload("TEST", _chain(), "2026-07-01")
    front, back = index.same_strike_pair("2026-08-01", "2026-09-01", "call", 100)
    assert front.strike == back.strike == 100


def test_handles_missing_greeks_and_empty_chain():
    row = {"expiration": "2026-08-01", "option_type": "call", "strike": 100, "bid": None, "ask": None}
    index = OptionChainIndex.from_chain_payload("TEST", [row], "2026-07-01")
    assert index.nearest_delta("2026-08-01", "call", 0.35) is None
    assert index.chain_quality_summary()["has_greeks"] is False
    assert OptionChainIndex.from_chain_payload("TEST", {}, "2026-07-01").chain_quality_summary()["empty"] is True
