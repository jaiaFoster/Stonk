from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


def _row_store_fixture(db_path: str):
    from app.services.strategy_row_repository import StrategyRowRepository

    repo = StrategyRowRepository(db_path)
    repo.write_run("run-30gh", {
        "earnings_calendar": {
            "canonical_opportunities": [
                {
                    "strategy_id": "earnings_calendar",
                    "ticker": "NFLX",
                    "row_id": "nflx-closed",
                    "verdict": "FAIL / ENTRY_WINDOW_CLOSED",
                    "action": "FAIL / ENTRY_WINDOW_CLOSED",
                    "row_type": "rejected_candidate",
                    "score": 10,
                    "entry_window_status": "ENTRY_WINDOW_CLOSED",
                    "entry_window_reason": "Only pre-earnings short leg 2026-07-10 has 1 DTE; minimum is 7.",
                    "short_leg_dte_minimum": 7,
                    "available_expirations": [
                        {"expiration": "2026-07-10", "dte": 1, "position": "pre_earnings"},
                        {"expiration": "2026-07-17", "dte": 8, "position": "spans_or_after_earnings"},
                    ],
                    "available_pre_earnings_expirations": [{"expiration": "2026-07-10", "dte": 1}],
                    "rejected_expirations": [
                        {"expiration": "2026-07-10", "dte": 1, "reason": "below minimum short-leg DTE 7"},
                        {"expiration": "2026-07-17", "dte": 8, "reason": "short leg spans or follows earnings"},
                    ],
                    "estimated_entry_date": "2026-07-04",
                    "days_until_entry_window": 0,
                    "blocker_code": "ENTRY_WINDOW_CLOSED",
                    "blocker_detail": "No valid pre-earnings short expiration with sufficient DTE/time value.",
                },
            ],
            "active_rows": [
                {
                    "type": "open_calendar",
                    "ticker": "SBUX",
                    "row_id": "sbux-call-calendar",
                    "verdict": "HOLD / MONITOR",
                    "score": 95,
                    "next_action": "Hold and recheck lifecycle.",
                    "structure": {
                        "structure_type": "calendar",
                        "front_expiration": "2026-08-21",
                        "back_expiration": "2026-09-18",
                        "strike": 110,
                        "option_type": "call",
                        "legs": [
                            {"symbol": "SBUX260821C00110000", "side": "short"},
                            {"symbol": "SBUX260918C00110000", "side": "long"},
                        ],
                    },
                    "value": {"current_debit": 1.75, "current_mid_debit": 1.66},
                    "reasons": ["Open calendar lifecycle row."],
                },
                {
                    "type": "open_calendar",
                    "ticker": "SBUX",
                    "row_id": "sbux-put-calendar",
                    "verdict": "HOLD / MONITOR",
                    "score": 94,
                    "next_action": "Hold and recheck lifecycle.",
                    "structure": {
                        "structure_type": "calendar",
                        "front_expiration": "2026-08-21",
                        "back_expiration": "2026-09-18",
                        "strike": 100,
                        "option_type": "put",
                        "legs": [
                            {"symbol": "SBUX260821P00100000", "side": "short"},
                            {"symbol": "SBUX260918P00100000", "side": "long"},
                        ],
                    },
                    "value": {"current_debit": 1.38, "current_mid_debit": 1.30},
                    "reasons": ["Open calendar lifecycle row."],
                },
            ],
        },
        "stock_momentum": {
            "canonical_opportunities": [
                {
                    "strategy_id": "stock_momentum",
                    "ticker": "AMZN",
                    "row_id": "amzn-stock",
                    "verdict": "CONSIDER ADDING",
                    "action": "CONSIDER ADDING",
                    "friendly_verdict": "Momentum Pass",
                    "score": 80,
                    "daily_opportunity_eligible": True,
                    "primary_reason": "Momentum pass.",
                },
                {
                    "strategy_id": "stock_momentum",
                    "ticker": "BYND",
                    "row_id": "bynd-stock",
                    "verdict": "AVOID / WEAK TREND",
                    "action": "AVOID / WEAK TREND",
                    "score": 5,
                    "daily_opportunity_eligible": False,
                },
            ]
        },
        "skew_momentum_vertical": {
            "canonical_opportunities": [
                {
                    "strategy_id": "skew_momentum_vertical",
                    "ticker": "MSFT",
                    "row_id": "msft-skew",
                    "verdict": "PASS / SKEW VERTICAL",
                    "score": 70,
                    "daily_opportunity_eligible": True,
                }
            ]
        },
        "forward_factor_calendar": {
            "canonical_opportunities": [
                {
                    "strategy_id": "forward_factor_calendar",
                    "ticker": "ELF",
                    "row_id": "elf-ff",
                    "verdict": "DIAGNOSTIC POSITIVE FF SIGNAL / REVIEW ONLY",
                    "score": 88,
                    "dry_run": True,
                    "daily_opportunity_eligible": True,
                }
            ]
        },
    })
    return repo


def test_daily_opportunity_reads_strategy_row_store_and_excludes_ff_dry_run():
    with TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "rows.sqlite3")
        _row_store_fixture(db)
        with patch("app.services.strategy_row_repository.config.STRATEGY_ROW_DB_PATH", db), \
             patch("app.services.report_snapshot_service.ReportSnapshotRepository") as snapshot_repo:
            from app.api.daily_opportunity_api import build_daily_opportunity_response

            response = build_daily_opportunity_response(limit=10)

    assert response["source"] == "strategy_row_store"
    assert response["fallback_used"] is False
    assert response["latest_run_id"] == "run-30gh"
    assert response["row_count_considered"] >= 5
    assert response["strategy_counts"]["forward_factor_calendar"]["rows_seen"] == 1
    assert response["dry_run_exclusions"]["forward_factor_calendar"]["excluded_reason"] == "dry_run"
    assert not any(action["source_strategy_id"] == "forward_factor_calendar" for action in response["actions"])
    assert response["actions"][0]["type"] == "active_calendar"
    assert any(action["ticker"] == "AMZN" for action in response["actions"])
    snapshot_repo.assert_not_called()


def test_daily_opportunity_legacy_fallback_is_labeled_when_row_store_empty():
    with TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "rows.sqlite3")
        with patch("app.services.strategy_row_repository.config.STRATEGY_ROW_DB_PATH", db), \
             patch("app.services.report_snapshot_service.ReportSnapshotRepository") as snapshot_repo:
            snapshot_repo.return_value.latest_success.return_value = {
                "run_id": "legacy-run",
                "completed_at": "2026-07-09T12:00:00+00:00",
            }
            snapshot_repo.return_value.load_summary.return_value = {
                "report_data": {
                    "tradier_snapshot": {
                        "_daily_opportunity_engine": {
                            "enabled": True,
                            "has_data": True,
                            "actions": [{"ticker": "AMZN", "action": "CONSIDER ADDING", "source": "stock_momentum"}],
                        }
                    }
                }
            }
            from app.api.daily_opportunity_api import build_daily_opportunity_response

            response = build_daily_opportunity_response(limit=10)

    assert response["source"] == "legacy_snapshot_fallback"
    assert response["fallback_used"] is True
    assert response["source_run_id"] == "legacy-run"


def test_open_positions_reads_lifecycle_rows_from_strategy_row_store():
    with TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "rows.sqlite3")
        _row_store_fixture(db)
        with patch("app.services.strategy_row_repository.config.STRATEGY_ROW_DB_PATH", db), \
             patch("app.services.report_snapshot_service.ReportSnapshotRepository") as snapshot_repo:
            from app.api.open_positions_api import build_open_positions_response

            response = build_open_positions_response()

    assert response["source"] == "strategy_row_store"
    assert response["fallback_used"] is False
    assert response["latest_run_id"] == "run-30gh"
    assert response["active_calendar_count"] == 2
    assert response["has_open_calendars"] is True
    assert len(response["calendar_structures"]) == 2
    assert response["open_option_leg_count"] == 4
    assert {s["option_type"] for s in response["calendar_structures"]} == {"call", "put"}
    snapshot_repo.assert_not_called()


def test_open_positions_warns_on_duplicate_lifecycle_structures():
    with TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "rows.sqlite3")
        from app.services.strategy_row_repository import StrategyRowRepository

        StrategyRowRepository(db).write_run("run-dupes", {
            "earnings_calendar": {
                "active_rows": [
                    {
                        "type": "open_calendar",
                        "ticker": "SBUX",
                        "row_id": "sbux-a",
                        "verdict": "HOLD / MONITOR",
                        "score": 50,
                        "structure": {
                            "structure_type": "calendar",
                            "front_expiration": "2026-08-21",
                            "back_expiration": "2026-09-18",
                            "strike": 110,
                            "option_type": "call",
                        },
                    },
                    {
                        "type": "open_calendar",
                        "ticker": "SBUX",
                        "row_id": "sbux-b",
                        "verdict": "HOLD / MONITOR",
                        "score": 49,
                        "structure": {
                            "structure_type": "calendar",
                            "front_expiration": "2026-08-21",
                            "back_expiration": "2026-09-18",
                            "strike": 110,
                            "option_type": "call",
                        },
                    },
                ]
            }
        })
        with patch("app.services.strategy_row_repository.config.STRATEGY_ROW_DB_PATH", db):
            from app.api.open_positions_api import build_open_positions_response

            response = build_open_positions_response()

    assert response["source"] == "strategy_row_store"
    assert response["dedup_summary"]["duplicate_group_count"] == 1
    assert response["dedup_summary"]["duplicate_warning"] is True
    assert response["warnings"]


def test_earnings_calendar_row_reason_fields_survive_store_api_shape():
    with TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "rows.sqlite3")
        _row_store_fixture(db)
        from app.services.strategy_row_repository import StrategyRowRepository

        stored = StrategyRowRepository(db).read_latest("earnings_calendar", limit=10)

    closed = next(row for row in stored["rows"] if row["ticker"] == "NFLX")
    details = closed["details"]["earnings_calendar"]
    assert closed["row_type"] == "rejected_candidate"
    assert closed["friendly_verdict"] == "ENTRY WINDOW CLOSED / DO NOT ENTER"
    assert details["blocker_code"] == "ENTRY_WINDOW_CLOSED"
    assert details["available_expirations"][1]["position"] == "spans_or_after_earnings"
    assert details["rejected_expirations"][0]["reason"] == "below minimum short-leg DTE 7"
    assert details["estimated_entry_date"] == "2026-07-04"
