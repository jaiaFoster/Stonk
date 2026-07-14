from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from app.models.calendar_evolution_policy import load_calendar_evolution_policy


def test_payload_profile_treats_legacy_summary_as_archive_not_hot_warning():
    from app.services.payload_profile_service import build_payload_size_profile

    profile = build_payload_size_profile(
        payload="small",
        positions=[],
        news={},
        recommendations=[],
        snapshot={},
        log=[],
        report_summary={"legacy": "x" * 1_200_000},
    )

    assert profile["summary_payload_status"] == "healthy"
    assert profile["active_summary_json_bytes"] == 0
    assert profile["summary_json_bytes"] > 1_000_000
    assert profile["legacy_report_summary_json_bytes"] > 1_000_000
    assert profile["sections_bytes"]["report_summary_json"] == 0
    assert profile["sections_bytes"]["legacy_report_summary_json"] > 1_000_000


def test_payload_warnings_recompute_from_compact_hot_path_even_if_old_status_warning():
    from app.services.payload_profile_service import build_payload_warnings

    profile = {
        "summary_payload_status": "warning",
        "summary_json_bytes": 1_200_000,
        "compact_summary_json_bytes": 1_842,
        "api_hot_path_bytes": 1_842,
        "legacy_report_summary_json_bytes": 2_640_000,
    }
    warnings = build_payload_warnings(profile)
    assert not any(w.get("name") == "payload_size_warning" for w in warnings)


def test_calendar_rejected_candidate_persists_as_explainability_row():
    from app.services.strategy_execution_service import collect_strategy_results
    from app.services.strategy_row_repository import StrategyRowRepository
    from app.services.calendar_opportunity_projection_service import build_calendar_canonical_projection

    quality_row = {
        "ticker": "NFLX",
        "earnings_date": "2026-07-16",
        "event": {"ticker": "NFLX", "earnings_date": "2026-07-16", "session_label": "AMC"},
        "checks": [
            {"name": "Option expirations", "status": "FAIL", "detail": "Only pre-earnings short leg 2026-07-10 has 1 DTE; minimum is 7."},
        ],
        "primary_rejection_reason": "No valid pre-earnings short expiration with sufficient DTE/time value.",
        "entry_window_status": "ENTRY_WINDOW_CLOSED",
        "entry_window_open": False,
        "entry_window_reason": "Only pre-earnings short leg 2026-07-10 has 1 DTE; minimum is 7.",
        "short_leg_expires_before_earnings": True,
        "short_leg_dte_minimum": 7,
        "short_leg_time_value_minimum": 7,
        "short_leg_does_not_span_event": True,
        "entry_window_front_expiration": "2026-07-10",
        "entry_window_front_dte": 1,
        "available_pre_earnings_expirations": [{"expiration": "2026-07-10", "dte": 1}],
        "rejected_expirations": [
            {"expiration": "2026-07-10", "dte": 1, "reason": "below minimum short-leg DTE 7"},
            {"expiration": "2026-07-17", "dte": 8, "reason": "short leg spans or follows earnings"},
        ],
        "proposed_short_expiration": "2026-07-10",
        "proposed_long_expiration": "2026-07-17",
    }
    engine = build_calendar_canonical_projection(
        earnings_trade_discovery={"items": []},
        earnings_discovery_quality={"items": [quality_row]},
        calendar_candidates=[],
        earnings_calendar_strategy={"items": []},
        calendar_ranking={"items": []},
        account_context={},
        open_options={},
        lifecycle_checks={},
        policy=load_calendar_evolution_policy(),
        log_print=lambda _msg: None,
    )
    assert engine["new_trade_rows"]
    assert engine["new_trade_rows"][0]["entry_window_status"] == "ENTRY_WINDOW_CLOSED"

    normalized = collect_strategy_results(SimpleNamespace(run_id="run-closeout"), {"earnings_calendar": engine})
    with TemporaryDirectory() as tmp:
        repo = StrategyRowRepository(str(Path(tmp) / "rows.sqlite3"))
        write = repo.write_run("run-closeout", normalized)
        assert write["by_strategy"]["earnings_calendar"] >= 1
        stored = repo.read_latest("earnings_calendar")

    row = stored["rows"][0]
    details = row["details"]["earnings_calendar"]
    assert row["source"] if "source" in row else True
    assert row["row_type"] == "rejected_candidate"
    assert row["friendly_verdict"] == "ENTRY WINDOW CLOSED / DO NOT ENTER"
    assert "minimum" in row["primary_reason"]
    assert details["entry_window_status"] == "ENTRY_WINDOW_CLOSED"
    assert details["available_pre_earnings_expirations"][0]["expiration"] == "2026-07-10"
    assert details["rejected_expirations"][1]["reason"] == "short leg spans or follows earnings"


def test_strategy_rows_endpoint_does_not_load_full_snapshot_when_row_store_has_rows():
    from app.api.strategy_api import get_strategy_rows
    from app.services.strategy_row_repository import StrategyRowRepository

    with TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "rows.sqlite3")
        StrategyRowRepository(db).write_run("run-store", {
            "earnings_calendar": {
                "canonical_opportunities": [
                    {
                        "strategy_id": "earnings_calendar",
                        "ticker": "NFLX",
                        "row_id": "nflx-entry-window-closed",
                        "verdict": "FAIL / ENTRY_WINDOW_CLOSED",
                        "entry_window_status": "ENTRY_WINDOW_CLOSED",
                    }
                ]
            }
        })
        with patch("app.services.strategy_row_repository.config.STRATEGY_ROW_DB_PATH", db), \
             patch("app.services.report_snapshot_service.ReportSnapshotRepository") as snapshot_repo:
            result = get_strategy_rows("earnings_calendar")

    assert result["source"] == "strategy_row_store"
    assert result["latest_run_id"] == "run-store"
    assert result["row_count"] == 1
    snapshot_repo.return_value.latest_success.assert_not_called()
