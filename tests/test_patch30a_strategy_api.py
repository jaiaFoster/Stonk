"""
ASA Patch 30A — Strategy API Tests

Covers:
  - app/api/strategy_api.py (list_strategies, get_strategy, get_strategy_schema,
    get_test_rows, validate_draft)
  - /api/strategies/* Flask endpoints in app/main.py (token enforcement, routing)
"""
from __future__ import annotations

import py_compile
from unittest.mock import patch

from app import config as cfg


class TestCompile:
    def test_strategy_api_compiles(self):
        py_compile.compile("app/api/strategy_api.py", doraise=True)

    def test_main_compiles(self):
        py_compile.compile("app/main.py", doraise=True)


# ─── list_strategies ──────────────────────────────────────────────────────────

class TestListStrategies:
    def test_returns_dict(self):
        from app.api.strategy_api import list_strategies
        result = list_strategies()
        assert isinstance(result, dict)

    def test_has_strategies_list(self):
        from app.api.strategy_api import list_strategies
        result = list_strategies()
        assert isinstance(result["strategies"], list)

    def test_has_five_strategies(self):
        from app.api.strategy_api import list_strategies
        result = list_strategies()
        assert result["count"] == 5

    def test_read_only_flag(self):
        from app.api.strategy_api import list_strategies
        result = list_strategies()
        assert result.get("provider_calls_triggered") is False
        assert result.get("read_only") is True

    def test_has_schema_version(self):
        from app.api.strategy_api import list_strategies
        result = list_strategies()
        assert result.get("schema_version")


# ─── get_strategy ──────────────────────────────────────────────────────────────

class TestGetStrategy:
    def test_known_strategy(self):
        from app.api.strategy_api import get_strategy
        spec = get_strategy("earnings_calendar")
        assert spec is not None
        assert spec["strategy_id"] == "earnings_calendar"

    def test_test_clone(self):
        from app.api.strategy_api import get_strategy
        spec = get_strategy("stock_momentum_unified_test")
        assert spec is not None
        assert spec["strategy_id"] == "stock_momentum_unified_test"

    def test_unknown_returns_none(self):
        from app.api.strategy_api import get_strategy
        assert get_strategy("does_not_exist") is None

    def test_empty_string_returns_none(self):
        from app.api.strategy_api import get_strategy
        assert get_strategy("") is None


# ─── get_strategy_schema ──────────────────────────────────────────────────────

class TestGetStrategySchema:
    def test_returns_dict(self):
        from app.api.strategy_api import get_strategy_schema
        result = get_strategy_schema()
        assert isinstance(result, dict)

    def test_has_required_core_fields(self):
        from app.api.strategy_api import get_strategy_schema
        result = get_strategy_schema()
        assert isinstance(result["required_core_fields"], list)
        assert len(result["required_core_fields"]) > 0

    def test_has_valid_row_types(self):
        from app.api.strategy_api import get_strategy_schema
        result = get_strategy_schema()
        assert "new_candidate" in result["valid_row_types"]

    def test_has_valid_gate_statuses(self):
        from app.api.strategy_api import get_strategy_schema
        result = get_strategy_schema()
        for s in ("pass", "fail", "watch"):
            assert s in result["valid_gate_statuses"]

    def test_read_only_flag(self):
        from app.api.strategy_api import get_strategy_schema
        result = get_strategy_schema()
        assert result.get("provider_calls_triggered") is False
        assert result.get("read_only") is True


# ─── validate_draft ───────────────────────────────────────────────────────────

class TestValidateDraft:
    def _validate(self, draft):
        from app.api.strategy_api import validate_draft
        return validate_draft(draft)

    def test_valid_minimal_draft(self):
        result = self._validate({"name": "Test Draft"})
        assert result["valid"] is True
        assert result["errors"] == []

    def test_missing_name_invalid(self):
        result = self._validate({})
        assert result["valid"] is False
        assert any("name" in e for e in result["errors"])

    def test_empty_name_invalid(self):
        result = self._validate({"name": ""})
        assert result["valid"] is False

    def test_name_too_long_invalid(self):
        result = self._validate({"name": "x" * 101})
        assert result["valid"] is False

    def test_non_dict_invalid(self):
        result = self._validate("not a dict")
        assert result["valid"] is False
        assert result["errors"]

    def test_valid_gate_operator(self):
        result = self._validate({
            "name": "Momentum Gate",
            "gates": [{"metric": "score", "operator": "gt", "value": 70}],
        })
        assert result["valid"] is True

    def test_invalid_operator_blocked(self):
        result = self._validate({
            "name": "Bad Gate",
            "gates": [{"metric": "score", "operator": "execute", "value": 70}],
        })
        assert result["valid"] is False
        assert any("operator" in e for e in result["errors"])

    def test_eval_in_gate_value_blocked(self):
        result = self._validate({
            "name": "Injection Gate",
            "gates": [{"metric": "score", "operator": "gt", "value": "eval(1+1)"}],
        })
        assert result["valid"] is False

    def test_exec_in_gate_blocked(self):
        result = self._validate({
            "name": "Exec Gate",
            "gates": [{"metric": "score", "operator": "gt", "value": "exec('import os')"}],
        })
        assert result["valid"] is False

    def test_import_in_gate_blocked(self):
        result = self._validate({
            "name": "Import Gate",
            "gates": [{"operator": "gt", "value": "import os"}],
        })
        assert result["valid"] is False

    def test_unknown_metric_warning_not_error(self):
        result = self._validate({
            "name": "Custom Metric",
            "gates": [{"metric": "custom_metric_xyz", "operator": "gt", "value": 50}],
        })
        assert result["valid"] is True
        assert any("metric" in w for w in result["warnings"])

    def test_no_gates_warning(self):
        result = self._validate({"name": "No Gates Draft"})
        assert result["valid"] is True
        assert any("gates" in w.lower() for w in result["warnings"])

    def test_invalid_gates_type_error(self):
        result = self._validate({"name": "Bad", "gates": "not a list"})
        assert result["valid"] is False

    def test_read_only_flag(self):
        result = self._validate({"name": "Test"})
        assert result.get("provider_calls_triggered") is False
        assert result.get("read_only") is True

    def test_weight_must_be_number(self):
        result = self._validate({"name": "Test", "weight": "heavy"})
        assert result["valid"] is False

    def test_weight_as_float_valid(self):
        result = self._validate({"name": "Test", "weight": 1.5})
        assert result["valid"] is True


# ─── Flask endpoints ───────────────────────────────────────────────────────────

class TestStrategyEndpoints:
    def _app(self):
        from app.main import app
        app.config["TESTING"] = True
        return app

    def test_list_requires_token(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=False):
                resp = client.get("/api/strategies")
                assert resp.status_code == 403

    def test_list_returns_200_with_valid_token(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/strategies")
                assert resp.status_code == 200
                data = resp.get_json()
                assert "strategies" in data
                assert data.get("provider_calls_triggered") is False

    def test_schema_endpoint_requires_token(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=False):
                resp = client.get("/api/strategies/schema")
                assert resp.status_code == 403

    def test_schema_endpoint_200(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/strategies/schema")
                assert resp.status_code == 200
                data = resp.get_json()
                assert "required_core_fields" in data

    def test_test_rows_requires_token(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=False):
                resp = client.get("/api/strategies/test-rows")
                assert resp.status_code == 403

    def test_test_rows_200_with_token(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/strategies/test-rows")
                assert resp.status_code == 200
                data = resp.get_json()
                assert "rows" in data
                assert data.get("provider_calls_triggered") is False

    def test_drafts_post_requires_token(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=False):
                resp = client.post("/api/strategies/drafts",
                                   json={"name": "Test"}, content_type="application/json")
                assert resp.status_code == 403

    def test_drafts_post_200_valid_draft(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.post("/api/strategies/drafts",
                                   json={"name": "Valid Draft"}, content_type="application/json")
                assert resp.status_code == 200
                data = resp.get_json()
                assert data["valid"] is True

    def test_drafts_post_invalid_draft_returns_errors(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.post("/api/strategies/drafts",
                                   json={"name": ""}, content_type="application/json")
                assert resp.status_code == 200
                data = resp.get_json()
                assert data["valid"] is False
                assert data["errors"]

    def test_get_strategy_by_id_200(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/strategies/earnings_calendar")
                assert resp.status_code == 200
                data = resp.get_json()
                assert data["strategy_id"] == "earnings_calendar"

    def test_get_strategy_by_id_404_unknown(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/strategies/does_not_exist")
                assert resp.status_code == 404
                data = resp.get_json()
                assert "error" in data

    def test_get_test_clone_by_id(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/strategies/stock_momentum_unified_test")
                assert resp.status_code == 200
                data = resp.get_json()
                assert data["strategy_id"] == "stock_momentum_unified_test"
                assert data.get("provider_calls_triggered") is False

    def test_get_by_id_requires_token(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=False):
                resp = client.get("/api/strategies/earnings_calendar")
                assert resp.status_code == 403
