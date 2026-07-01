from scripts.asa_smoke import summarize


def test_summary_collects_canonical_counts_and_safety_flags():
    result = summarize({
        "feature_health": {"status": "ok", "trade_execution_enabled": False, "provider_calls_triggered": False,
                           "checks": {"forward_factor_dry_run": True, "forward_factor_daily_opportunity_excluded": True}},
        "strategy_detail": {"detail": {"earnings_calendar": {
            "rows": [{"ticker": "TEST"}], "canonical_opportunity_count": 1,
            "canonical_normalizer_error_count": 0, "canonical_lost_field_counts": {},
            "canonical_opportunities": [{"blockers": ["OPTIONS_ILLIQUID"]}],
        }}},
        "calendar_trace": {"summary": {"raw_event_count": 10, "passed_count": 2}},
    })
    assert result["trade_execution_enabled"] is False
    assert result["ff_dry_run"] is True
    assert result["strategies"]["earnings_calendar"]["canonical_opportunities"] == 1
    assert result["top_blockers"] == [("OPTIONS_ILLIQUID", 1)]
