"""Smoke tests for Patch 27AG — /api/advisor/snapshot + local vault write."""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _fake_snapshot():
    return {"run_id": "run-test-001", "completed_at": "2026-06-17T08:00:00+00:00", "status": "success", "mode": "prod"}


def _fake_summary():
    return {
        "strategy_results": {
            "earnings_calendar": {"pass_count": 2, "watch_count": 1, "fail_count": 0, "skipped_count": 0},
            "skew_momentum_vertical": {"pass_count": 1, "watch_count": 3, "fail_count": 0, "skipped_count": 0},
        },
        "pipeline_status": {"report_quality": "SUCCESS_COMPLETE", "overall_status": "complete"},
        "report_data": {
            "positions": [
                {"ticker": "NVDA", "quantity": 10, "avg_buy_price": 400.0, "current_price": 450.0,
                 "gain_loss_pct": 12.5, "market_value": 4500.0, "asset_type": "stock"},
            ],
            "tradier_snapshot": {
                "_pipeline_status": {"report_quality": "SUCCESS_COMPLETE"},
                "_strategy_results": {
                    "earnings_calendar": {"pass_count": 2, "watch_count": 1, "fail_count": 0, "skipped_count": 0},
                },
                "_daily_opportunity_engine": {
                    "actions": [
                        {"ticker": "AAPL", "action": "WATCH", "type": "calendar", "source": "earnings_calendar",
                         "priority_score": 75, "primary_reason": "IV crush setup."},
                    ],
                },
                "_forward_factor_strategy": {
                    "ff_journal": {"total": 42, "tickers": 8, "runs": 10, "latest_date": "2026-06-16"},
                },
                "_calendar_ranking": {
                    "items": [
                        {"ticker": "AAPL", "rank_score": 82.0, "action": "PASS / BACKTEST",
                         "entry_timing": "IDEAL", "days_until_earnings": 8, "passes_all_criteria": True,
                         "candidate": {"front_expiration": "2026-06-20", "back_expiration": "2026-07-18",
                                       "debit": 1.50, "underlying_price": 185.0}},
                    ],
                },
                "_skew_momentum_vertical_strategy": {
                    "pass_items": [
                        {"ticker": "NVDA", "verdict": "PASS / POSSIBLE ENTRY SETUP", "score": 78.5,
                         "direction": "bullish", "momentum_score": 70.0, "dte": 21,
                         "possible_spread": {"expiration": "2026-07-18"},
                         "conservative_debit": 4.20, "reward_risk": 2.1},
                    ],
                    "watch_items": [],
                },
            },
            "log": [],
        },
    }


# ---------------------------------------------------------------------------
# build_advisor_snapshot_payload
# ---------------------------------------------------------------------------

class TestBuildAdvisorSnapshotPayload:
    def _call(self):
        from app.services.advisor_data_service import build_advisor_snapshot_payload
        summary = _fake_summary()
        return build_advisor_snapshot_payload(_fake_snapshot(), summary, summary["report_data"])

    def test_returns_schema_version(self):
        result = self._call()
        assert result["schema_version"] == 1

    def test_returns_run_metadata(self):
        result = self._call()
        assert result["run_id"] == "run-test-001"
        assert result["run_quality"] == "SUCCESS_COMPLETE"

    def test_provider_calls_triggered_false(self):
        result = self._call()
        assert result["provider_calls_triggered"] is False

    def test_daily_opportunity_present(self):
        result = self._call()
        assert "daily_opportunity" in result
        assert result["daily_opportunity"]["action_count"] == 1
        assert result["daily_opportunity"]["actions"][0]["ticker"] == "AAPL"

    def test_strategy_summary_present(self):
        result = self._call()
        assert "strategy_summary" in result
        assert "earnings_calendar" in result["strategy_summary"]

    def test_positions_summary_present(self):
        result = self._call()
        assert "positions_summary" in result
        assert result["positions_summary"][0]["ticker"] == "NVDA"

    def test_ff_journal_summary_present(self):
        result = self._call()
        j = result["ff_journal_summary"]
        assert j["total_observations"] == 42
        assert j["distinct_tickers"] == 8

    def test_calendar_candidates_present(self):
        result = self._call()
        assert "calendar_candidates" in result
        assert result["calendar_candidates"][0]["ticker"] == "AAPL"
        assert result["calendar_candidates"][0]["rank_score"] == 82.0

    def test_skew_candidates_present(self):
        result = self._call()
        assert "skew_candidates" in result
        assert result["skew_candidates"][0]["ticker"] == "NVDA"
        assert result["skew_candidates"][0]["direction"] == "bullish"

    def test_freshness_indicator_present(self):
        result = self._call()
        assert "freshness" in result
        assert result["freshness"]["status"] in {"fresh", "warn", "stale", "unknown"}
        assert result["freshness"]["completed_at"] == "2026-06-17T08:00:00+00:00"


# ---------------------------------------------------------------------------
# Local vault write
# ---------------------------------------------------------------------------

class TestLocalVaultWrite:
    def _make_fake_run_inputs(self):
        summary = _fake_summary()
        return {
            "run_id": "run-test-vault",
            "run_mode": "prod",
            "positions": summary["report_data"]["positions"],
            "tradier_snapshot": summary["report_data"]["tradier_snapshot"],
            "snapshot_summary": summary,
            "pipeline_status": summary["pipeline_status"],
        }

    def test_writes_json_file_when_path_configured(self):
        from app.services.analysis_service import _write_local_vault
        with tempfile.TemporaryDirectory() as tmpdir:
            vault_path = os.path.join(tmpdir, "vault.json")
            inputs = self._make_fake_run_inputs()
            with patch("app.config.LOCAL_VAULT_OUTPUT_PATH", vault_path):
                _write_local_vault(
                    inputs["run_id"], inputs["run_mode"], inputs["positions"],
                    inputs["tradier_snapshot"], inputs["snapshot_summary"],
                    inputs["pipeline_status"], lambda msg: None,
                )
            assert os.path.isfile(vault_path)
            with open(vault_path, encoding="utf-8") as fh:
                data = json.load(fh)
            assert data["vault_schema_version"] == 1
            assert data["run_id"] == "run-test-vault"
            assert "snapshot" in data

    def test_does_nothing_when_path_not_configured(self):
        from app.services.analysis_service import _write_local_vault
        inputs = self._make_fake_run_inputs()
        log_lines = []
        with patch("app.config.LOCAL_VAULT_OUTPUT_PATH", None):
            _write_local_vault(
                inputs["run_id"], inputs["run_mode"], inputs["positions"],
                inputs["tradier_snapshot"], inputs["snapshot_summary"],
                inputs["pipeline_status"], log_lines.append,
            )
        assert log_lines == []

    def test_creates_parent_directories(self):
        from app.services.analysis_service import _write_local_vault
        with tempfile.TemporaryDirectory() as tmpdir:
            vault_path = os.path.join(tmpdir, "subdir", "nested", "vault.json")
            inputs = self._make_fake_run_inputs()
            with patch("app.config.LOCAL_VAULT_OUTPUT_PATH", vault_path):
                _write_local_vault(
                    inputs["run_id"], inputs["run_mode"], inputs["positions"],
                    inputs["tradier_snapshot"], inputs["snapshot_summary"],
                    inputs["pipeline_status"], lambda msg: None,
                )
            assert os.path.isfile(vault_path)

    def test_non_fatal_on_write_error(self):
        from app.services.analysis_service import _write_local_vault
        inputs = self._make_fake_run_inputs()
        log_lines = []
        with patch("app.config.LOCAL_VAULT_OUTPUT_PATH", "/nonexistent_root_path_xyz/vault.json"):
            # Should not raise — errors are caught and logged
            _write_local_vault(
                inputs["run_id"], inputs["run_mode"], inputs["positions"],
                inputs["tradier_snapshot"], inputs["snapshot_summary"],
                inputs["pipeline_status"], log_lines.append,
            )
        assert any("LocalVault" in msg for msg in log_lines)

    def test_vault_snapshot_contains_expected_keys(self):
        from app.services.analysis_service import _write_local_vault
        with tempfile.TemporaryDirectory() as tmpdir:
            vault_path = os.path.join(tmpdir, "vault.json")
            inputs = self._make_fake_run_inputs()
            with patch("app.config.LOCAL_VAULT_OUTPUT_PATH", vault_path):
                _write_local_vault(
                    inputs["run_id"], inputs["run_mode"], inputs["positions"],
                    inputs["tradier_snapshot"], inputs["snapshot_summary"],
                    inputs["pipeline_status"], lambda msg: None,
                )
            with open(vault_path, encoding="utf-8") as fh:
                data = json.load(fh)
            snap = data["snapshot"]
            for key in ("schema_version", "run_id", "daily_opportunity", "strategy_summary",
                        "positions_summary", "ff_journal_summary", "calendar_candidates",
                        "skew_candidates", "freshness"):
                assert key in snap, f"Missing key: {key}"
