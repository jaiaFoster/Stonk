from unittest.mock import patch

from app.providers.earnings_provider import _merge_dedupe_events
from app.services.daily_opportunity_engine_service import _calendar_actions
from app.services.earnings_trust_service import (
    build_earnings_trust_summary,
    earnings_trust_caveats,
    normalize_earnings_trust,
    public_earnings_trust_label,
)
from app.services.public_screener_gate_service import earnings_trust_public_label
from app.services.skew_momentum_vertical_verdict_service import apply_skew_momentum_vertical_verdict
from app.services.unified_calendar_trade_engine_service import _build_new_trade_row


def _event(date="2026-07-09", sources=None, conflict=False, **extra):
    return {
        "ticker": "CTAS",
        "earnings_date": date,
        "earnings_time": "amc",
        "sources_seen": sources or [],
        "earnings_source_conflict": conflict,
        **extra,
    }


def test_multi_source_confirmed():
    row = normalize_earnings_trust(_event(sources=["finnhub", "alphavantage"]))
    assert row["earnings_trust_label"] == "multi_source_confirmed"
    assert row["calendar_entry_allowed"] is True


def test_single_source_verify_blocks_calendar_pass():
    row = normalize_earnings_trust(_event(sources=["finnhub"]))
    assert row["earnings_trust_label"] == "single_source_verify"
    assert row["calendar_entry_allowed"] is False


def test_conflict_do_not_trade():
    row = normalize_earnings_trust(_event(sources=["finnhub", "reference"], conflict=True))
    assert row["earnings_trust_label"] == "conflict_do_not_trade"
    assert row["calendar_entry_allowed"] is False


def test_unknown_research_only():
    row = normalize_earnings_trust({})
    assert row["earnings_trust_label"] == "unknown_research_only"
    assert row["calendar_entry_allowed"] is False
    assert normalize_earnings_trust({"earnings_date": "2026-07-09"})["earnings_trust_label"] == "unknown_research_only"


def test_ctas_cag_style_date_bleed_is_conflict_with_details():
    rows = _merge_dedupe_events([
        {"ticker": "CAG", "earnings_date": "2026-07-15", "source": "finnhub"},
        {"ticker": "CTAS", "earnings_date": "2026-07-15", "source": "finnhub"},
        {"ticker": "CTAS", "earnings_date": "2026-07-09", "source": "reference"},
    ])
    ctas = [row for row in rows if row["ticker"] == "CTAS"]
    assert len(ctas) == 2
    assert all(row["earnings_source_conflict"] for row in ctas)
    assert all({item["date"] for item in row["earnings_conflict_details"]} == {"2026-07-09", "2026-07-15"} for row in ctas)
    assert all(normalize_earnings_trust(row)["calendar_entry_allowed"] is False for row in ctas)


def test_calendar_daily_opportunity_excludes_untrusted_pass():
    engine = {"new_trade_rows": [{"ticker": "CTAS", "verdict": "PASS / ENTRY", "calendar_entry_allowed": False}], "open_trade_rows": []}
    assert _calendar_actions(engine) == []


def test_calendar_conflict_gets_explicit_hard_fail():
    row = _build_new_trade_row(
        _event(sources=["finnhub", "reference"], conflict=True),
        {},
        {},
    )
    assert row["verdict"] == "FAIL / EARNINGS DATE CONFLICT"
    assert row["calendar_entry_allowed"] is False


def test_skew_conflicting_near_term_requirement_blocks():
    row = apply_skew_momentum_vertical_verdict({
        "ticker": "CTAS",
        "momentum_confirmed": True,
        "skew_pass": True,
        "requirements": [{"status": "FAIL", "code": "earnings_trust", "detail": "Conflicting dates"}],
    })
    assert row["verdict"] == "FAIL / EARNINGS DATE CONFLICT"


def test_morning_caveats_are_human_readable():
    rows = [_event(sources=["finnhub"]), _event(sources=["finnhub", "reference"], conflict=True)]
    text = " ".join(earnings_trust_caveats(rows))
    assert "single-source" in text
    assert "Blocked:" in text
    assert "UNKNOWN" not in text


def test_public_labels_are_explicit():
    conflict = _event(sources=["a", "b"], conflict=True)
    assert public_earnings_trust_label(conflict) == "Conflict — do not trade"
    assert earnings_trust_public_label({}) == "Unknown — research only"


def test_public_screener_card_shows_conflict_label_without_raw_unknown():
    from app.main import _public_row_card
    html = _public_row_card({
        "ticker": "CTAS",
        "verdict": "FAIL / EARNINGS DATE CONFLICT",
        **normalize_earnings_trust(_event(sources=["a", "b"], conflict=True)),
    }, "earnings_calendar")
    assert "Earnings Trust: Conflict — do not trade" in html
    assert "Date trust: unknown" not in html


def test_admin_summary_no_data_is_stable_and_read_only():
    with patch("app.services.earnings_trust_service.ReportSnapshotRepository") as repo:
        repo.return_value.latest_success.return_value = None
        result = build_earnings_trust_summary()
    assert result["provider_calls_triggered"] is False
    assert result["calendar_candidates_blocked_by_date_trust"] == 0
    assert result["provider_date_bleed_suspects"] == []


def test_required_strategy_fields_are_complete():
    result = normalize_earnings_trust(_event(sources=["finnhub"]))
    required = {
        "earnings_date_confidence", "earnings_source_count", "earnings_sources_seen",
        "earnings_source_conflict", "earnings_conflict_details", "earnings_trust_label",
        "earnings_trust_reason", "calendar_entry_allowed",
    }
    assert required <= result.keys()


def test_trust_fields_survive_canonical_strategy_serialization():
    from types import SimpleNamespace
    from app.services.strategy_execution_service import _attach_canonical_opportunities
    trust = normalize_earnings_trust(_event(sources=["finnhub"]))
    results = {"earnings_calendar": {"rows": [{"ticker": "CTAS", "verdict": "WATCH", **trust}]}}
    canonical = _attach_canonical_opportunities(results, SimpleNamespace(run_id="run-29g"))["earnings_calendar"]["canonical_opportunities"][0]
    assert canonical["earnings_trust_label"] == "single_source_verify"
    assert canonical["calendar_entry_allowed"] is False


def test_ff_safety_defaults_unchanged():
    from app import config
    assert config.FORWARD_FACTOR_DRY_RUN is True
