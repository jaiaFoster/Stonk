from types import SimpleNamespace

from app.services.strategy_execution_service import _attach_canonical_opportunities


def test_legacy_rows_remain_and_canonical_rows_added():
    legacy = {"ticker": "TEST", "verdict": "WATCH", "stale_structure": True, "future_field": 7}
    results = {"skew_momentum_vertical": {"rows": [legacy]}}
    block = _attach_canonical_opportunities(results, SimpleNamespace(run_id="run-1"))["skew_momentum_vertical"]
    assert block["rows"][0] is legacy
    assert block["canonical_opportunity_count"] == 1
    assert block["canonical_opportunities"][0]["raw"]["future_field"] == 7
    assert block["canonical_lost_field_counts"] == {}


def test_ff_canonical_rows_never_become_actionable():
    results = {"forward_factor_calendar": {"rows": [{
        "ticker": "TEST", "verdict": "PASS", "can_trade_live": True,
        "can_enter_daily_opportunity": True, "is_source_qualified": True,
    }]}}
    row = _attach_canonical_opportunities(results, SimpleNamespace(run_id="run-1"))["forward_factor_calendar"]["canonical_opportunities"][0]
    assert row["can_trade_live"] is False
    assert row["can_enter_daily_opportunity"] is False


def test_malformed_row_counted_without_breaking_result():
    results = {"earnings_calendar": {"rows": ["bad-row"]}}
    block = _attach_canonical_opportunities(results, SimpleNamespace(run_id=None))["earnings_calendar"]
    assert block["canonical_opportunity_count"] == 0
    assert block["canonical_normalizer_error_count"] == 1
