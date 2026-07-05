from __future__ import annotations

import json
from unittest.mock import patch


def _client():
    from app.main import app

    app.config["TESTING"] = True
    return app.test_client()


def _core_data():
    snapshot = {"run_id": "run-public-29e", "completed_at": "2026-07-04T22:55:05.764810+00:00"}
    report = {
        "positions": [],
        "tradier_snapshot": {
            "_pipeline_status": {"report_quality": "SUCCESS_COMPLETE", "run_mode": "dev"},
            "_earnings_discovery_quality": {
                "candidate_count": 6,
                "items": [
                    {
                        "ticker": "JPM",
                        "earnings_date": "2026-07-18",
                        "earnings_time": "amc",
                        "date_confidence": "single_source",
                        "date_sources": ["finnhub"],
                        "date_conflict": False,
                    },
                    {
                        "ticker": "MS",
                        "earnings_date": "2026-07-19",
                        "earnings_time": "unknown",
                        "date_confidence": "disputed",
                        "date_sources": ["finnhub", "alphavantage"],
                        "date_conflict": True,
                    },
                ],
            },
            "_forward_factor_strategy": {
                "stage_counts": {
                    "universe": 12,
                    "cheap_evaluated": 4,
                    "skipped_dev_cap": 2,
                    "skipped_provider_budget": 1,
                    "chain_sets": 3,
                },
                "items": [
                    {
                        "ticker": "SBUX",
                        "verdict": "WATCH / EX-EARNINGS IV UNAVAILABLE",
                        "diagnostic_raw_iv_forward_factor": 0.968,
                        "front_dte": 60,
                        "back_dte": 90,
                        "front_expiration": "2026-08-21",
                        "back_expiration": "2026-09-18",
                        "earnings_date": "2026-07-25",
                        "earnings_confidence": "single_source",
                        "earnings_contaminated": True,
                        "date_sources": ["finnhub"],
                        "raw": {
                            "ticker": "SBUX",
                            "verdict": "WATCH / EX-EARNINGS IV UNAVAILABLE",
                            "is_diagnostic_only": True,
                            "can_enter_daily_opportunity": False,
                        },
                    }
                ],
                "rows": [],
            },
            "_strategy_results": {
                "stock_momentum": {
                    "pass_count": 1,
                    "watch_count": 0,
                    "fail_count": 0,
                    "canonical_opportunities": [
                        {
                            "ticker": "ALGN",
                            "verdict": "CONSIDER ADDING",
                            "verdict_tier": 100,
                            "score": 82.4,
                            "raw": {"ticker": "ALGN", "verdict": "CONSIDER ADDING", "score": 82.4},
                        }
                    ],
                },
                "forward_factor_calendar": {
                    "pass_count": 0,
                    "watch_count": 1,
                    "fail_count": 0,
                    "canonical_opportunities": [
                        {
                            "ticker": "SBUX",
                            "verdict": "WATCH / EX-EARNINGS IV UNAVAILABLE",
                            "verdict_tier": 80,
                            "score": 89.0,
                            "front_dte": 60,
                            "back_dte": 90,
                            "diagnostic_raw_iv_forward_factor": 0.968,
                            "earnings_confidence": "single_source",
                            "date_sources": ["finnhub"],
                            "date_conflict": False,
                            "earnings_contaminated": True,
                            "raw": {
                                "ticker": "SBUX",
                                "verdict": "WATCH / EX-EARNINGS IV UNAVAILABLE",
                                "is_diagnostic_only": True,
                                "can_enter_daily_opportunity": False,
                            },
                        }
                    ],
                },
                "earnings_calendar": {
                    "pass_count": 1,
                    "watch_count": 0,
                    "fail_count": 0,
                    "canonical_opportunities": [
                        {
                            "ticker": "JPM",
                            "verdict": "PASS / POSSIBLE ENTRY SETUP",
                            "verdict_tier": 100,
                            "score": 74.0,
                            "earnings_date": "2026-07-18",
                            "earnings_time": "amc",
                            "date_confidence": "single_source",
                            "date_sources": ["finnhub"],
                            "date_conflict": False,
                            "raw": {"ticker": "JPM", "verdict": "PASS / POSSIBLE ENTRY SETUP"},
                        }
                    ],
                },
                "skew_momentum_vertical": {
                    "pass_count": 0,
                    "watch_count": 0,
                    "fail_count": 0,
                    "canonical_opportunities": [],
                },
            },
        },
    }
    return snapshot, report, report["tradier_snapshot"]


def test_public_demo_summary_counts(tmp_path):
    db_path = str(tmp_path / "telemetry.db")
    with patch("app.config.TELEMETRY_ENABLED", True), \
         patch("app.config.PUBLIC_DEMO_TELEMETRY_ENABLED", True), \
         patch("app.config.TELEMETRY_DB_PATH", db_path), \
         patch("app.config.RUN_TOKEN", "run-token-29e"):
        from app.db.telemetry import public_demo_summary, record_public_demo_event

        record_public_demo_event(
            event_type="page_view",
            page="/screener",
            session_id="sid-1",
            run_id="run-1",
            strategy_id="forward_factor_calendar",
            ticker="SBUX",
            verdict="WATCH / EX-EARNINGS IV UNAVAILABLE",
            action="load",
            referrer_host="example.com",
            user_agent_family="browser",
            ip="1.2.3.4",
            db_path=db_path,
        )
        record_public_demo_event(
            event_type="strategy_nav_click",
            page="/screener",
            session_id="sid-1",
            run_id="run-1",
            strategy_id="forward_factor_calendar",
            action="nav_click",
            db_path=db_path,
        )
        record_public_demo_event(
            event_type="cta_click",
            page="/screener",
            session_id="sid-2",
            run_id="run-1",
            action="create_account",
            db_path=db_path,
        )
        summary = public_demo_summary(days=7, db_path=db_path)

    assert summary["total_events"] == 3
    assert summary["page_views"] == 1
    assert summary["unique_sessions"] == 2
    assert summary["cta_clicks"] == 1
    assert summary["strategy_nav_clicks"] == 1
    assert summary["top_strategies"][0]["strategy_id"] == "forward_factor_calendar"
    assert summary["top_tickers"][0]["ticker"] == "SBUX"


def test_public_demo_endpoint_records_event(tmp_path):
    client = _client()
    db_path = str(tmp_path / "telemetry.db")
    with patch("app.config.TELEMETRY_ENABLED", True), \
         patch("app.config.PUBLIC_DEMO_TELEMETRY_ENABLED", True), \
         patch("app.config.TELEMETRY_DB_PATH", db_path), \
         patch("app.config.RUN_TOKEN", "run-token-29e"), \
         patch("app.api.telemetry._client_ip", return_value="5.6.7.8"):
        resp = client.post(
            "/api/telemetry/public-demo",
            json={
                "event_type": "page_view",
                "page": "/screener",
                "session_id": "anon-1",
                "run_id": "core-run-1",
                "action": "load",
            },
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://example.com/screener"},
        )
        from app.db.telemetry import public_demo_summary

        summary = public_demo_summary(days=7, db_path=db_path)

    assert resp.status_code == 200
    assert resp.get_json()["recorded"] is True
    assert summary["page_views"] == 1
    assert summary["unique_sessions"] == 1


def test_public_demo_endpoint_rejects_invalid_event_type():
    client = _client()
    with patch("app.config.PUBLIC_DEMO_TELEMETRY_ENABLED", True):
        resp = client.post("/api/telemetry/public-demo", json={"event_type": "bad_event"})
    assert resp.status_code == 400


def test_admin_demo_telemetry_endpoint_returns_read_only():
    client = _client()
    fake = {
        "period_days": 7,
        "total_events": 9,
        "page_views": 3,
        "unique_sessions": 2,
        "cta_clicks": 1,
        "strategy_nav_clicks": 2,
        "signal_card_clicks": 2,
        "copy_link_clicks": 1,
        "top_strategies": [{"strategy_id": "forward_factor_calendar", "count": 3}],
        "top_tickers": [{"ticker": "SBUX", "count": 2}],
        "top_verdicts": [{"verdict": "WATCH / EX-EARNINGS IV UNAVAILABLE", "count": 2}],
        "last_seen_at": "2026-07-04 22:00:00",
    }
    with patch("app.config.DEV_API_TOKEN", "admin-token"), \
         patch("app.config.RUN_TOKEN", "admin-token"), \
         patch("app.db.telemetry.public_demo_summary", return_value=fake):
        resp = client.get("/api/admin/demo-telemetry?days=7", headers={"Authorization": "Bearer admin-token"})
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["status"] == "ok"
    assert body["provider_calls_triggered"] is False
    assert body["total_events"] == 9


def test_admin_ff_graduation_endpoint_returns_read_only():
    client = _client()
    fake = {
        "status": "ok",
        "checked_at": "2026-07-04T22:00:00+00:00",
        "period_days": 30,
        "total_observations": 10,
        "pass_observations": 2,
        "source_qualified_positive_count": 0,
        "diagnostic_positive_count": 2,
        "near_positive_count": 1,
        "structure_complete_count": 2,
        "liquidity_pass_count": 1,
        "earnings_contaminated_count": 1,
        "avg_forward_factor": 0.41,
        "top_pass_tickers": [],
        "recent_passes": [],
        "common_blockers": [],
        "dry_run": True,
        "provider_calls_triggered": False,
        "eligible_for_review": False,
        "readiness": {"eligible_for_review": False, "reasons": ["Needs manual review outcome capture"]},
    }
    with patch("app.config.DEV_API_TOKEN", "admin-token"), \
         patch("app.config.RUN_TOKEN", "admin-token"), \
         patch("app.services.ff_graduation_analysis_service.build_ff_graduation_analysis", return_value=fake):
        resp = client.get("/api/admin/ff-graduation?days=30", headers={"Authorization": "Bearer admin-token"})
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["dry_run"] is True
    assert body["provider_calls_triggered"] is False


def test_earnings_trust_summary_reads_cached_snapshot_only():
    fake_snapshot = {
        "run_id": "run-cache-1",
        "completed_at": "2026-07-04T22:00:00+00:00",
        "provider_status_json": json.dumps(
            {
                "finnhub": {"error": "quota"},
                "alpha_vantage": {"last_error": "timeout"},
            }
        ),
    }
    fake_summary = {
        "report_data": {
            "tradier_snapshot": {
                "_earnings_discovery_quality": {
                    "items": [
                        {
                            "ticker": "JPM",
                            "earnings_date": "2026-07-18",
                            "earnings_time": "amc",
                            "date_confidence": "single_source",
                            "date_sources": ["finnhub"],
                            "date_conflict": False,
                        },
                        {
                            "ticker": "MS",
                            "earnings_date": "2026-07-19",
                            "earnings_time": "unknown",
                            "date_confidence": "disputed",
                            "date_sources": ["finnhub", "alphavantage"],
                            "date_conflict": True,
                        },
                    ]
                },
                "_forward_factor_strategy": {
                    "items": [
                        {
                            "ticker": "SBUX",
                            "earnings_date": "2026-07-25",
                            "earnings_confidence": "single_source",
                            "earnings_contaminated": True,
                            "date_sources": ["finnhub"],
                        }
                    ]
                },
            }
        }
    }
    with patch("app.services.earnings_trust_service.ReportSnapshotRepository") as repo_cls, \
         patch("app.config.EARNINGS_PROVIDER_ORDER", ["finnhub", "alphavantage"]), \
         patch("app.config.EARNINGS_MERGE_PROVIDER_EVENTS", True), \
         patch("app.config.ALPHA_VANTAGE_API_KEY", "alpha"), \
         patch("app.config.FINNHUB_API_KEY", "finn"):
        repo = repo_cls.return_value
        repo.latest_success.return_value = fake_snapshot
        repo.load_summary.return_value = fake_summary
        from app.services.earnings_trust_service import build_earnings_trust_summary

        result = build_earnings_trust_summary()

    assert result["status"] == "ok"
    assert result["provider_calls_triggered"] is False
    assert result["single_source_count"] >= 1
    assert result["conflict_count"] == 1
    assert result["provider_errors"][0]["provider_name"] in {"finnhub", "alpha_vantage", "alphavantage"}


def test_admin_earnings_trust_endpoint_returns_cached_summary():
    client = _client()
    fake = {
        "status": "ok",
        "checked_at": "2026-07-04T22:00:00+00:00",
        "run_id": "run-cache-1",
        "generated_at": "2026-07-04T21:59:00+00:00",
        "provider_order": ["finnhub", "alphavantage"],
        "merge_provider_events": True,
        "alpha_vantage_configured": True,
        "finnhub_configured": True,
        "total_earnings_events": 3,
        "multi_source_count": 1,
        "single_source_count": 2,
        "conflict_count": 1,
        "unknown_time_count": 1,
        "high_confidence_count": 0,
        "medium_confidence_count": 2,
        "low_confidence_count": 1,
        "top_single_source_rows": [],
        "top_conflict_rows": [],
        "provider_errors": [],
        "provider_calls_triggered": False,
    }
    with patch("app.config.DEV_API_TOKEN", "admin-token"), \
         patch("app.config.RUN_TOKEN", "admin-token"), \
         patch("app.services.earnings_trust_service.build_earnings_trust_summary", return_value=fake):
        resp = client.get("/api/admin/earnings-trust", headers={"Authorization": "Bearer admin-token"})
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["status"] == "ok"
    assert body["provider_calls_triggered"] is False


def test_screener_includes_nav_coverage_trust_and_demo_telemetry():
    client = _client()
    with patch("app.main._load_dashboard_core_report", return_value=_core_data()), \
         patch("app.config.PUBLIC_SCREENER_ENABLED", True), \
         patch("app.config.FORWARD_FACTOR_DRY_RUN", True):
        html = client.get("/screener").get_data(as_text=True)

    assert "Scan Coverage" in html
    assert "Earnings date trust" in html
    assert 'href="#forward-factor"' in html
    assert 'data-demo-nav="forward_factor_calendar"' in html
    assert 'data-demo-copy-link="1"' in html
    assert "/api/telemetry/public-demo" in html
    assert "Forward Factor is being observed in dry-run mode." in html
    assert "Single source" in html
