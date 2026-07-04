from __future__ import annotations

import json
from unittest.mock import patch


def _client():
    from app.main import app
    app.config["TESTING"] = True
    return app.test_client()


def _strategy_row(ticker: str, verdict: str, score: float) -> dict:
    return {
        "ticker": ticker,
        "verdict": verdict,
        "verdict_tier": 100 if verdict.startswith("PASS") else 80 if verdict.startswith("WATCH") else 35,
        "score": score,
        "raw": {"ticker": ticker, "verdict": verdict, "score": score},
    }


def _core_report() -> tuple[dict, dict]:
    snapshot = {"run_id": "run-1", "completed_at": "2026-07-04T12:00:00+00:00"}
    report = {
        "tradier_snapshot": {
            "_pipeline_status": {"report_quality": "SUCCESS_COMPLETE"},
            "_strategy_results": {
                "stock_momentum": {
                    "pass_count": 1,
                    "watch_count": 1,
                    "fail_count": 0,
                    "canonical_opportunities": [
                        _strategy_row("NVDA", "PASS / STOCK MOMENTUM", 91.0),
                        _strategy_row("MSFT", "WATCH / STOCK MOMENTUM", 77.0),
                    ],
                },
                "forward_factor_calendar": {
                    "pass_count": 0,
                    "watch_count": 1,
                    "fail_count": 0,
                    "canonical_opportunities": [
                        {
                            **_strategy_row("SBUX", "WATCH / EX-EARNINGS IV UNAVAILABLE", 88.0),
                            "raw": {"diagnostic_raw_iv_forward_factor": 0.3118},
                        }
                    ],
                },
                "earnings_calendar": {
                    "pass_count": 1,
                    "watch_count": 1,
                    "fail_count": 1,
                    "canonical_opportunities": [
                        _strategy_row("AAPL", "WATCH / STRUCTURE FOUND", 65.0),
                        _strategy_row("JPM", "PASS / POSSIBLE ENTRY SETUP", 72.0),
                        _strategy_row("CAG", "FAIL / NO VALID CALENDAR STRUCTURE", 35.0),
                    ],
                },
                "skew_momentum_vertical": {
                    "pass_count": 0,
                    "watch_count": 1,
                    "fail_count": 0,
                    "canonical_opportunities": [
                        {
                            **_strategy_row("AMZN", "WATCH / SKEW NOT RICH ENOUGH", 71.0),
                            "raw": {"direction": "Bullish"},
                        }
                    ],
                },
            },
        }
    }
    return snapshot, report


def test_dashboard_renders_real_content_via_token_for_connected_user():
    client = _client()
    snapshot, report = _core_report()
    user = {
        "id": 7,
        "username": "jaia",
        "api_key": "asa_test_dashboard",
        "is_admin": 0,
        "is_active": 1,
        "broker_connection_optional": 0,
        "broker_connected": 1,
        "credentials_validated_at": "2026-07-04T11:00:00+00:00",
        "credentials_last_error": None,
        "robinhood_username": "jaia@example.com",
        "last_login_at": "2026-07-04T10:00:00+00:00",
    }
    calendar_payload = {
        "account_label": "ira_roth",
        "pnl_pct_estimate": -0.74,
    }
    with patch("app.main._get_session_user", return_value=None), \
         patch("app.auth._resolve_user", return_value=user), \
         patch("app.services.personalization._load_latest_core_run", return_value=(snapshot, report)), \
         patch("app.services.run_manifest_repository.RunManifestRepository.latest", return_value={
             "completed_at": "2026-07-04T12:00:00+00:00",
             "report_quality": "SUCCESS_COMPLETE",
             "provider_fetch_count": 12,
             "broker_mode": "connected",
         }), \
         patch("app.db.users.get_latest_user_run", return_value={
             "status": "complete",
             "completed_at": "2026-07-04T12:05:00+00:00",
             "positions_fetched": 4,
             "daily_opportunity_count": 3,
         }), \
         patch("app.db.users.get_latest_complete_user_run", return_value={"run_id": "user-run-1"}), \
         patch("app.db.users.get_user_broker_accounts", return_value=[{
             "account_number": "A1",
             "account_type": "ira_roth",
             "nickname": None,
         }]), \
         patch("app.db.users.get_user_positions", return_value=[{
             "ticker": "SBUX",
             "market_value": 15000.0,
             "position_type": "stock",
             "account_type": "ira_roth",
             "account_number": "A1",
         }, {
             "ticker": "AMZN",
             "position_type": "options",
             "account_type": "options",
             "account_number": "A1",
             "unrealized_pnl_pct": 12.5,
             "option_details": json.dumps({
                 "strategy_type": "skew_vertical",
                 "option_type": "call",
                 "expiration": "2026-09-18",
                 "exit_signal": "MONITOR",
                 "legs": [{"strike": 180}, {"strike": 190}],
                 "current_value": 4.2,
             }),
         }]), \
         patch("app.db.users.get_user_option_positions", return_value=[{
             "underlying": "SBUX",
             "option_type": "call",
             "front_expiration": "2026-08-21",
             "back_expiration": "2026-09-18",
             "action": "MONITOR",
             "account_type": "ira_roth",
             "calendar_json": json.dumps(calendar_payload),
         }]):
        response = client.get("/dashboard?token=asa_test_dashboard")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Last run:" in html
    assert "Open Positions" in html
    assert "SBUX" in html
    assert "Roth IRA" in html
    assert "Forward Factor Calendar" in html
    assert "dry-run: signal live, execution gated" in html
    assert "Stock Momentum" in html
    assert "Earnings Calendar" in html
    assert "Skew Momentum Verticals" in html
    assert "Preferences" in html


def test_dashboard_shows_connect_prompt_for_signals_only_user():
    client = _client()
    snapshot, report = _core_report()
    user = {
        "id": 8,
        "username": "signals@example.com",
        "api_key": "asa_signals_only",
        "is_admin": 0,
        "is_active": 1,
        "broker_connection_optional": 1,
        "broker_connected": 0,
        "credentials_validated_at": None,
        "credentials_last_error": None,
        "robinhood_username": "",
        "last_login_at": "2026-07-04T10:00:00+00:00",
    }
    with patch("app.main._get_session_user", return_value=None), \
         patch("app.auth._resolve_user", return_value=user), \
         patch("app.services.personalization._load_latest_core_run", return_value=(snapshot, report)), \
         patch("app.services.run_manifest_repository.RunManifestRepository.latest", return_value={
             "completed_at": "2026-07-04T12:00:00+00:00",
             "report_quality": "SUCCESS_COMPLETE",
             "provider_fetch_count": 9,
         }), \
         patch("app.db.users.get_latest_user_run", return_value=None):
        response = client.get("/dashboard?token=asa_signals_only")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Connect your brokerage" in html
    assert "Open Positions" not in html
    assert "Forward Factor Calendar" in html


def test_admin_users_endpoint_exposes_broker_optional_fields(tmp_path):
    db = str(tmp_path / "users.db")
    with patch("app.config.USERS_DB_PATH", db):
        from app.db.users import create_user_broker_optional, create_user, init_db
        init_db()
        admin = create_user("admin28f", "pw123", is_admin=1)
        create_user_broker_optional("signals@test.com", "password123")
        admin_key = admin["api_key"]
    client = _client()
    with patch("app.config.USERS_DB_PATH", db):
        response = client.get(f"/api/admin/users?token={admin_key}")
    assert response.status_code == 200
    users = response.get_json()["users"]
    signals_user = next(item for item in users if item["username"] == "signals@test.com")
    assert signals_user["broker_connected"] is False
    assert signals_user["broker_connection_optional"] is True
