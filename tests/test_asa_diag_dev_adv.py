"""Tests for TKT-DIAG-01, TKT-DEV-001, TKT-DEV-002, TKT-ADV-029."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# TKT-DIAG-01 — prescreen_stats stored in quality filter result
# ---------------------------------------------------------------------------

class TestPrescreenStats:
    def test_prescreen_stats_present_in_result(self):
        from app.services.earnings_discovery_quality_service import (
            filter_earnings_discovery_for_calendar_scan,
        )

        discovery = {"items": [{"ticker": "AAPL"}, {"ticker": "ZZZZ"}]}

        with patch("app.config.EARNINGS_DISCOVERY_CONSTITUENT_PRESCREEN", True), \
             patch("app.services.universe_discovery_service.get_constituent_ticker_set",
                   return_value={"AAPL"}), \
             patch("app.services.earnings_discovery_quality_service._merge_universe_discovery",
                   side_effect=lambda items, *a, **k: items), \
             patch("app.services.earnings_discovery_quality_service.TradierProvider") as MockT:
            MockT.return_value.is_configured = False
            result = filter_earnings_discovery_for_calendar_scan(
                discovery, log_print=lambda msg: None
            )

        stats = result.get("_prescreen_stats") or {}
        assert stats["removed_count"] == 1
        assert stats["raw_count"] == 2
        assert stats["post_count"] == 1
        assert stats["removed_pct"] == 50.0
        assert "ZZZZ" in stats["removed_tickers"]
        assert stats["fail_open"] is False

    def test_prescreen_stats_fail_open_when_cache_unavailable(self):
        from app.services.earnings_discovery_quality_service import (
            filter_earnings_discovery_for_calendar_scan,
        )

        discovery = {"items": [{"ticker": "AAPL"}]}

        with patch("app.config.EARNINGS_DISCOVERY_CONSTITUENT_PRESCREEN", True), \
             patch("app.services.universe_discovery_service.get_constituent_ticker_set",
                   side_effect=RuntimeError("cache down")), \
             patch("app.services.earnings_discovery_quality_service._merge_universe_discovery",
                   side_effect=lambda items, *a, **k: items), \
             patch("app.services.earnings_discovery_quality_service.TradierProvider") as MockT:
            MockT.return_value.is_configured = False
            result = filter_earnings_discovery_for_calendar_scan(
                discovery, log_print=lambda msg: None
            )

        stats = result.get("_prescreen_stats") or {}
        assert stats.get("fail_open") is True

    def test_prescreen_stats_cache_size_recorded(self):
        from app.services.earnings_discovery_quality_service import (
            filter_earnings_discovery_for_calendar_scan,
        )

        discovery = {"items": [{"ticker": "AAPL"}]}
        fake_set = {"AAPL", "MSFT", "GOOG"}

        with patch("app.config.EARNINGS_DISCOVERY_CONSTITUENT_PRESCREEN", True), \
             patch("app.services.universe_discovery_service.get_constituent_ticker_set",
                   return_value=fake_set), \
             patch("app.services.earnings_discovery_quality_service._merge_universe_discovery",
                   side_effect=lambda items, *a, **k: items), \
             patch("app.services.earnings_discovery_quality_service.TradierProvider") as MockT:
            MockT.return_value.is_configured = False
            result = filter_earnings_discovery_for_calendar_scan(
                discovery, log_print=lambda msg: None
            )

        assert result["_prescreen_stats"]["cache_size"] == 3

    def test_warning_logs_when_constituent_cache_undersized(self):
        from app.services.earnings_discovery_quality_service import (
            filter_earnings_discovery_for_calendar_scan,
        )

        tickers = [f"T{i}" for i in range(9)]
        discovery = {"items": [{"ticker": t} for t in tickers]}
        logged: list[str] = []

        with patch("app.config.EARNINGS_DISCOVERY_CONSTITUENT_PRESCREEN", True), \
             patch("app.services.universe_discovery_service.get_constituent_ticker_set",
                   return_value={"T0"}), \
             patch("app.services.earnings_discovery_quality_service._merge_universe_discovery",
                   side_effect=lambda items, *a, **k: items), \
             patch("app.services.earnings_discovery_quality_service.TradierProvider") as MockT:
            MockT.return_value.is_configured = False
            filter_earnings_discovery_for_calendar_scan(
                discovery, log_print=logged.append
            )

        assert any("WARNING" in msg for msg in logged)

    def test_high_removal_alone_does_not_log_warning_when_cache_healthy(self):
        from app.services.earnings_discovery_quality_service import (
            filter_earnings_discovery_for_calendar_scan,
        )

        tickers = [f"T{i}" for i in range(9)]
        discovery = {"items": [{"ticker": t} for t in tickers]}
        logged: list[str] = []
        healthy_cache = {f"T{i}" for i in range(664)}

        with patch("app.config.EARNINGS_DISCOVERY_CONSTITUENT_PRESCREEN", True), \
             patch("app.services.universe_discovery_service.get_constituent_ticker_set",
                   return_value=healthy_cache), \
             patch("app.services.earnings_discovery_quality_service._merge_universe_discovery",
                   side_effect=lambda items, *a, **k: items), \
             patch("app.services.earnings_discovery_quality_service.TradierProvider") as MockT:
            MockT.return_value.is_configured = False
            filter_earnings_discovery_for_calendar_scan(
                {"items": [{"ticker": "T0"}] + [{"ticker": f"MISS{i}"} for i in range(8)]},
                log_print=logged.append
            )

        assert not any("possible stale cache" in msg for msg in logged)


# ---------------------------------------------------------------------------
# TKT-DIAG-01 — _reconstruct_calendar_lifecycle helper
# ---------------------------------------------------------------------------

class TestReconstructCalendarLifecycle:
    def _call(self, ticker, engine_row, quality_row):
        from app.main import _reconstruct_calendar_lifecycle
        return _reconstruct_calendar_lifecycle(ticker, engine_row, quality_row)

    def test_all_pass_when_full_data_present(self):
        quality_row = {
            "passes_precheck": True,
            "front_expiration": "2026-07-10",
            "back_expiration": "2026-08-07",
            "expiration_pair": {"front": "2026-07-10", "back": "2026-08-07"},
        }
        engine_row = {
            "verdict": "PASS / POSSIBLE ENTRY SETUP",
            "possible_spread": {"short_expiration": "2026-07-10", "long_expiration": "2026-08-07"},
        }

        result = self._call("AAPL", engine_row, quality_row)

        stages = result["stages"]
        assert stages["quality_precheck"] == "PASS"
        assert stages["front_back_expirations_found"] == "PASS"
        assert stages["expiration_pair_dict_stored"] == "PASS"
        assert stages["trade_engine_received_pair"] == "PASS"
        assert stages["structure_built"] == "PASS"
        assert stages["final_verdict"].startswith("PASS")

    def test_expiration_pair_dict_fail_when_front_back_present_but_no_dict(self):
        """This is the diagnostic for TKT-ADV-006 bug — expirations found but dict not stored."""
        quality_row = {
            "passes_precheck": True,
            "front_expiration": "2026-07-10",
            "back_expiration": "2026-08-07",
            # expiration_pair key missing — the bug
        }
        engine_row = {"verdict": "FAIL / NO VALID CALENDAR STRUCTURE"}

        result = self._call("CAG", engine_row, quality_row)

        stages = result["stages"]
        assert stages["front_back_expirations_found"] == "PASS"
        assert stages["expiration_pair_dict_stored"] == "FAIL"
        assert stages["trade_engine_received_pair"] == "FAIL"

    def test_skipped_when_quality_failed(self):
        quality_row = {
            "passes_precheck": False,
            "front_expiration": None,
            "back_expiration": None,
        }
        engine_row = {}

        result = self._call("ILLIQUID", engine_row, quality_row)

        stages = result["stages"]
        assert stages["quality_precheck"] == "FAIL"
        assert stages["front_back_expirations_found"] == "SKIPPED"

    def test_front_before_earnings_computed(self):
        quality_row = {
            "passes_precheck": True,
            "front_expiration": "2026-07-10",
            "back_expiration": "2026-08-07",
            "event": {"earnings_date": "2026-07-11"},
        }

        result = self._call("JPM", {}, quality_row)

        assert result["expiration_debug"]["front_before_earnings"] is True


# ---------------------------------------------------------------------------
# TKT-DEV-001 — dev trigger-run endpoint
# ---------------------------------------------------------------------------

class TestTriggerRunEndpoint:
    def _client(self):
        from app.main import app
        app.config["TESTING"] = True
        return app.test_client()

    def test_trigger_run_requires_valid_token(self):
        client = self._client()
        with patch("app.config.DEV_API_TOKEN", "secret"), \
             patch("app.config.ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True):
            resp = client.post("/api/dev/trigger-run?token=wrong")
        assert resp.status_code == 403

    def test_trigger_run_returns_run_id(self):
        client = self._client()
        with patch("app.config.DEV_API_TOKEN", "secret"), \
             patch("app.config.ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
             patch("app.main._run_job"), \
             patch("app.main.RUN_LOCK") as mock_lock:
            mock_lock.acquire.return_value = True
            resp = client.post("/api/dev/trigger-run?token=secret&mode=dev")
        # May return 202 triggered or 202 already_running depending on lock state
        assert resp.status_code == 202
        data = resp.get_json()
        assert data is not None
        assert "run_id" in data or "status" in data

    def test_trigger_run_rejects_invalid_mode(self):
        client = self._client()
        with patch("app.config.DEV_API_TOKEN", "secret"), \
             patch("app.config.ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True):
            resp = client.post("/api/dev/trigger-run?token=secret&mode=chaos")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# TKT-DEV-001 — last_run_age_seconds in dev status
# ---------------------------------------------------------------------------

class TestLastRunAgeDev:
    def test_enrich_latest_run_adds_age(self):
        from app.services.app_diagnostics_service import _enrich_latest_run
        from datetime import datetime, timezone, timedelta

        completed = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        result = _enrich_latest_run({"run_id": "abc", "completed_at": completed})

        assert result is not None
        assert result["last_run_age_seconds"] is not None
        # Should be close to 7200 seconds ±60
        assert 7000 < result["last_run_age_seconds"] < 7400
        assert result["last_run_is_stale"] is False

    def test_enrich_returns_stale_when_old(self):
        from app.services.app_diagnostics_service import _enrich_latest_run
        from datetime import datetime, timezone, timedelta

        completed = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        result = _enrich_latest_run({"run_id": "abc", "completed_at": completed})

        assert result["last_run_is_stale"] is True

    def test_enrich_returns_none_on_missing_completed_at(self):
        from app.services.app_diagnostics_service import _enrich_latest_run

        result = _enrich_latest_run({"run_id": "abc"})

        assert result["last_run_age_seconds"] is None
        assert result["last_run_is_stale"] is None

    def test_enrich_returns_none_on_none_input(self):
        from app.services.app_diagnostics_service import _enrich_latest_run

        assert _enrich_latest_run(None) is None


# ---------------------------------------------------------------------------
# TKT-DEV-002 — broker_auth_status in run manifest
# ---------------------------------------------------------------------------

class TestBrokerAuthStatus:
    def _call(self, provider_status):
        from app.services.run_manifest_repository import _broker_auth_status
        return _broker_auth_status(provider_status)

    def test_ok_when_success_true(self):
        assert self._call({"robinhood": {"success": True, "status": "ok"}}) == "OK"

    def test_expired_when_auth_required(self):
        assert self._call({"robinhood": {"success": False, "status": "auth_required"}}) == "EXPIRED"

    def test_expired_when_auth_failed(self):
        assert self._call({"robinhood": {"success": False, "status": "auth_failed"}}) == "EXPIRED"

    def test_expired_when_auth_timeout(self):
        assert self._call({"robinhood": {"success": False, "status": "auth_timeout"}}) == "EXPIRED"

    def test_rate_limited_status(self):
        assert self._call({"robinhood": {"success": False, "status": "rate_limited"}}) == "RATE_LIMITED"

    def test_unknown_when_no_robinhood_key(self):
        assert self._call({}) == "UNKNOWN"

    def test_unknown_when_provider_status_none(self):
        assert self._call(None) == "UNKNOWN"

    def test_broker_auth_status_in_manifest(self):
        from app.services.run_manifest_repository import build_run_manifest

        manifest = build_run_manifest(
            run_id="test-run",
            mode="dev",
            status="SUCCESS_COMPLETE",
            report_quality="SUCCESS_COMPLETE",
            runtime_profile={},
            payload_profile={},
            pipeline_status={"errors": []},
            strategy_results={},
            daily_opportunity={},
            provider_status={"robinhood": {"success": True, "status": "ok"}},
        )

        assert "broker_auth_status" in manifest
        assert manifest["broker_auth_status"] == "OK"

    def test_manifest_broker_auth_expired_sets_message(self):
        from app.services.run_manifest_repository import build_run_manifest

        manifest = build_run_manifest(
            run_id="test-run",
            mode="dev",
            status="SUCCESS_DEGRADED",
            report_quality="SUCCESS_DEGRADED",
            runtime_profile={},
            payload_profile={},
            pipeline_status={"errors": []},
            strategy_results={},
            daily_opportunity={},
            provider_status={"robinhood": {"success": False, "status": "auth_required"}},
        )

        assert manifest["broker_auth_status"] == "EXPIRED"
        assert manifest["broker_auth_message"] is not None
        assert "re-approv" in (manifest["broker_auth_message"] or "").lower()


# ---------------------------------------------------------------------------
# TKT-ADV-029 — brief_format in agent-prompt
# ---------------------------------------------------------------------------

class TestBriefFormatAgentPrompt:
    def _get_response_payload(self):
        """Call the agent-prompt view, passing the legacy dev token so require_auth passes."""
        from app.main import app as flask_app
        flask_app.config["TESTING"] = True
        with patch("app.config.DEV_API_TOKEN", "test_dev_token"), \
             patch("app.config.RUN_TOKEN", "test_dev_token"):
            with flask_app.test_client() as client:
                resp = client.get(
                    "/api/advisor/knowledge/agent-prompt",
                    headers={"Authorization": "Bearer test_dev_token"},
                )
                return resp.get_json()

    def test_brief_format_present(self):
        data = self._get_response_payload()
        assert "brief_format" in data

    def test_brief_format_default_is_prose(self):
        data = self._get_response_payload()
        assert data["brief_format"]["default"] == "prose"

    def test_position_tables_on_request_only(self):
        data = self._get_response_payload()
        assert data["brief_format"]["position_tables"] == "on_request_only"

    def test_brief_format_has_rules(self):
        data = self._get_response_payload()
        rules = data["brief_format"]["rules"]
        assert isinstance(rules, list)
        assert len(rules) >= 5
        assert any("table" in rule.lower() for rule in rules)

    def test_target_duration_under_3_minutes(self):
        data = self._get_response_payload()
        assert "3 minutes" in data["brief_format"]["target_duration"]
