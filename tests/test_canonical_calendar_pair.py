from app.services.earnings_discovery_quality_service import _canonical_expiration_pair
from app.services.strategy_opportunity_normalizer import normalize_legacy_strategy_row


def test_clean_precheck_pair_survives_normalizer():
    pair = _canonical_expiration_pair(
        ("2026-07-10", "2026-08-07"), {"earnings_date": "2026-07-14"}
    )
    row = {"ticker": "TEST", "verdict": "WATCH", "expiration_pair": pair}
    opportunity = normalize_legacy_strategy_row("earnings_calendar", row)
    assert opportunity.expiration_pair.front_expiration == "2026-07-10"
    assert opportunity.expiration_pair.is_near_miss is False
    assert opportunity.expiration_pair.front_before_earnings is True


def test_near_miss_pair_marked_and_invalid_front_after_event():
    pair = _canonical_expiration_pair(
        ("2026-07-17", "2026-08-21", True), {"earnings_date": "2026-07-14"}
    )
    assert pair["is_near_miss"] is True
    assert pair["front_before_earnings"] is False
    assert pair["gap_days"] < 0
