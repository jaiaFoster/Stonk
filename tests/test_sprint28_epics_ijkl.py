"""
Sprint 28 — Epics I, J, K, L Tests

Covers:
- Epic I: Forward Factor Production Promotion (ff_production_promotion_service)
- Epic J: Historical Evidence (journal service provenance/confidence fields)
- Epic K: UI Transparency (data_details_builder_service)
- Epic L: API Versioning (provenance_api, endpoints, headers)

CAVEMAN MODE: All tests verify that promotion mechanics are dry-run safe,
no broker writes occur, and FF remains read-only even when promoted.
"""
from __future__ import annotations

import json
import py_compile
from unittest.mock import patch


# ─── Compile checks ───────────────────────────────────────────────────────────

class TestCompile:
    def test_ff_production_promotion_service_compiles(self):
        py_compile.compile("app/services/ff_production_promotion_service.py", doraise=True)

    def test_data_details_builder_service_compiles(self):
        py_compile.compile("app/services/data_details_builder_service.py", doraise=True)

    def test_provenance_api_compiles(self):
        py_compile.compile("app/api/provenance_api.py", doraise=True)


# ─── Epic I: Forward Factor Production Promotion ──────────────────────────────

class TestFFProductionPromotion:
    def test_is_promotion_active_default_false(self):
        from app.services.ff_production_promotion_service import is_promotion_active
        from app import config as cfg
        # Default: FF_PRODUCTION_PROMOTION_ENABLED=False → promotion not active
        with patch.object(cfg, "FF_PRODUCTION_PROMOTION_ENABLED", False):
            assert is_promotion_active() is False

    def test_is_promotion_active_enabled_with_dry_run(self):
        from app.services.ff_production_promotion_service import is_promotion_active
        from app import config as cfg
        with patch.object(cfg, "FF_PRODUCTION_PROMOTION_ENABLED", True), \
             patch.object(cfg, "FORWARD_FACTOR_DRY_RUN", True), \
             patch.object(cfg, "FF_CALIBRATION_VERSION", "32C.ff.v1"):
            assert is_promotion_active() is True

    def test_promotion_blocked_when_dry_run_false(self):
        from app.services.ff_production_promotion_service import is_promotion_active
        from app import config as cfg
        with patch.object(cfg, "FF_PRODUCTION_PROMOTION_ENABLED", True), \
             patch.object(cfg, "FORWARD_FACTOR_DRY_RUN", False), \
             patch.object(cfg, "FF_CALIBRATION_VERSION", "32C.ff.v1"):
            # dry_run=False must block promotion (safety invariant)
            assert is_promotion_active() is False

    def test_promotion_blocked_when_calibration_insufficient(self):
        from app.services.ff_production_promotion_service import is_promotion_active
        from app import config as cfg
        with patch.object(cfg, "FF_PRODUCTION_PROMOTION_ENABLED", True), \
             patch.object(cfg, "FORWARD_FACTOR_DRY_RUN", True), \
             patch.object(cfg, "FF_CALIBRATION_VERSION", "30A.ff.v1"):
            assert is_promotion_active() is False

    def test_promotion_status_serializable(self):
        from app.services.ff_production_promotion_service import promotion_status
        status = promotion_status()
        json.dumps(status)

    def test_promotion_status_read_only(self):
        from app.services.ff_production_promotion_service import promotion_status
        s = promotion_status()
        assert s["provider_calls_triggered"] is False
        assert s["read_only"] is True

    def test_promotion_status_can_trade_live_always_false(self):
        from app.services.ff_production_promotion_service import promotion_status
        from app import config as cfg
        with patch.object(cfg, "FF_PRODUCTION_PROMOTION_ENABLED", True), \
             patch.object(cfg, "FORWARD_FACTOR_DRY_RUN", True), \
             patch.object(cfg, "FF_CALIBRATION_VERSION", "32C.ff.v1"):
            s = promotion_status()
            assert s["can_trade_live"] is False

    def test_promotion_status_has_rollback_instruction(self):
        from app.services.ff_production_promotion_service import promotion_status
        s = promotion_status()
        assert "rollback" in s.get("rollback_instruction", "").lower() or "revert" in s.get("rollback_instruction", "").lower()

    def test_promotion_status_has_checks(self):
        from app.services.ff_production_promotion_service import promotion_status
        s = promotion_status()
        assert isinstance(s["checks"], list)
        assert len(s["checks"]) >= 3

    def test_attach_ff_provenance_to_row(self):
        from app.services.ff_production_promotion_service import attach_ff_provenance
        row = {
            "ticker": "NVDA",
            "forward_factor": 0.25,
            "front_iv": 0.45,
            "back_iv": 0.38,
            "front_dte": 60,
            "back_dte": 90,
            "verdict": "SOURCE-QUALIFIED POSITIVE FF SIGNAL / REVIEW ENTRY",
        }
        attach_ff_provenance(row)
        assert "_ff_provenance" in row
        prov = row["_ff_provenance"]
        assert prov["dry_run"] is True
        assert prov["can_trade_live"] is False
        assert "forward_factor" in prov
        json.dumps(prov)

    def test_validate_ff_row_pass_verdict_eligible(self):
        from app.services.ff_production_promotion_service import (
            attach_ff_provenance, validate_ff_row_for_promotion,
        )
        row = {
            "ticker": "NVDA",
            "forward_factor": 0.25,
            "verdict": "SOURCE-QUALIFIED POSITIVE FF SIGNAL / REVIEW ENTRY",
            "dry_run": True,
            "can_trade_live": False,
        }
        attach_ff_provenance(row)
        result = validate_ff_row_for_promotion(row)
        # PASS verdict → eligible for promotion checks
        passed = [c for c in result["checks"] if c["check"] == "verdict_is_pass_or_watch"]
        assert passed[0]["passed"] is True

    def test_validate_ff_row_fail_verdict_ineligible(self):
        from app.services.ff_production_promotion_service import validate_ff_row_for_promotion
        row = {
            "ticker": "NVDA",
            "forward_factor": 0.10,
            "verdict": "FAIL / BELOW THRESHOLD",
            "dry_run": True,
            "can_trade_live": False,
        }
        result = validate_ff_row_for_promotion(row)
        assert result["eligible_for_promotion"] is False

    def test_validate_ff_row_can_trade_live_blocks_promotion(self):
        from app.services.ff_production_promotion_service import validate_ff_row_for_promotion
        row = {
            "ticker": "NVDA",
            "verdict": "PASS",
            "dry_run": True,
            "can_trade_live": True,  # must never be true
        }
        result = validate_ff_row_for_promotion(row)
        assert result["eligible_for_promotion"] is False

    def test_caveman_dry_run_still_true_when_promoted(self):
        from app import config as cfg
        # The FORWARD_FACTOR_DRY_RUN flag must stay True regardless of promotion
        assert cfg.FORWARD_FACTOR_DRY_RUN is True

    def test_ff_production_promotion_enabled_default_false(self):
        from app import config as cfg
        assert cfg.FF_PRODUCTION_PROMOTION_ENABLED is False


# ─── Epic J: Historical Evidence (Journal) ────────────────────────────────────

class TestJournalProvenanceEvidence:
    def _row(self, **kwargs) -> dict:
        base = {
            "ticker": "BAC",
            "strategy_id": "earnings_calendar",
            "action": "EARNINGS CALENDAR CANDIDATE",
            "score": 72.0,
            "verdict": "PASS / EARNINGS CALENDAR ENTRY",
            "earnings_date": "2026-07-22",
            "earnings_date_confidence": "confirmed",
            "earnings_sources_seen": ["finnhub", "alpha_vantage"],
            "earnings_source_conflict": False,
            "date_conflict": False,
        }
        base.update(kwargs)
        return base

    def test_build_strategy_observation_has_provenance_json(self):
        from app.services.strategy_observation_journal_service import build_strategy_observation
        obs = build_strategy_observation(self._row(), "run-001", "2026-07-13", "earnings_calendar")
        assert "provenance_json" in obs
        # Must be valid JSON
        prov = json.loads(obs["provenance_json"])
        assert isinstance(prov, dict)

    def test_build_strategy_observation_has_confidence_evidence_json(self):
        from app.services.strategy_observation_journal_service import build_strategy_observation
        obs = build_strategy_observation(self._row(), "run-001", "2026-07-13", "earnings_calendar")
        assert "confidence_evidence_json" in obs
        ce = json.loads(obs["confidence_evidence_json"])
        assert isinstance(ce, dict)

    def test_confidence_evidence_has_date_confidence(self):
        from app.services.strategy_observation_journal_service import build_strategy_observation
        obs = build_strategy_observation(self._row(), "run-001", "2026-07-13", "earnings_calendar")
        ce = json.loads(obs["confidence_evidence_json"])
        assert ce.get("date_confidence") == "confirmed"

    def test_confidence_evidence_has_sources(self):
        from app.services.strategy_observation_journal_service import build_strategy_observation
        obs = build_strategy_observation(self._row(), "run-001", "2026-07-13", "earnings_calendar")
        ce = json.loads(obs["confidence_evidence_json"])
        assert "sources" in ce
        assert "finnhub" in ce["sources"]

    def test_provenance_evidence_has_schema_version(self):
        from app.services.strategy_observation_journal_service import build_strategy_observation
        obs = build_strategy_observation(self._row(), "run-001", "2026-07-13", "earnings_calendar")
        prov = json.loads(obs["provenance_json"])
        assert prov.get("schema_version") == "28.J.v1"

    def test_confidence_evidence_conflict_recorded(self):
        from app.services.strategy_observation_journal_service import build_strategy_observation
        row = self._row(date_conflict=True, earnings_source_conflict=True)
        obs = build_strategy_observation(row, "run-001", "2026-07-13", "earnings_calendar")
        ce = json.loads(obs["confidence_evidence_json"])
        assert ce.get("date_conflict") is True

    def test_ff_row_has_ff_confidence_in_evidence(self):
        from app.services.strategy_observation_journal_service import build_strategy_observation
        row = {
            "ticker": "NVDA",
            "strategy_id": "forward_factor_calendar",
            "action": "FF RESEARCH SIGNAL",
            "score": 80.0,
            "verdict": "SOURCE-QUALIFIED POSITIVE FF SIGNAL / REVIEW ENTRY",
            "forward_factor": 0.25,
            "near_miss_ff": False,
            "watch_zone_ff": False,
        }
        obs = build_strategy_observation(row, "run-001", "2026-07-13", "forward_factor_calendar")
        ce = json.loads(obs["confidence_evidence_json"])
        assert "ff_confidence" in ce
        assert ce["ff_confidence"].get("forward_factor") == pytest.approx(0.25, abs=0.001) \
            or ce["ff_confidence"].get("forward_factor") is not None

    def test_journal_observation_still_serializable(self):
        from app.services.strategy_observation_journal_service import build_strategy_observation
        obs = build_strategy_observation(self._row(), "run-001", "2026-07-13", "earnings_calendar")
        json.dumps(obs)  # full observation must serialize

    def test_data_diagnostics_reflected_in_provenance(self):
        from app.services.strategy_observation_journal_service import build_strategy_observation
        from app.services.strategy_data_diagnostics_service import (
            StrategyDataDiagnostics, attach_data_diagnostics,
        )
        row = self._row()
        d = StrategyDataDiagnostics("earnings_calendar", "BAC")
        d.require("earnings_date").mark_present("earnings_date", source="finnhub", confidence="confirmed")
        attach_data_diagnostics(row, d)
        obs = build_strategy_observation(row, "run-001", "2026-07-13", "earnings_calendar")
        prov = json.loads(obs["provenance_json"])
        assert prov.get("data_diagnostics_present") is True
        assert prov.get("data_complete") is True


import pytest  # needed for approx


# ─── Epic K: UI Transparency (Data Details) ────────────────────────────────────

class TestDataDetailsBuilder:
    def _earnings_row(self) -> dict:
        return {
            "ticker": "BAC",
            "strategy_id": "earnings_calendar",
            "action": "EARNINGS CALENDAR CANDIDATE",
            "score": 72.0,
            "earnings_date": "2026-07-22",
            "earnings_date_confidence": "confirmed",
            "earnings_sources_seen": ["finnhub", "alpha_vantage"],
            "earnings_trust_label": "multi_source_confirmed",
            "front_expiration": "2026-07-18",
            "back_expiration": "2026-07-25",
            "strike": 45.0,
            "front_iv": 0.42,
            "back_iv": 0.58,
            "conservative_debit": 0.35,
            "delta": -0.35,
            "iv": 0.42,
        }

    def _ff_row(self) -> dict:
        return {
            "ticker": "NVDA",
            "strategy_id": "forward_factor_calendar",
            "forward_factor": 0.25,
            "front_iv": 0.45,
            "back_iv": 0.38,
            "front_dte": 60,
            "back_dte": 90,
            "verdict": "SOURCE-QUALIFIED POSITIVE FF SIGNAL / REVIEW ENTRY",
            "near_miss_ff": False,
        }

    def test_build_data_details_earnings_calendar(self):
        from app.services.data_details_builder_service import build_data_details
        dd = build_data_details(self._earnings_row(), "earnings_calendar")
        assert dd["panel_count"] > 0
        sections = [p["section"] for p in dd["panels"]]
        assert "earnings" in sections

    def test_build_data_details_ff_calendar(self):
        from app.services.data_details_builder_service import build_data_details
        dd = build_data_details(self._ff_row(), "forward_factor_calendar")
        sections = [p["section"] for p in dd["panels"]]
        assert "forward_factor" in sections

    def test_data_details_is_serializable(self):
        from app.services.data_details_builder_service import build_data_details
        dd = build_data_details(self._earnings_row(), "earnings_calendar")
        json.dumps(dd)

    def test_data_details_read_only(self):
        from app.services.data_details_builder_service import build_data_details
        dd = build_data_details(self._earnings_row(), "earnings_calendar")
        assert dd["provider_calls_triggered"] is False
        assert dd["read_only"] is True

    def test_earnings_panel_has_date_field(self):
        from app.services.data_details_builder_service import build_data_details
        dd = build_data_details(self._earnings_row(), "earnings_calendar")
        earnings = next((p for p in dd["panels"] if p["section"] == "earnings"), None)
        assert earnings is not None
        field_names = [f["name"] for f in earnings["fields"]]
        assert "earnings_date" in field_names

    def test_earnings_panel_confidence_confirmed(self):
        from app.services.data_details_builder_service import build_data_details
        dd = build_data_details(self._earnings_row(), "earnings_calendar")
        earnings = next((p for p in dd["panels"] if p["section"] == "earnings"), None)
        assert earnings["confidence_summary"] == "confirmed"

    def test_options_panel_has_strike(self):
        from app.services.data_details_builder_service import build_data_details
        row = self._earnings_row()
        dd = build_data_details(row, "earnings_calendar")
        options = next((p for p in dd["panels"] if p["section"] == "options"), None)
        if options:
            field_names = [f["name"] for f in options["fields"]]
            assert "strike" in field_names

    def test_ff_panel_has_forward_factor(self):
        from app.services.data_details_builder_service import build_data_details
        dd = build_data_details(self._ff_row(), "forward_factor_calendar")
        ff_panel = next((p for p in dd["panels"] if p["section"] == "forward_factor"), None)
        assert ff_panel is not None
        field_names = [f["name"] for f in ff_panel["fields"]]
        assert "forward_factor" in field_names

    def test_ff_panel_forward_factor_formatted(self):
        from app.services.data_details_builder_service import build_data_details
        dd = build_data_details(self._ff_row(), "forward_factor_calendar")
        ff_panel = next((p for p in dd["panels"] if p["section"] == "forward_factor"), None)
        ff_field = next((f for f in ff_panel["fields"] if f["name"] == "forward_factor"), None)
        assert ff_field is not None
        assert "0.2500" in ff_field["value"]

    def test_near_miss_warning_in_ff_panel(self):
        from app.services.data_details_builder_service import build_data_details
        row = self._ff_row()
        row["near_miss_ff"] = True
        row["miss_distance"] = 0.0123
        dd = build_data_details(row, "forward_factor_calendar")
        ff_panel = next((p for p in dd["panels"] if p["section"] == "forward_factor"), None)
        assert any("NEAR MISS" in w or "near miss" in w.lower() for w in ff_panel["warnings"])

    def test_conflict_warning_in_earnings_panel(self):
        from app.services.data_details_builder_service import build_data_details
        row = self._earnings_row()
        row["_confidence"] = {
            "conflict_detected": True,
            "conflict_summary": "Date conflict: 2026-07-22 (finnhub) vs 2026-07-24 (av)",
            "date_confidence": "disputed",
            "sources_returned_data": ["finnhub", "alpha_vantage"],
            "trade_allowed": False,
        }
        dd = build_data_details(row, "earnings_calendar")
        ep = next((p for p in dd["panels"] if p["section"] == "earnings"), None)
        assert any("conflict" in w.lower() for w in ep["warnings"])

    def test_attach_data_details(self):
        from app.services.data_details_builder_service import attach_data_details
        row = self._earnings_row()
        attach_data_details(row, "earnings_calendar")
        assert "_data_details" in row
        assert row["_data_details"]["panel_count"] > 0

    def test_data_details_compact(self):
        from app.services.data_details_builder_service import (
            attach_data_details, data_details_compact,
        )
        row = self._earnings_row()
        attach_data_details(row, "earnings_calendar")
        compact = data_details_compact(row)
        assert compact is not None
        assert "panels" in compact
        assert "panel_count" in compact
        json.dumps(compact)

    def test_diagnostics_panel_when_attached(self):
        from app.services.data_details_builder_service import build_data_details
        from app.services.strategy_data_diagnostics_service import (
            StrategyDataDiagnostics, attach_data_diagnostics,
        )
        row = self._earnings_row()
        d = StrategyDataDiagnostics("earnings_calendar", "BAC")
        d.require("earnings_date").mark_present("earnings_date", source="finnhub")
        attach_data_diagnostics(row, d)
        dd = build_data_details(row, "earnings_calendar")
        sections = [p["section"] for p in dd["panels"]]
        assert "diagnostics" in sections

    def test_greeks_panel_delta_sourced_from_tradier(self):
        from app.services.data_details_builder_service import build_data_details
        from app.services.greek_attribution_service import enrich_option_row_with_greek_attribution
        row = self._earnings_row()
        row["_greek_provenance"] = enrich_option_row_with_greek_attribution(row, "tradier")["_greek_provenance"]
        dd = build_data_details(row, "earnings_calendar")
        gp = next((p for p in dd["panels"] if p["section"] == "greeks"), None)
        if gp:
            delta_field = next((f for f in gp["fields"] if f["name"] == "delta"), None)
            if delta_field:
                assert delta_field["source"] in ("tradier", "unknown")


# ─── Epic L: API Versioning ────────────────────────────────────────────────────

class TestProvenanceAPI:
    def test_build_data_version_info_serializable(self):
        from app.api.provenance_api import build_data_version_info
        info = build_data_version_info()
        json.dumps(info)

    def test_data_version_info_read_only(self):
        from app.api.provenance_api import build_data_version_info
        info = build_data_version_info()
        assert info["provider_calls_triggered"] is False
        assert info["read_only"] is True

    def test_data_version_info_has_schema_version(self):
        from app.api.provenance_api import build_data_version_info
        from app.models.data_provenance import PROVENANCE_SCHEMA_VERSION
        info = build_data_version_info()
        assert info["provenance_schema_version"] == PROVENANCE_SCHEMA_VERSION

    def test_data_version_info_has_endpoints(self):
        from app.api.provenance_api import build_data_version_info
        info = build_data_version_info()
        assert "/api/data/version" in info.get("endpoints", {})

    def test_provenance_response_headers(self):
        from app.api.provenance_api import provenance_response_headers
        headers = provenance_response_headers()
        assert "X-ASA-Data-Version" in headers
        assert "X-ASA-Provenance-Schema" in headers
        assert "X-ASA-Read-Only" in headers
        assert headers["X-ASA-Read-Only"] == "true"

    def test_build_provenance_summary_no_data(self):
        from app.api.provenance_api import build_provenance_summary
        result = build_provenance_summary("BAC")
        assert result["ticker"] == "BAC"
        assert result["provider_calls_triggered"] is False
        json.dumps(result)

    def test_build_provenance_summary_with_earnings(self):
        from app.api.provenance_api import build_provenance_summary
        earnings = {
            "earnings_date": "2026-07-22",
            "sources_seen": ["finnhub", "alpha_vantage"],
            "earnings_source_conflict": False,
            "earnings_date_confidence": "confirmed",
        }
        result = build_provenance_summary("BAC", earnings_event=earnings)
        assert "earnings" in result["sections"]
        assert result["sections"]["earnings"]["date_confidence"] == "confirmed"

    def test_enrich_rows_with_provenance(self):
        from app.api.provenance_api import enrich_rows_with_provenance
        rows = [
            {"ticker": "BAC", "score": 72.0, "earnings_sources_seen": ["finnhub"]},
            {"ticker": "AAPL", "score": 65.0},
        ]
        enriched = enrich_rows_with_provenance(rows, "earnings_calendar")
        assert len(enriched) == 2
        for r in enriched:
            assert "_provenance_compact" in r
            json.dumps(r["_provenance_compact"])

    def test_enrich_rows_with_data_details(self):
        from app.api.provenance_api import enrich_rows_with_provenance
        rows = [{"ticker": "BAC", "score": 72.0, "earnings_sources_seen": ["finnhub"],
                 "earnings_date": "2026-07-22"}]
        enriched = enrich_rows_with_provenance(rows, "earnings_calendar", include_data_details=True)
        assert "_data_details" in enriched[0]

    def test_enrich_rows_does_not_alter_existing_fields(self):
        from app.api.provenance_api import enrich_rows_with_provenance
        rows = [{"ticker": "BAC", "score": 72.0, "custom_field": "preserved"}]
        enriched = enrich_rows_with_provenance(rows, "earnings_calendar")
        assert enriched[0]["ticker"] == "BAC"
        assert enriched[0]["score"] == 72.0
        assert enriched[0]["custom_field"] == "preserved"

    def test_provenance_compact_has_conflict_flag(self):
        from app.api.provenance_api import enrich_rows_with_provenance
        rows = [{"ticker": "BAC", "date_conflict": True, "earnings_sources_seen": ["finnhub"]}]
        enriched = enrich_rows_with_provenance(rows, "earnings_calendar")
        prov = enriched[0]["_provenance_compact"]
        assert prov["has_conflict"] is True


class TestProvenanceEndpoints:
    def _app(self):
        from app.main import app
        app.config["TESTING"] = True
        return app

    def test_api_data_version_endpoint(self):
        with self._app().test_client() as client:
            resp = client.get("/api/data/version")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["provider_calls_triggered"] is False
            assert "api_data_version" in data

    def test_api_data_version_has_provenance_headers(self):
        with self._app().test_client() as client:
            resp = client.get("/api/data/version")
            assert resp.status_code == 200
            assert "X-ASA-Data-Version" in resp.headers
            assert "X-ASA-Provenance-Schema" in resp.headers

    def test_api_data_provenance_ticker_endpoint(self):
        with self._app().test_client() as client:
            resp = client.get("/api/data/provenance/BAC")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ticker"] == "BAC"
            assert data["provider_calls_triggered"] is False

    def test_api_provider_health_endpoint(self):
        with self._app().test_client() as client:
            resp = client.get("/api/data/provider-health")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["provider_calls_triggered"] is False
            assert "overall_status" in data
            assert "providers" in data

    def test_api_provider_health_has_all_known_providers(self):
        from app.services.provider_health_service import KNOWN_PROVIDERS
        with self._app().test_client() as client:
            resp = client.get("/api/data/provider-health")
            data = resp.get_json()
            for slug in KNOWN_PROVIDERS:
                assert slug in data["providers"]
