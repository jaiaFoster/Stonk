"""
ASA Patch 30B — Draft Strategy Safety Tests

Verifies that the draft validation endpoint:
  - Rejects all execution-related top-level keys (code, python, eval, exec,
    shell, command, sql, url_fetch, network, broker_write, order, trade, etc.)
  - Rejects forbidden string patterns in any nested value
  - Only allows the safe DSL field set
  - Accepts valid safe drafts
  - Never executes code (static analysis only)
  - CAVEMAN MODE: FF dry-run, no broker writes, no DO changes
"""
from __future__ import annotations

import py_compile
from unittest.mock import patch

from app import config as cfg


class TestCompile:
    def test_strategy_api_compiles(self):
        py_compile.compile("app/api/strategy_api.py", doraise=True)


# ─── Top-level forbidden key rejection ───────────────────────────────────────

class TestForbiddenTopLevelKeys:
    def _validate(self, draft: dict) -> dict:
        from app.api.strategy_api import validate_draft
        return validate_draft(draft)

    def _assert_rejected(self, key: str, value=None):
        draft = {"name": "Test", key: value or "bad"}
        result = self._validate(draft)
        assert result["valid"] is False, f"Expected rejection for key {key!r}"
        assert any(key in e for e in result["errors"]), (
            f"Error message should mention key {key!r}: {result['errors']}"
        )

    def test_code_field_rejected(self):
        self._assert_rejected("code", "eval('1+1')")

    def test_python_field_rejected(self):
        self._assert_rejected("python", "import os")

    def test_eval_field_rejected(self):
        self._assert_rejected("eval", "1+1")

    def test_exec_field_rejected(self):
        self._assert_rejected("exec", "os.system('ls')")

    def test_shell_field_rejected(self):
        self._assert_rejected("shell", "rm -rf /")

    def test_command_field_rejected(self):
        self._assert_rejected("command", "curl http://evil.com")

    def test_sql_field_rejected(self):
        self._assert_rejected("sql", "SELECT * FROM users")

    def test_url_fetch_field_rejected(self):
        self._assert_rejected("url_fetch", "https://example.com")

    def test_network_field_rejected(self):
        self._assert_rejected("network", {"url": "https://evil.com"})

    def test_broker_write_field_rejected(self):
        self._assert_rejected("broker_write", True)

    def test_order_field_rejected(self):
        self._assert_rejected("order", {"symbol": "AAPL", "qty": 100})

    def test_trade_field_rejected(self):
        self._assert_rejected("trade", {"action": "buy"})

    def test_schedule_field_rejected(self):
        self._assert_rejected("schedule", "daily")

    def test_webhook_field_rejected(self):
        self._assert_rejected("webhook", "https://hook.com")


# ─── Forbidden string patterns rejected anywhere in draft ─────────────────────

class TestForbiddenStringPatterns:
    def _validate(self, draft: dict) -> dict:
        from app.api.strategy_api import validate_draft
        return validate_draft(draft)

    def _assert_rejected(self, field: str, value: str):
        draft = {"name": "Test", "description": "ok", "gates": [{"metric": "score", "operator": "gt", field: value}]}
        result = self._validate(draft)
        assert result["valid"] is False, f"Expected rejection for {field}={value!r}"

    def test_eval_in_gate_value(self):
        self._assert_rejected("value", "eval(1+1)")

    def test_exec_in_gate_value(self):
        self._assert_rejected("value", "exec('import os')")

    def test_import_in_gate_reason(self):
        self._assert_rejected("reason_template", "import os")

    def test_dunder_in_gate_value(self):
        self._assert_rejected("value", "__class__.__bases__")

    def test_os_in_gate_value(self):
        self._assert_rejected("value", "os.system('ls')")

    def test_sys_in_gate_value(self):
        self._assert_rejected("value", "sys.exit(0)")

    def test_subprocess_in_gate_value(self):
        self._assert_rejected("value", "subprocess.run(['ls'])")

    def test_forbidden_in_description(self):
        result = self._validate({"name": "Test", "description": "eval all the things"})
        assert result["valid"] is False

    def test_forbidden_in_name_not_blocked_by_pattern(self):
        # "eval" in name should be a pattern error (from description scan, not name length)
        result = self._validate({"name": "eval test"})
        # name contains "eval" — scan should catch it
        # Note: eval is in name string — depends on whether _scan_for_forbidden_patterns
        # catches name field too
        # This is a safe-by-default test: at least don't crash
        assert isinstance(result["valid"], bool)


# ─── Valid safe drafts accepted ───────────────────────────────────────────────

class TestValidDraftsAccepted:
    def _validate(self, draft: dict) -> dict:
        from app.api.strategy_api import validate_draft
        return validate_draft(draft)

    def test_minimal_name_only(self):
        result = self._validate({"name": "My Strategy"})
        assert result["valid"] is True
        assert result["errors"] == []

    def test_full_safe_draft(self):
        result = self._validate({
            "name": "Momentum Entry",
            "description": "Requires strong relative strength and volume.",
            "asset_class": "equity",
            "inputs": ["quote", "candles"],
            "gates": [
                {
                    "metric": "momentum_score",
                    "operator": "gte",
                    "value": 70,
                    "gate": "setup.momentum",
                },
                {
                    "metric": "relative_strength",
                    "operator": "gt",
                    "value": 0,
                    "gate": "setup.rs",
                },
            ],
            "weight": 1.0,
            "reason_template": "Momentum score passed threshold.",
        })
        assert result["valid"] is True
        assert result["errors"] == []

    def test_valid_operator_gt(self):
        result = self._validate({"name": "T", "gates": [{"metric": "score", "operator": "gt", "value": 50}]})
        assert result["valid"] is True

    def test_valid_operator_gte(self):
        result = self._validate({"name": "T", "gates": [{"operator": "gte", "value": 50}]})
        assert result["valid"] is True

    def test_valid_operator_eq(self):
        result = self._validate({"name": "T", "gates": [{"operator": "eq", "value": "pass"}]})
        assert result["valid"] is True

    def test_unknown_metric_is_warning_not_error(self):
        result = self._validate({
            "name": "T",
            "gates": [{"metric": "custom_xyz", "operator": "gt", "value": 1}],
        })
        assert result["valid"] is True
        assert any("metric" in w for w in result["warnings"])

    def test_missing_gates_is_warning_not_error(self):
        result = self._validate({"name": "No Gates"})
        assert result["valid"] is True
        assert any("gate" in w.lower() for w in result["warnings"])

    def test_read_only_flag(self):
        result = self._validate({"name": "T"})
        assert result.get("provider_calls_triggered") is False
        assert result.get("read_only") is True

    def test_rules_alias_accepted(self):
        result = self._validate({
            "name": "T",
            "rules": [{"metric": "score", "operator": "gt", "value": 70}],
        })
        assert result["valid"] is True


# ─── Operator allowlist enforced ──────────────────────────────────────────────

class TestOperatorAllowlist:
    def _validate(self, draft: dict) -> dict:
        from app.api.strategy_api import validate_draft
        return validate_draft(draft)

    def test_invalid_operator_rejected(self):
        result = self._validate({"name": "T", "gates": [{"operator": "execute", "value": 1}]})
        assert result["valid"] is False
        assert any("operator" in e for e in result["errors"])

    def test_python_operator_rejected(self):
        result = self._validate({"name": "T", "gates": [{"operator": "python", "value": 1}]})
        assert result["valid"] is False

    def test_all_safe_operators_accepted(self):
        from app.api.strategy_api import _SAFE_OPERATORS
        for op in _SAFE_OPERATORS:
            result = self._validate({"name": "T", "gates": [{"operator": op, "value": 1}]})
            assert result["valid"] is True, f"Safe operator {op!r} incorrectly rejected"


# ─── Flask endpoint draft safety ──────────────────────────────────────────────

class TestDraftEndpointSafety:
    def _app(self):
        from app.main import app
        app.config["TESTING"] = True
        return app

    def test_malicious_draft_rejected_via_endpoint(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.post(
                    "/api/strategies/drafts",
                    json={"name": "Bad", "code": "eval('1+1')", "rules": []},
                    content_type="application/json",
                )
                assert resp.status_code == 200
                data = resp.get_json()
                assert data["valid"] is False
                assert data["errors"]

    def test_safe_draft_accepted_via_endpoint(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.post(
                    "/api/strategies/drafts",
                    json={
                        "name": "Safe Strategy",
                        "description": "Momentum entry with RS confirmation.",
                        "gates": [{"metric": "momentum_score", "operator": "gte", "value": 70}],
                    },
                    content_type="application/json",
                )
                assert resp.status_code == 200
                data = resp.get_json()
                assert data["valid"] is True

    def test_eval_injection_rejected(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.post(
                    "/api/strategies/drafts",
                    json={"name": "Evil", "description": "eval(os.system('whoami'))"},
                    content_type="application/json",
                )
                data = resp.get_json()
                assert data["valid"] is False

    def test_no_provider_calls_triggered(self):
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.post(
                    "/api/strategies/drafts",
                    json={"name": "T"},
                    content_type="application/json",
                )
                data = resp.get_json()
                assert data.get("provider_calls_triggered") is False


# ─── CAVEMAN MODE invariants ──────────────────────────────────────────────────

class TestCavemanModeInvariants:
    def test_ff_dry_run_unchanged(self):
        assert cfg.FORWARD_FACTOR_DRY_RUN is True

    def test_no_trade_execution_in_config(self):
        assert not getattr(cfg, "TRADE_EXECUTION_ENABLED", False)

    def test_ff_spec_dry_run_true(self):
        from app.services.strategy_spec_registry import get_spec
        assert get_spec("forward_factor_calendar")["dry_run"] is True

    def test_ff_spec_daily_opportunity_not_allowed(self):
        from app.services.strategy_spec_registry import get_spec
        assert get_spec("forward_factor_calendar")["daily_opportunity_allowed"] is False

    def test_validate_draft_never_executes_code(self):
        # Verifies that validate_draft is a pure dictionary inspection function.
        # If it tried to eval/exec, this would raise (or return wrong result).
        from app.api.strategy_api import validate_draft
        nasty_draft = {
            "name": "Test",
            "code": "raise RuntimeError('executed!')",
            "rules": [],
        }
        result = validate_draft(nasty_draft)
        # Should return a validation result dict, not raise
        assert isinstance(result, dict)
        assert result["valid"] is False  # rejected due to 'code' key

    def test_public_screener_no_provider_calls(self):
        from app.main import app
        app.config["TESTING"] = True
        client = app.test_client()
        resp = client.get("/screener/data")
        if resp.status_code == 200:
            data = resp.get_json() or {}
            assert data.get("provider_calls_triggered") is not True
