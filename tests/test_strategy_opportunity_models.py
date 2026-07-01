"""Regression tests for the canonical strategy opportunity schema."""

from app.models.strategy_opportunity_models import (
    ExpirationPair,
    StrategyLeg,
    StrategyPricing,
    StrategyRisk,
    StrategyStructure,
)
from app.services.strategy_opportunity_normalizer import normalize_legacy_strategy_row


def test_expiration_pair_valid():
    pair = ExpirationPair("2026-07-02", "2026-07-31", 2, 31, "2026-07-09", 9, True, 7, False, "event_aware")
    assert pair.is_valid is True
    assert pair.to_dict()["is_valid"] is True


def test_expiration_pair_invalid_front_after_earnings():
    pair = ExpirationPair("2026-07-17", "2026-08-21", 17, 52, "2026-07-13", 13, False, -4, False, "event_aware")
    assert pair.is_valid is False
    assert pair.to_dict()["is_valid"] is False


def test_expiration_pair_round_trip():
    original = ExpirationPair("2026-07-10", "2026-08-07", 10, 38, "2026-07-14", 14, True, 4, False, "event_aware")
    restored = ExpirationPair.from_dict(original.to_dict())
    assert restored.front_expiration == original.front_expiration
    assert restored.is_valid == original.is_valid


def test_normalize_calendar_row_preserves_expiration_pair():
    row = {
        "ticker": "JPM",
        "verdict": "FAIL / NO VALID CALENDAR STRUCTURE",
        "quality_precheck": {
            "passes_precheck": True,
            "front_expiration": "2026-07-10",
            "back_expiration": "2026-08-07",
            "expiration_pair": None,
            "front_before_earnings": True,
            "earnings_date": "2026-07-14",
            "gap_days": 4,
        },
    }
    opportunity = normalize_legacy_strategy_row("earnings_calendar", row)
    assert opportunity.expiration_pair is not None
    assert opportunity.expiration_pair.front_expiration == "2026-07-10"
    assert opportunity.expiration_pair.is_valid is True
    assert opportunity.to_dict()["expiration_pair"]["front_expiration"] == "2026-07-10"


def test_normalize_skew_row_preserves_stale_structure():
    row = {"ticker": "ORCL", "verdict": "WATCH", "score": 72.0,
           "stale_structure": True, "stale_structure_note": "Stock 37% below long_strike $177.50"}
    opportunity = normalize_legacy_strategy_row("skew_momentum_vertical", row)
    assert opportunity.stale_structure is True
    assert "37%" in opportunity.stale_structure_note
    assert opportunity.to_dict()["raw"]["stale_structure"] is True


def test_normalize_ff_diagnostic_row_cannot_trade():
    row = {"ticker": "SBUX", "verdict": "DIAGNOSTIC / SOURCE QUALIFIED",
           "diagnostic_model": True, "is_source_qualified": True, "forward_factor": 0.968}
    opportunity = normalize_legacy_strategy_row("forward_factor_calendar", row)
    assert opportunity.can_trade_live is False
    assert opportunity.source_mode == "source_qualified"


def test_normalize_daily_opportunity_default_false():
    opportunity = normalize_legacy_strategy_row("stock_momentum", {"ticker": "MU", "verdict": "PASS", "score": 100.0})
    assert opportunity.can_enter_daily_opportunity is False


def test_normalize_malformed_row_does_not_throw():
    opportunity = normalize_legacy_strategy_row("earnings_calendar", {"not": "a real row"})
    assert opportunity.ticker == "UNKNOWN"
    assert opportunity.can_trade_live is False


def test_normalize_non_dict_does_not_throw():
    opportunity = normalize_legacy_strategy_row("earnings_calendar", None)  # type: ignore[arg-type]
    assert opportunity.ticker == "UNKNOWN"
    assert opportunity.source_mode == "unknown"
    assert opportunity.can_trade_live is False


def test_normalize_preserves_unknown_fields_in_raw():
    row = {"ticker": "NFLX", "verdict": "WATCH", "my_custom_signal": 42.5,
           "future_field_not_yet_in_schema": "preserved"}
    opportunity = normalize_legacy_strategy_row("earnings_calendar", row)
    assert opportunity.raw["my_custom_signal"] == 42.5
    assert opportunity.raw["future_field_not_yet_in_schema"] == "preserved"


def test_normalize_date_lineage_single_source():
    row = {"ticker": "CAG", "verdict": "FAIL / NO VALID CALENDAR STRUCTURE",
           "date_confidence": "single_source",
           "earnings": {"earnings_date": "2026-07-08", "sources_seen": ["finnhub"], "date_conflict": False}}
    opportunity = normalize_legacy_strategy_row("earnings_calendar", row)
    assert opportunity.data_lineage is not None
    assert opportunity.data_lineage.earnings_date_confidence == "single_source"
    assert opportunity.data_lineage.earnings_date_sources == ["finnhub"]
    assert opportunity.to_dict()["data_lineage"]["earnings_date_confidence"] == "single_source"


def test_to_dict_complete_no_missing_keys():
    data = normalize_legacy_strategy_row("earnings_calendar", {"ticker": "TEST", "verdict": "PASS", "score": 99.0}).to_dict()
    required = ["strategy_id", "ticker", "verdict", "verdict_tier", "score", "can_trade_live",
                "can_enter_daily_opportunity", "source_mode", "legs", "gates", "blockers", "warnings",
                "raw", "data_lineage", "pipeline_trace", "expiration_pair", "stale_structure",
                "edge_on_margin", "iv_percentile"]
    for key in required:
        assert key in data, f"Missing key in to_dict(): {key}"


def test_leg_fields_survive_serialization():
    leg = StrategyLeg(
        "long_front", "long", "call", 100.0, "2026-08-21", 51,
        1.0, 1.2, 1.1, 0.35, 0.35, 200, 50, 1.1, None,
    )
    assert StrategyLeg.from_dict(leg.to_dict()).to_dict() == leg.to_dict()


def test_structure_models_serialize_without_provider_objects():
    leg = StrategyLeg(
        "long", "long", "call", 100.0, "2026-08-21", 51,
        1.0, 1.2, 1.1, 0.35, 0.35, 200, 50, None, None,
    )
    structure = StrategyStructure(
        "calendar", "TEST", [leg],
        pricing=StrategyPricing(mid_debit=1.1, conservative_debit=1.2, pricing_status="complete"),
        risk=StrategyRisk(max_risk=120.0, risk_status="bounded"),
    )
    data = structure.to_dict()
    assert data["legs"][0]["delta"] == 0.35
    assert data["pricing"]["conservative_debit"] == 1.2
    assert data["risk"]["max_risk"] == 120.0
