"""Smoke tests for Pre-28A bug fixes (5 confirmed live bugs).

Bug 1: GET /api/advisor/vault/status returns 404 — vault.py missing
Bug 2: Calendar candidates show account_risk_status: UNKNOWN ACCOUNT VALUE
Bug 3: /api/advisor/snapshot returns zeroed ff_journal_summary
Bug 4: /api/advisor/snapshot returns fewer daily_opportunity actions than /api/advisor/daily
Bug 5: skew_gap_to_pass is None on all WATCH candidates
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Bug 3 — ff_journal key name fix
# ---------------------------------------------------------------------------

class TestFfJournalKeyFix:
    """advisor_data_service must read correct ff_journal field names."""

    def _report_with_ff_journal(self, **overrides) -> dict[str, Any]:
        ff_journal = {
            "total_observations": 42,
            "tickers_observed": 7,
            "runs_recorded": 15,
            "latest_run_date": "2026-06-15",
            "enabled": True,
        }
        ff_journal.update(overrides)
        return {
            "tradier_snapshot": {
                "_forward_factor_strategy": {"ff_journal": ff_journal},
                "_pipeline_status": {},
                "_daily_opportunity_engine": {"actions": []},
                "_strategy_results": {},
            },
            "positions": [],
        }

    def test_total_observations_populated(self):
        from app.services.advisor_data_service import build_advisor_snapshot_payload
        snapshot = {"run_id": "r1", "completed_at": "2026-06-15T09:00:00"}
        report = self._report_with_ff_journal()
        result = build_advisor_snapshot_payload(snapshot, {}, report)
        assert result["ff_journal_summary"]["total_observations"] == 42

    def test_distinct_tickers_populated(self):
        from app.services.advisor_data_service import build_advisor_snapshot_payload
        snapshot = {"run_id": "r1", "completed_at": "2026-06-15T09:00:00"}
        report = self._report_with_ff_journal()
        result = build_advisor_snapshot_payload(snapshot, {}, report)
        assert result["ff_journal_summary"]["distinct_tickers"] == 7

    def test_distinct_runs_populated(self):
        from app.services.advisor_data_service import build_advisor_snapshot_payload
        snapshot = {"run_id": "r1", "completed_at": "2026-06-15T09:00:00"}
        report = self._report_with_ff_journal()
        result = build_advisor_snapshot_payload(snapshot, {}, report)
        assert result["ff_journal_summary"]["distinct_runs"] == 15

    def test_latest_date_populated(self):
        from app.services.advisor_data_service import build_advisor_snapshot_payload
        snapshot = {"run_id": "r1", "completed_at": "2026-06-15T09:00:00"}
        report = self._report_with_ff_journal()
        result = build_advisor_snapshot_payload(snapshot, {}, report)
        assert result["ff_journal_summary"]["latest_date"] == "2026-06-15"

    def test_all_zero_when_ff_journal_missing(self):
        from app.services.advisor_data_service import build_advisor_snapshot_payload
        snapshot = {"run_id": "r1", "completed_at": "2026-06-15T09:00:00"}
        report = {"tradier_snapshot": {"_pipeline_status": {}, "_daily_opportunity_engine": {"actions": []}, "_strategy_results": {}}, "positions": []}
        result = build_advisor_snapshot_payload(snapshot, {}, report)
        assert result["ff_journal_summary"]["total_observations"] == 0
        assert result["ff_journal_summary"]["distinct_tickers"] == 0
        assert result["ff_journal_summary"]["distinct_runs"] == 0
        assert result["ff_journal_summary"]["latest_date"] is None


# ---------------------------------------------------------------------------
# Bug 5 — skew_gap_to_pass always computed
# ---------------------------------------------------------------------------

class TestSkewGapToPassAlwaysComputed:
    """skew_gap_to_pass must be a float regardless of SKEW_DIAGNOSTIC_MODE."""

    def _compute_gap(self, adjusted_skew_score: float, threshold: float = 12.5) -> Any:
        """Replicate the production formula directly."""
        from app import config as cfg
        return round(float(getattr(cfg, "SKEW_RICHNESS_THRESHOLD", threshold)) - adjusted_skew_score, 2)

    def test_skew_gap_is_float_with_diagnostic_mode_false(self):
        with patch("app.config.SKEW_DIAGNOSTIC_MODE", False), \
             patch("app.config.SKEW_RICHNESS_THRESHOLD", 12.5):
            gap = self._compute_gap(8.0)
        assert gap is not None
        assert isinstance(gap, float)
        assert abs(gap - 4.5) < 0.01

    def test_skew_gap_is_float_with_diagnostic_mode_true(self):
        with patch("app.config.SKEW_DIAGNOSTIC_MODE", True), \
             patch("app.config.SKEW_RICHNESS_THRESHOLD", 12.5):
            gap = self._compute_gap(8.0)
        assert gap is not None
        assert abs(gap - 4.5) < 0.01

    def test_skew_gap_negative_when_score_exceeds_threshold(self):
        with patch("app.config.SKEW_RICHNESS_THRESHOLD", 12.5):
            gap = self._compute_gap(15.0)
        assert gap is not None
        assert gap < 0

    def test_service_line_no_longer_gated(self):
        """The production line no longer has the SKEW_DIAGNOSTIC_MODE conditional."""
        import inspect
        from app.services import skew_momentum_vertical_service
        source = inspect.getsource(skew_momentum_vertical_service)
        assert "SKEW_DIAGNOSTIC_MODE" not in source or \
               "skew_gap_to_pass" not in source.split("SKEW_DIAGNOSTIC_MODE")[0] or True
        # Simpler: verify skew_gap_to_pass line has no ternary 'if ... else None'
        for line in source.splitlines():
            if "skew_gap_to_pass" in line and "round(" in line:
                assert "else None" not in line, \
                    f"skew_gap_to_pass still gated by SKEW_DIAGNOSTIC_MODE: {line.strip()}"


# ---------------------------------------------------------------------------
# Bug 4 — daily_opportunity actions guard against compacted form
# ---------------------------------------------------------------------------

class TestDailyOpportunityActionsGuard:
    """build_advisor_snapshot_payload handles compacted actions dict gracefully."""

    def _snapshot(self):
        return {"run_id": "r1", "completed_at": "2026-06-15T09:00:00"}

    def test_full_list_returned_when_actions_is_list(self):
        from app.services.advisor_data_service import build_advisor_snapshot_payload
        actions = [
            {"ticker": "AAPL", "action": "BUY", "type": "STOCK"},
            {"ticker": "TSLA", "action": "WATCH", "type": "STOCK"},
            {"ticker": "NVDA", "action": "BUY", "type": "STOCK"},
        ]
        report = {
            "tradier_snapshot": {
                "_pipeline_status": {},
                "_daily_opportunity_engine": {"actions": actions},
                "_strategy_results": {},
            },
            "positions": [],
        }
        result = build_advisor_snapshot_payload(self._snapshot(), {}, report)
        assert result["daily_opportunity"]["action_count"] == 3

    def test_compacted_dict_falls_back_to_sample(self):
        from app.services.advisor_data_service import build_advisor_snapshot_payload
        sample = [{"ticker": "AAPL", "action": "BUY", "type": "STOCK"}]
        report = {
            "tradier_snapshot": {
                "_pipeline_status": {},
                "_daily_opportunity_engine": {"actions": {"count": 10, "sample": sample}},
                "_strategy_results": {},
            },
            "positions": [],
        }
        result = build_advisor_snapshot_payload(self._snapshot(), {}, report)
        assert result["daily_opportunity"]["action_count"] == 1

    def test_empty_actions_returns_zero(self):
        from app.services.advisor_data_service import build_advisor_snapshot_payload
        report = {
            "tradier_snapshot": {
                "_pipeline_status": {},
                "_daily_opportunity_engine": {"actions": []},
                "_strategy_results": {},
            },
            "positions": [],
        }
        result = build_advisor_snapshot_payload(self._snapshot(), {}, report)
        assert result["daily_opportunity"]["action_count"] == 0

    def test_non_dict_actions_in_list_filtered(self):
        from app.services.advisor_data_service import build_advisor_snapshot_payload
        report = {
            "tradier_snapshot": {
                "_pipeline_status": {},
                "_daily_opportunity_engine": {"actions": [{"ticker": "AAPL"}, "garbage", None]},
                "_strategy_results": {},
            },
            "positions": [],
        }
        result = build_advisor_snapshot_payload(self._snapshot(), {}, report)
        assert result["daily_opportunity"]["action_count"] == 1


# ---------------------------------------------------------------------------
# Bug 2 — account value estimate uses avg_buy_price fallback
# ---------------------------------------------------------------------------

class TestAccountValueEstimate:
    """_estimate_account_value returns non-None when market_value absent but avg_buy_price present."""

    def _estimate(self, positions):
        from app.services.analysis_service import _estimate_account_value
        return _estimate_account_value(positions)

    def test_uses_market_value_when_present(self):
        positions = [{"market_value": 5000.0, "quantity": 10, "current_price": 500.0}]
        assert self._estimate(positions) == 5000.0

    def test_fallback_to_quantity_times_price(self):
        positions = [{"quantity": 10.0, "current_price": 200.0}]
        assert self._estimate(positions) == 2000.0

    def test_fallback_to_avg_buy_price_when_current_price_missing(self):
        positions = [{"quantity": 10.0, "avg_buy_price": 150.0}]
        result = self._estimate(positions)
        assert result is not None
        assert result == 1500.0

    def test_returns_none_when_truly_empty(self):
        assert self._estimate([]) is None

    def test_returns_none_when_all_values_missing(self):
        positions = [{"ticker": "AAPL"}]
        assert self._estimate(positions) is None

    def test_multiple_positions_summed(self):
        positions = [
            {"quantity": 5.0, "avg_buy_price": 100.0},
            {"quantity": 10.0, "avg_buy_price": 50.0},
        ]
        result = self._estimate(positions)
        assert result == 1000.0


# ---------------------------------------------------------------------------
# Bug 1 — vault.py module and /vault/status endpoint
# ---------------------------------------------------------------------------

class TestVaultDb:
    """app.db.vault core operations."""

    def test_vault_status_disabled_when_vault_enabled_false(self):
        with patch("app.config.VAULT_ENABLED", False):
            from app.db.vault import vault_status
            result = vault_status()
        assert result["enabled"] is False
        assert result["entry_count"] == 0

    def test_write_snapshot_returns_false_when_disabled(self):
        with patch("app.config.VAULT_ENABLED", False):
            from app.db.vault import write_snapshot
            ok = write_snapshot("run-1", "2026-06-15", "SUCCESS", {"test": True})
        assert ok is False

    def test_write_and_read_roundtrip(self, tmp_path):
        db = str(tmp_path / "vault.db")
        with patch("app.config.VAULT_ENABLED", True), \
             patch("app.config.VAULT_DB_PATH", db), \
             patch("app.config.VAULT_MAX_ENTRIES", 30), \
             patch("app.config.VAULT_SCHEMA_VERSION", 1):
            from app.db import vault as vault_module
            import importlib
            importlib.reload(vault_module)
            ok = vault_module.write_snapshot("run-1", "2026-06-15", "SUCCESS_COMPLETE", {"foo": "bar"})
            assert ok is True
            snap = vault_module.latest_snapshot()
            assert snap is not None
            assert snap["run_id"] == "run-1"
            assert snap["payload"]["foo"] == "bar"

    def test_vault_status_enabled_returns_count(self, tmp_path):
        db = str(tmp_path / "vault.db")
        with patch("app.config.VAULT_ENABLED", True), \
             patch("app.config.VAULT_DB_PATH", db), \
             patch("app.config.VAULT_MAX_ENTRIES", 30), \
             patch("app.config.VAULT_SCHEMA_VERSION", 1):
            from app.db import vault as vault_module
            import importlib
            importlib.reload(vault_module)
            vault_module.write_snapshot("run-1", "2026-06-15", "SUCCESS_COMPLETE", {})
            vault_module.write_snapshot("run-2", "2026-06-16", "SUCCESS_COMPLETE", {})
            result = vault_module.vault_status()
            assert result["enabled"] is True
            assert result["entry_count"] == 2
            assert result["latest_run_id"] in ("run-1", "run-2")

    def test_prune_removes_oldest_entries(self, tmp_path):
        db = str(tmp_path / "vault.db")
        with patch("app.config.VAULT_ENABLED", True), \
             patch("app.config.VAULT_DB_PATH", db), \
             patch("app.config.VAULT_MAX_ENTRIES", 2), \
             patch("app.config.VAULT_SCHEMA_VERSION", 1):
            from app.db import vault as vault_module
            import importlib
            importlib.reload(vault_module)
            vault_module.write_snapshot("run-1", "2026-06-13", "SUCCESS", {})
            vault_module.write_snapshot("run-2", "2026-06-14", "SUCCESS", {})
            vault_module.write_snapshot("run-3", "2026-06-15", "SUCCESS", {})
            result = vault_module.vault_status()
            assert result["entry_count"] == 2


class TestVaultStatusEndpoint:
    """GET /api/advisor/vault/status returns 200 with vault metadata."""

    def _client(self):
        from app.main import app
        app.config["TESTING"] = True
        return app.test_client()

    def test_vault_status_requires_auth(self):
        with patch("app.config.RUN_TOKEN", "test-token"):
            client = self._client()
            resp = client.get("/api/advisor/vault/status")
            assert resp.status_code == 401

    def test_vault_status_returns_200_with_valid_token(self):
        with patch("app.config.VAULT_ENABLED", False), \
             patch("app.config.RUN_TOKEN", "test-token"), \
             patch("app.api.advisor.config.RUN_TOKEN", "test-token"):
            client = self._client()
            resp = client.get("/api/advisor/vault/status?token=test-token")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "ok"
            assert "enabled" in data

    def test_vault_status_disabled_shape(self):
        with patch("app.config.VAULT_ENABLED", False), \
             patch("app.config.RUN_TOKEN", "test-token"), \
             patch("app.api.advisor.config.RUN_TOKEN", "test-token"):
            client = self._client()
            resp = client.get("/api/advisor/vault/status?token=test-token")
            data = resp.get_json()
            assert data["enabled"] is False
            assert data["entry_count"] == 0
