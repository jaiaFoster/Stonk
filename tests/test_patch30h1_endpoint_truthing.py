from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


def _write_rows(db_path: str):
    from app.services.strategy_row_repository import StrategyRowRepository

    StrategyRowRepository(db_path).write_run("run-30h1", {
        "earnings_calendar": {
            "active_rows": [
                {
                    "type": "open_calendar",
                    "ticker": "SBUX",
                    "row_id": "sbux-call",
                    "verdict": "HOLD / MONITOR",
                    "score": 65,
                    "details": {
                        "earnings_calendar": {
                            "structure": "110.0 CALL | short 2026-08-21 / long 2026-09-18",
                            "value": "current debit 0.82 | entry debit est. 0.84",
                            "next_action": "Monitor daily.",
                        }
                    },
                },
                {
                    "type": "open_calendar",
                    "ticker": "SBUX",
                    "row_id": "sbux-put",
                    "verdict": "HOLD / MONITOR",
                    "score": 64,
                    "details": {
                        "earnings_calendar": {
                            "structure": "100.0 PUT | short 2026-08-21 / long 2026-09-18",
                            "value": "current debit 0.61 | entry debit est. 0.68",
                            "next_action": "Monitor daily.",
                        }
                    },
                },
            ]
        },
        "stock_momentum": {
            "canonical_opportunities": [
                {
                    "strategy_id": "stock_momentum",
                    "ticker": "GE",
                    "row_id": "ge-watch",
                    "verdict": "WATCH / CONFIRM TREND",
                    "score": 94,
                    "daily_opportunity_eligible": False,
                },
                {
                    "strategy_id": "stock_momentum",
                    "ticker": "SOXL",
                    "row_id": "soxl-tactical",
                    "verdict": "TACTICAL ONLY / DO NOT CHASE",
                    "score": 55,
                    "daily_opportunity_eligible": False,
                },
                {
                    "strategy_id": "stock_momentum",
                    "ticker": "BYND",
                    "row_id": "bynd-fail",
                    "verdict": "AVOID / WEAK TREND",
                    "score": 5,
                    "daily_opportunity_eligible": False,
                },
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


def test_daily_opportunity_restores_stock_watch_parity_from_row_store():
    with TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "rows.sqlite3")
        _write_rows(db)
        with patch("app.services.strategy_row_repository.config.STRATEGY_ROW_DB_PATH", db), \
             patch("app.services.report_snapshot_service.ReportSnapshotRepository") as snapshot_repo:
            from app.api.daily_opportunity_api import build_daily_opportunity_response

            response = build_daily_opportunity_response(limit=12)

    assert response["source"] == "strategy_row_store"
    assert response["fallback_used"] is False
    assert response["provider_calls_triggered"] is False
    assert response["strategy_counts"]["stock_momentum"]["eligible"] == 2
    assert response["strategy_counts"]["forward_factor_calendar"]["eligible"] == 0
    assert response["dry_run_exclusions"]["forward_factor_calendar"]["excluded_reason"] == "dry_run"
    action_types = {action["ticker"]: action["type"] for action in response["actions"]}
    assert action_types["SBUX"] == "calendar_position_action"
    assert action_types["GE"] == "stock_watch"
    assert action_types["SOXL"] == "tactical_stock_watch"
    assert "BYND" not in action_types
    snapshot_repo.assert_not_called()


def test_open_positions_uses_row_store_with_lifecycle_string_structures():
    with TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "rows.sqlite3")
        _write_rows(db)
        with patch("app.services.strategy_row_repository.config.STRATEGY_ROW_DB_PATH", db), \
             patch("app.services.report_snapshot_service.ReportSnapshotRepository") as snapshot_repo:
            from app.api.open_positions_api import build_open_positions_response

            response = build_open_positions_response()

    assert response["source"] == "strategy_row_store"
    assert response["fallback_used"] is False
    assert response["provider_calls_triggered"] is False
    assert response["active_calendar_count"] == 2
    assert response["has_open_calendars"] is True
    assert len(response["calendar_structures"]) == 2
    structures = {item["option_type"]: item for item in response["calendar_structures"]}
    assert structures["call"]["strike"] == 110.0
    assert structures["call"]["front_expiration"] == "2026-08-21"
    assert structures["call"]["back_expiration"] == "2026-09-18"
    assert structures["call"]["current_debit"] == 0.82
    assert structures["put"]["strike"] == 100.0
    assert structures["put"]["current_debit"] == 0.61
    snapshot_repo.assert_not_called()


def test_open_positions_no_legacy_fallback_when_row_store_empty():
    from app.api.open_positions_api import build_open_positions_response

    fake_snapshot = {"run_id": "legacy-run", "completed_at": "2026-07-09T18:00:00+00:00"}
    fake_summary = {
        "report_data": {
            "tradier_snapshot": {
                "_open_options_positions": {
                    "has_open_calendars": False,
                    "active_calendar_count": 0,
                    "options_positions": [
                        {"ticker": "SBUX", "option_type": "call", "expiration": "2026-08-21", "qty": 1},
                        {"ticker": "SBUX", "option_type": "call", "expiration": "2026-08-21", "qty": 1},
                    ],
                },
                "_calendar_lifecycle_checks": {
                    "checks": [
                        {
                            "row_id": "legacy-sbux",
                            "ticker": "SBUX",
                            "front_expiration": "2026-08-21",
                            "back_expiration": "2026-09-18",
                            "strike": 110.0,
                            "option_type": "call",
                            "current_debit": 0.82,
                            "action": "HOLD / MONITOR",
                            "raw": {"account_number": "912620267", "account": "https://api.robinhood.com/accounts/912620267/"},
                        }
                    ],
                },
            }
        }
    }
    with TemporaryDirectory() as tmp, \
         patch("app.services.strategy_row_repository.config.STRATEGY_ROW_DB_PATH", str(Path(tmp) / "rows.sqlite3")), \
         patch("app.services.report_snapshot_service.ReportSnapshotRepository") as snapshot_repo:
        snapshot_repo.return_value.latest_success.return_value = fake_snapshot
        snapshot_repo.return_value.load_summary.return_value = fake_summary
        response = build_open_positions_response()

    assert response["source"] == "empty"
    assert response["fallback_used"] is False
    assert response["active_calendar_count"] == 0
    assert response["has_open_calendars"] is False
    snapshot_repo.assert_not_called()


def test_robinhood_log_account_mask_helper():
    from app.providers.robinhood_provider import _mask_account_id_for_log

    assert _mask_account_id_for_log("912620267") == "***0267"
    assert _mask_account_id_for_log("116003410788") == "***0788"
    assert _mask_account_id_for_log(None) == "default"
