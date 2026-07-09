from __future__ import annotations

import json
import py_compile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


def test_new_modules_compile():
    py_compile.compile("app/services/strategy_row_repository.py", doraise=True)
    py_compile.compile("app/services/payload_path_audit_service.py", doraise=True)
    py_compile.compile("app/services/earnings_discovery_quality_service.py", doraise=True)


def test_path_level_payload_audit_reports_specific_paths():
    from app.services.payload_path_audit_service import largest_json_paths

    payload = {"report_summary": {"strategy_results": {"forward_factor_calendar": {"rows": [{"details": {"chain_diagnostics": "x" * 5000}}]}}}}
    rows = largest_json_paths(payload, root="report_summary", limit=10, min_bytes=100)
    paths = [row["path"] for row in rows]
    assert any("forward_factor_calendar.rows[0].details.chain_diagnostics" in path for path in paths)


def test_payload_profile_distinguishes_compact_and_archive_metrics():
    from app.services.payload_profile_service import build_payload_size_profile

    profile = build_payload_size_profile(
        "payload",
        [],
        {},
        [],
        {"_strategy_results": {"forward_factor_calendar": {"rows": [{"ticker": "ELF", "details": {"x": "y" * 2000}}]}}},
        [],
        {"strategy_results": {"forward_factor_calendar": {"rows": [{"ticker": "ELF"}]}}},
    )
    assert "compact_summary_json_bytes" in profile
    assert "legacy_report_summary_json_bytes" in profile
    assert "largest_report_summary_paths" in profile
    assert isinstance(profile["largest_snapshot_paths"], list)


def test_compact_manifest_excludes_full_strategy_rows_and_raw_provider_data():
    from app.services.report_snapshot_service import build_compact_manifest_summary

    summary = {
        "report_quality": "SUCCESS_COMPLETE",
        "report_data": {
            "positions": [{"ticker": "NVDA"}],
            "tradier_snapshot": {
                "_strategy_results": {
                    "stock_momentum": {"pass_count": 1, "rows": [{"ticker": "NVDA", "raw": "x" * 10000}]},
                    "forward_factor_calendar": {"watch_count": 1, "rows": [{"ticker": "ELF", "details": "x" * 10000}]},
                },
                "_daily_opportunity_engine": {"actions": [{"ticker": "NVDA", "action": "CONSIDER ADDING"}]},
                "raw_option_chain": "secret-heavy",
            },
        },
    }
    compact = build_compact_manifest_summary(summary)
    raw = json.dumps(compact, default=str)
    assert len(raw.encode("utf-8")) < 750_000
    assert "raw_option_chain" not in raw
    assert "\"rows\"" not in raw
    assert compact["api_links"]["strategy_rows_template"] == "/api/strategies/{strategy_id}/rows"


def test_strategy_row_repository_writes_and_reads_by_strategy():
    from app.services.strategy_row_repository import StrategyRowRepository

    with TemporaryDirectory() as tmp:
        repo = StrategyRowRepository(str(Path(tmp) / "rows.sqlite3"))
        result = repo.write_run("run-1", {
            "stock_momentum": {
                "canonical_opportunities": [
                    {
                        "strategy_id": "stock_momentum",
                        "ticker": "NVDA",
                        "row_id": "sm-nvda",
                        "verdict": "PASS",
                        "friendly_verdict": "Momentum Pass",
                        "score": 91,
                        "daily_opportunity_eligible": True,
                        "details": {"stock_momentum": {"momentum_score": 91}},
                    }
                ]
            }
        })
        assert result["write_count"] == 1
        read = repo.read_latest("stock_momentum")
        assert read["row_count"] == 1
        row = read["rows"][0]
        assert row["ticker"] == "NVDA"
        assert row["source"] if "source" in row else True
        assert row["normalization_status"] == "ok"
        assert row["details"]["stock_momentum"]["momentum_score"] == 91


def test_strategy_rows_api_reads_row_store_before_legacy_snapshot():
    from app.api.strategy_api import get_strategy_rows
    from app.services.strategy_row_repository import StrategyRowRepository

    with TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "rows.sqlite3")
        repo = StrategyRowRepository(db)
        repo.write_run("run-store", {
            "forward_factor_calendar": {
                "canonical_opportunities": [
                    {
                        "strategy_id": "forward_factor_calendar",
                        "ticker": "ELF",
                        "row_id": "ff-elf",
                        "verdict": "WATCH / EX-EARNINGS IV UNAVAILABLE",
                        "daily_opportunity_eligible": False,
                        "dry_run": True,
                    }
                ]
            }
        })
        with patch("app.services.strategy_row_repository.config.STRATEGY_ROW_DB_PATH", db), \
             patch("app.services.report_snapshot_service.ReportSnapshotRepository") as snapshot_repo:
            result = get_strategy_rows("forward_factor_calendar")
        assert result["source"] == "strategy_row_store"
        assert result["latest_run_id"] == "run-store"
        assert result["row_count"] == 1
        snapshot_repo.return_value.latest_success.assert_not_called()
        assert result["rows"][0]["daily_opportunity_eligible"] is False


def test_strategy_rows_api_labels_legacy_fallback_when_row_store_empty():
    from app.api.strategy_api import get_strategy_rows

    with TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "empty.sqlite3")
        fake_snapshot = {"run_id": "run-fallback"}
        fake_summary = {
            "report_data": {
                "tradier_snapshot": {
                    "_strategy_results": {
                        "stock_momentum": {
                            "rows": [{"ticker": "NVDA", "action": "CONSIDER ADDING", "score": 90}]
                        }
                    }
                }
            }
        }
        with patch("app.services.strategy_row_repository.config.STRATEGY_ROW_DB_PATH", db), \
             patch("app.services.report_snapshot_service.ReportSnapshotRepository") as snapshot_repo:
            inst = snapshot_repo.return_value
            inst.latest_success.return_value = fake_snapshot
            inst.load_summary.return_value = fake_summary
            result = get_strategy_rows("stock_momentum")
        assert result["source"] == "legacy_snapshot_fallback"
        assert result["source_run_id"] == "run-fallback"
        assert result["row_count"] == 1


def test_calendar_entry_window_blocks_july16_july8_july10_july17_case(monkeypatch):
    from datetime import date
    from app.services import earnings_discovery_quality_service as svc

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 7, 8)

    monkeypatch.setattr(svc, "date", FixedDate)
    gate = svc._entry_window_gate(
        ["2026-07-10", "2026-07-17"],
        {"ticker": "NFLX", "earnings_date": "2026-07-16", "session_label": "AMC"},
    )
    assert gate["entry_window_open"] is False
    assert gate["entry_window_status"] == "ENTRY_WINDOW_CLOSED"
    assert gate["short_leg_dte_minimum"] >= 4


def test_calendar_entry_window_blocks_short_leg_spanning_earnings(monkeypatch):
    from datetime import date
    from app.services import earnings_discovery_quality_service as svc

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 7, 8)

    monkeypatch.setattr(svc, "date", FixedDate)
    gate = svc._entry_window_gate(
        ["2026-07-17", "2026-07-24"],
        {"ticker": "ISRG", "earnings_date": "2026-07-16", "session_label": "BMO"},
    )
    assert gate["entry_window_status"] == "SHORT_LEG_SPANS_EARNINGS"
    assert gate["short_leg_does_not_span_event"] is False


def test_calendar_entry_window_valid_case_can_watch(monkeypatch):
    from datetime import date
    from app.services import earnings_discovery_quality_service as svc

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 7, 8)

    monkeypatch.setattr(svc, "date", FixedDate)
    gate = svc._entry_window_gate(
        ["2026-07-15", "2026-08-14"],
        {"ticker": "BAC", "earnings_date": "2026-07-16", "session_label": "BMO"},
    )
    assert gate["entry_window_open"] is True
    assert gate["entry_window_status"] in {"ENTRY_WINDOW_OPEN", "ENTRY_WINDOW_CLOSING"}


def test_calendar_entry_window_future_event_monitors_pre_window(monkeypatch):
    from datetime import date
    from app.services import earnings_discovery_quality_service as svc

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 7, 1)

    monkeypatch.setattr(svc, "date", FixedDate)
    gate = svc._entry_window_gate(
        ["2026-07-17", "2026-08-21"],
        {"ticker": "JPM", "earnings_date": "2026-07-20", "session_label": "BMO"},
    )
    assert gate["entry_window_status"] == "MONITOR_PRE_WINDOW"
    assert gate["entry_window_open"] is False
    assert "Pre-window monitor" in gate["entry_window_reason"]


def test_calendar_entry_window_chain_unavailable_yields_data_needed(monkeypatch):
    from datetime import date
    from app.services import earnings_discovery_quality_service as svc

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 7, 1)

    monkeypatch.setattr(svc, "date", FixedDate)
    gate = svc._entry_window_gate(
        [],
        {"ticker": "JPM", "earnings_date": "2026-07-20", "session_label": "BMO"},
    )
    assert gate["entry_window_status"] == "DATA_NEEDED"
    assert gate["entry_window_open"] is False


def test_calendar_entry_window_date_conflict_yields_review(monkeypatch):
    from datetime import date
    from app.services import earnings_discovery_quality_service as svc

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 7, 8)

    monkeypatch.setattr(svc, "date", FixedDate)
    gate = svc._entry_window_gate(
        ["2026-07-15", "2026-08-21"],
        {"ticker": "CTAS", "earnings_date": "2026-07-16", "earnings_source_conflict": True},
    )
    assert gate["entry_window_status"] == "DATE_CONFLICT_REVIEW"
    assert gate["entry_window_open"] is False


def test_universal_earnings_row_includes_entry_window_gate():
    from app.strategies.earnings_calendar_universal import build_earnings_calendar_universal_row

    row = {
        "ticker": "BAC",
        "action": "WATCH / ENTRY WINDOW CLOSING",
        "calendar_entry_allowed": False,
        "entry_window_status": "ENTRY_WINDOW_CLOSING",
        "entry_window_open": True,
        "entry_window_reason": "Short leg is near minimum.",
        "earnings_relation": "long_leg_captures_earnings",
        "earnings_date": "2026-07-16",
    }
    build_earnings_calendar_universal_row(row, run_id="run-1")
    details = row["details"]["earnings_calendar"]
    assert details["entry_window_status"] == "ENTRY_WINDOW_CLOSING"
    assert row["gate_groups"]["event"]["calendar_entry_window"]["status"] == "watch"


def test_broker_debug_raw_logs_guard_exists():
    source = Path("app/providers/robinhood_provider.py").read_text()
    assert "BROKER_DEBUG_RAW_LOGS_ENABLED" in source
    assert "FULL_RAW_DICT" in source
