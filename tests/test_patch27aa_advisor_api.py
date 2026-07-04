"""
Patch 27AA — Advisor API endpoints.

Tests:
1. /api/advisor/status — no auth, returns ok
2. /api/advisor/daily — requires auth, returns actions + strategy_summary
3. /api/advisor/positions — requires auth, returns accounts
4. 401 on missing/invalid token for protected endpoints
5. Authorization: Bearer header accepted
6. ?token= query param accepted
7. provider_calls_triggered=False on all responses
8. ff_dry_run=True in daily response
9. FF not actionable via advisor API (actionability_score=0 for FF rows)
10. No live provider calls triggered (no hub, no broker call in path)
"""

import json
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch


def _make_app():
    from app.main import app
    app.config["TESTING"] = True
    return app.test_client()


def _fake_snapshot():
    return {
        "run_id": "test-run-aaa",
        "completed_at": "2026-06-16T17:05:00+00:00",
        "mode": "prod",
        "status": "complete",
    }


def _fake_report():
    return {
        "tradier_snapshot": {
            "_pipeline_status": {
                "report_quality": "SUCCESS_COMPLETE",
                "overall_status": "complete",
            },
            "_daily_opportunity_engine": {
                "actions": [
                    {
                        "type": "stock_add",
                        "ticker": "CRDO",
                        "priority_score": 87.4,
                        "action": "CONSIDER ADDING",
                        "why": "Momentum confirmed.",
                        "source": "Stock Momentum Add Strategy v1",
                    },
                    {
                        "type": "stock_add",
                        "ticker": "NVDA",
                        "priority_score": 80.0,
                        "action": "CONSIDER ADDING",
                        "why": "High volume breakout.",
                        "source": "Stock Momentum Add Strategy v1",
                    },
                ],
            },
            "_strategy_results": {
                "stock_momentum": {"pass_count": 1, "watch_count": 10, "fail_count": 1, "skipped_count": 0},
                "earnings_calendar": {"pass_count": 0, "watch_count": 3, "fail_count": 2, "skipped_count": 0},
                "skew_momentum_vertical": {"pass_count": 0, "watch_count": 6, "fail_count": 1, "skipped_count": 0},
                "forward_factor_calendar": {"pass_count": 0, "watch_count": 0, "fail_count": 6, "skipped_count": 30},
            },
        },
        "positions": [
            {"ticker": "NVDA", "quantity": 10.0, "avg_buy_price": 112.40, "current_price": 131.20,
             "gain_loss_pct": 16.7, "market_value": 1312.0, "account": "roth_ira", "asset_type": "stock"},
            {"ticker": "AAPL", "quantity": 5.0, "avg_buy_price": 170.0, "current_price": 210.0,
             "gain_loss_pct": 23.5, "market_value": 1050.0, "account": "individual", "asset_type": "stock"},
        ],
    }


def _patch_snapshot(summary_report=None):
    """Patch _load_snapshot to return fake data without hitting DB."""
    snap = _fake_snapshot()
    report = summary_report or _fake_report()

    def fake_load():
        return snap, {"report_data": report}, report

    return patch("app.api.advisor._load_snapshot", side_effect=fake_load)


def _patch_no_snapshot():
    def fake_load():
        return None, None, None
    return patch("app.api.advisor._load_snapshot", side_effect=fake_load)


VALID_TOKEN = "jaa-stonks"


class TestAdvisorStatus(unittest.TestCase):
    def setUp(self):
        self.client = _make_app()

    def test_status_no_auth_required(self):
        with _patch_snapshot():
            resp = self.client.get("/api/advisor/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "ok")

    def test_status_shape(self):
        with _patch_snapshot():
            resp = self.client.get("/api/advisor/status")
        data = resp.get_json()
        for key in ["status", "last_run_quality", "last_run_date", "daily_opportunity_count", "ff_dry_run",
                    "provider_calls_triggered", "run_id", "generated_at"]:
            self.assertIn(key, data, msg=f"Missing key: {key}")

    def test_status_ff_dry_run_true(self):
        with _patch_snapshot():
            resp = self.client.get("/api/advisor/status")
        self.assertTrue(resp.get_json()["ff_dry_run"])

    def test_status_no_provider_calls_flag(self):
        with _patch_snapshot():
            resp = self.client.get("/api/advisor/status")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.get_json()["provider_calls_triggered"])

    def test_status_no_snapshot_returns_ok(self):
        with _patch_no_snapshot():
            resp = self.client.get("/api/advisor/status")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "ok")

    def test_status_correct_action_count(self):
        with _patch_snapshot():
            resp = self.client.get("/api/advisor/status")
        self.assertEqual(resp.get_json()["daily_opportunity_count"], 2)

    def test_status_correct_run_date(self):
        with _patch_snapshot():
            resp = self.client.get("/api/advisor/status")
        self.assertEqual(resp.get_json()["last_run_date"], "2026-06-16")


class TestAdvisorDaily(unittest.TestCase):
    def setUp(self):
        self.client = _make_app()

    def test_daily_requires_auth(self):
        with _patch_snapshot():
            resp = self.client.get("/api/advisor/daily")
        self.assertEqual(resp.status_code, 401)

    def test_daily_invalid_token_401(self):
        with _patch_snapshot():
            resp = self.client.get("/api/advisor/daily?token=wrong")
        self.assertEqual(resp.status_code, 401)

    def test_daily_bearer_header_accepted(self):
        with patch("app.config.RUN_TOKEN", VALID_TOKEN), _patch_snapshot():
            resp = self.client.get("/api/advisor/daily",
                                   headers={"Authorization": f"Bearer {VALID_TOKEN}"})
        self.assertEqual(resp.status_code, 200)

    def test_daily_query_param_accepted(self):
        with patch("app.config.RUN_TOKEN", VALID_TOKEN), _patch_snapshot():
            resp = self.client.get(f"/api/advisor/daily?token={VALID_TOKEN}")
        self.assertEqual(resp.status_code, 200)

    def test_daily_response_shape(self):
        with patch("app.config.RUN_TOKEN", VALID_TOKEN), _patch_snapshot():
            resp = self.client.get(f"/api/advisor/daily?token={VALID_TOKEN}")
        data = resp.get_json()
        for key in ["run_id", "run_date", "run_quality", "generated_at", "actions",
                    "strategy_summary", "ff_dry_run", "provider_calls_triggered"]:
            self.assertIn(key, data, msg=f"Missing key: {key}")

    def test_daily_provider_calls_false(self):
        with patch("app.config.RUN_TOKEN", VALID_TOKEN), _patch_snapshot():
            resp = self.client.get(f"/api/advisor/daily?token={VALID_TOKEN}")
        self.assertFalse(resp.get_json()["provider_calls_triggered"])

    def test_daily_ff_dry_run_true(self):
        with patch("app.config.RUN_TOKEN", VALID_TOKEN), _patch_snapshot():
            resp = self.client.get(f"/api/advisor/daily?token={VALID_TOKEN}")
        self.assertTrue(resp.get_json()["ff_dry_run"])

    def test_daily_actions_present(self):
        with patch("app.config.RUN_TOKEN", VALID_TOKEN), _patch_snapshot():
            resp = self.client.get(f"/api/advisor/daily?token={VALID_TOKEN}")
        data = resp.get_json()
        self.assertEqual(len(data["actions"]), 2)
        self.assertEqual(data["actions"][0]["ticker"], "CRDO")

    def test_daily_action_shape(self):
        with patch("app.config.RUN_TOKEN", VALID_TOKEN), _patch_snapshot():
            resp = self.client.get(f"/api/advisor/daily?token={VALID_TOKEN}")
        action = resp.get_json()["actions"][0]
        for key in ["ticker", "action", "type", "strategy", "signal_score"]:
            self.assertIn(key, action, msg=f"Action missing key: {key}")

    def test_daily_strategy_summary_all_strategies(self):
        with patch("app.config.RUN_TOKEN", VALID_TOKEN), _patch_snapshot():
            resp = self.client.get(f"/api/advisor/daily?token={VALID_TOKEN}")
        summary = resp.get_json()["strategy_summary"]
        self.assertIn("stock_momentum", summary)
        self.assertIn("forward_factor_calendar", summary)
        ff = summary["forward_factor_calendar"]
        self.assertEqual(ff["skipped"], 30)

    def test_daily_no_snapshot_404(self):
        with patch("app.config.RUN_TOKEN", VALID_TOKEN), _patch_no_snapshot():
            resp = self.client.get(f"/api/advisor/daily?token={VALID_TOKEN}")
        self.assertEqual(resp.status_code, 404)

    def test_daily_run_id_present(self):
        with patch("app.config.RUN_TOKEN", VALID_TOKEN), _patch_snapshot():
            resp = self.client.get(f"/api/advisor/daily?token={VALID_TOKEN}")
        self.assertEqual(resp.get_json()["run_id"], "test-run-aaa")


class TestAdvisorPositions(unittest.TestCase):
    def setUp(self):
        self.client = _make_app()

    def test_positions_requires_auth(self):
        with _patch_snapshot():
            resp = self.client.get("/api/advisor/positions")
        self.assertEqual(resp.status_code, 401)

    def test_positions_invalid_token_401(self):
        with _patch_snapshot():
            resp = self.client.get("/api/advisor/positions?token=bad")
        self.assertEqual(resp.status_code, 401)

    def test_positions_valid_token_200(self):
        with patch("app.config.RUN_TOKEN", VALID_TOKEN), _patch_snapshot():
            resp = self.client.get(f"/api/advisor/positions?token={VALID_TOKEN}")
        self.assertEqual(resp.status_code, 200)

    def test_positions_bearer_header_accepted(self):
        with patch("app.config.RUN_TOKEN", VALID_TOKEN), _patch_snapshot():
            resp = self.client.get("/api/advisor/positions",
                                   headers={"Authorization": f"Bearer {VALID_TOKEN}"})
        self.assertEqual(resp.status_code, 200)

    def test_positions_response_shape(self):
        with patch("app.config.RUN_TOKEN", VALID_TOKEN), _patch_snapshot():
            resp = self.client.get(f"/api/advisor/positions?token={VALID_TOKEN}")
        data = resp.get_json()
        for key in ["run_id", "generated_at", "core_run_id", "core_generated_at", "as_of", "positions_as_of",
                    "position_data_stale", "position_data_status", "accounts", "broker_accounts",
                    "options_positions", "options_count", "has_open_verticals", "has_open_calendars",
                    "provider_calls_triggered", "personalized"]:
            self.assertIn(key, data, msg=f"Missing key: {key}")

    def test_positions_accounts_grouped(self):
        with patch("app.config.RUN_TOKEN", VALID_TOKEN), _patch_snapshot():
            resp = self.client.get(f"/api/advisor/positions?token={VALID_TOKEN}")
        accounts = resp.get_json()["accounts"]
        account_types = {a["account_type"] for a in accounts}
        self.assertIn("roth_ira", account_types)
        self.assertIn("individual", account_types)

    def test_positions_row_shape(self):
        with patch("app.config.RUN_TOKEN", VALID_TOKEN), _patch_snapshot():
            resp = self.client.get(f"/api/advisor/positions?token={VALID_TOKEN}")
        accounts = resp.get_json()["accounts"]
        roth = next(a for a in accounts if a["account_type"] == "roth_ira")
        pos = roth["positions"][0]
        for key in ["ticker", "quantity", "avg_cost", "current_price", "unrealized_pnl_pct"]:
            self.assertIn(key, pos, msg=f"Position missing key: {key}")

    def test_positions_provider_calls_false(self):
        with patch("app.config.RUN_TOKEN", VALID_TOKEN), _patch_snapshot():
            resp = self.client.get(f"/api/advisor/positions?token={VALID_TOKEN}")
        self.assertFalse(resp.get_json()["provider_calls_triggered"])

    def test_positions_no_snapshot_404(self):
        with patch("app.config.RUN_TOKEN", VALID_TOKEN), _patch_no_snapshot():
            resp = self.client.get(f"/api/advisor/positions?token={VALID_TOKEN}")
        self.assertEqual(resp.status_code, 404)

    def test_positions_admin_shared_path_has_stable_empty_options_and_freshness(self):
        with patch("app.config.RUN_TOKEN", VALID_TOKEN), _patch_snapshot():
            resp = self.client.get(f"/api/advisor/positions?token={VALID_TOKEN}")
        data = resp.get_json()
        self.assertFalse(data["personalized"])
        self.assertEqual(data["run_id"], "test-run-aaa")
        self.assertEqual(data["core_run_id"], "test-run-aaa")
        self.assertEqual(data["generated_at"], "2026-06-16T17:05:00+00:00")
        self.assertEqual(data["positions_as_of"], "2026-06-16T17:05:00+00:00")
        self.assertFalse(data["position_data_stale"])
        self.assertEqual(data["position_data_status"], "FRESH")
        self.assertEqual(data["broker_accounts"], [])
        self.assertEqual(data["options_positions"], [])
        self.assertEqual(data["options_count"], 0)
        self.assertFalse(data["has_open_verticals"])
        self.assertFalse(data["has_open_calendars"])

    def test_positions_personalized_stale_metadata_when_user_run_older_than_core(self):
        with patch("app.auth._resolve_user", return_value={"id": 123, "is_active": True, "is_admin": False}), \
             patch("app.db.users.get_latest_complete_user_run",
                   return_value={"run_id": "usr-1", "completed_at": "2026-06-15T17:05:00+00:00",
                                 "core_run_id_used": "test-run-aaa"}), \
             patch("app.db.users.get_user_positions",
                   return_value=[{"ticker": "NVDA", "quantity": 1.0, "avg_cost": 100.0,
                                  "current_price": 110.0, "unrealized_pnl_pct": 10.0,
                                  "market_value": 110.0, "account_type": "roth_ira",
                                  "account_number": "111", "position_type": "stock"}]), \
             patch("app.db.users.get_user_broker_accounts",
                   return_value=[{"account_number": "111", "account_type": "Roth IRA",
                                  "broker_type": "robinhood", "discovered_at": "2026-06-15T17:00:00+00:00",
                                  "nickname": None}]), \
             _patch_snapshot():
            resp = self.client.get("/api/advisor/positions?token=user-token")
        data = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data["personalized"])
        self.assertEqual(data["user_run_id"], "usr-1")
        self.assertEqual(data["run_id"], "test-run-aaa")
        self.assertEqual(data["core_run_id"], "test-run-aaa")
        self.assertEqual(data["positions_as_of"], "2026-06-15T17:05:00+00:00")
        self.assertEqual(data["core_generated_at"], "2026-06-16T17:05:00+00:00")
        self.assertTrue(data["position_data_stale"])
        self.assertEqual(data["position_data_status"], "STALE_USER_POSITIONS")
        self.assertFalse(data["provider_calls_triggered"])

    def test_positions_personalized_no_run_has_stable_empty_keys(self):
        with patch("app.auth._resolve_user", return_value={"id": 123, "is_active": True, "is_admin": False}), \
             patch("app.db.users.get_latest_complete_user_run", return_value=None), \
             _patch_snapshot():
            resp = self.client.get("/api/advisor/positions?token=user-token")
        data = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(data["provider_calls_triggered"])
        self.assertFalse(data["personalized"])
        self.assertEqual(data["run_id"], "test-run-aaa")
        self.assertEqual(data["generated_at"], "2026-06-16T17:05:00+00:00")
        self.assertEqual(data["core_run_id"], "test-run-aaa")
        self.assertEqual(data["core_generated_at"], "2026-06-16T17:05:00+00:00")
        self.assertIsNone(data["as_of"])
        self.assertIsNone(data["positions_as_of"])
        self.assertIsNone(data["position_data_stale"])
        self.assertEqual(data["position_data_status"], "NO_PERSONALIZATION_RUN")
        self.assertEqual(data["accounts"], [])
        self.assertEqual(data["broker_accounts"], [])
        self.assertEqual(data["options_positions"], [])
        self.assertEqual(data["options_count"], 0)
        self.assertFalse(data["has_open_verticals"])
        self.assertFalse(data["has_open_calendars"])


class TestFFNotActionableViaAdvisor(unittest.TestCase):
    def setUp(self):
        self.client = _make_app()

    def test_ff_strategy_has_zero_pass_in_summary(self):
        with patch("app.config.RUN_TOKEN", VALID_TOKEN), _patch_snapshot():
            resp = self.client.get(f"/api/advisor/daily?token={VALID_TOKEN}")
        ff = resp.get_json()["strategy_summary"].get("forward_factor_calendar", {})
        self.assertEqual(ff.get("pass", 0), 0)

    def test_no_ff_actions_in_daily_opportunity(self):
        """FF dry_run=True means FF rows must not appear in actions list."""
        with patch("app.config.RUN_TOKEN", VALID_TOKEN), _patch_snapshot():
            resp = self.client.get(f"/api/advisor/daily?token={VALID_TOKEN}")
        actions = resp.get_json()["actions"]
        ff_actions = [a for a in actions if "forward_factor" in str(a.get("strategy") or "").lower()]
        self.assertEqual(len(ff_actions), 0, "FF rows must not be in daily opportunity actions")
