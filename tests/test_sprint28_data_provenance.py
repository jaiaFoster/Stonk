"""
Sprint 28 — Data Confidence & Provenance Tests

Covers:
- Epic A: Universal Provenance Model (DataProvenanceRecord, EarningsProvenance, GreekProvenance, ChainDataProvenance)
- Epic B: Earnings Confidence Service (build_earnings_confidence_report, bulk, enrich)
- Epic C: Provider Conflict Service (earnings date conflict, numeric conflict, resolution)
- Epic D: Chain Accuracy Audit (leg audit, two-leg spread audit, pipeline summary)
- Epic E: Greek Source Attribution (build_greek_attribution_report, compact_greek_sources)
- Epic F: Freshness annotations (annotate_freshness, freshness_age_seconds, freshness_label)
- Epic G: Automated Data Validation (earnings schema, option leg, cross-provider, strategy row)
- Epic H: Strategy Data Diagnostics (StrategyDataDiagnostics, attach, summary)
- Epic M: Provider Health Service (build from manifest, health summary, classify)
"""
from __future__ import annotations

import json
import py_compile
from datetime import datetime, timezone, timedelta


# ─── Compile checks ───────────────────────────────────────────────────────────

class TestCompile:
    def test_data_provenance_model_compiles(self):
        py_compile.compile("app/models/data_provenance.py", doraise=True)

    def test_data_provenance_service_compiles(self):
        py_compile.compile("app/services/data_provenance_service.py", doraise=True)

    def test_earnings_confidence_service_compiles(self):
        py_compile.compile("app/services/earnings_confidence_service.py", doraise=True)

    def test_provider_conflict_service_compiles(self):
        py_compile.compile("app/services/provider_conflict_service.py", doraise=True)

    def test_chain_accuracy_service_compiles(self):
        py_compile.compile("app/services/chain_accuracy_service.py", doraise=True)

    def test_greek_attribution_service_compiles(self):
        py_compile.compile("app/services/greek_attribution_service.py", doraise=True)

    def test_strategy_data_diagnostics_service_compiles(self):
        py_compile.compile("app/services/strategy_data_diagnostics_service.py", doraise=True)

    def test_automated_data_validation_service_compiles(self):
        py_compile.compile("app/services/automated_data_validation_service.py", doraise=True)

    def test_provider_health_service_compiles(self):
        py_compile.compile("app/services/provider_health_service.py", doraise=True)


# ─── Epic A: Universal Provenance Model ──────────────────────────────────────

class TestDataProvenanceModel:
    def _record(self):
        from app.models.data_provenance import DataProvenanceRecord
        return DataProvenanceRecord

    def test_default_record_is_unknown(self):
        cls = self._record()
        r = cls()
        assert r.source == "unknown"
        assert r.confidence == "no_data"

    def test_to_dict_is_serializable(self):
        cls = self._record()
        r = cls.single_source("tradier", "2026-07-13T10:00:00+00:00")
        d = r.to_dict()
        json.dumps(d)  # must not raise
        assert d["source"] == "tradier"
        assert d["confidence"] == "single_source"

    def test_from_dict_round_trip(self):
        cls = self._record()
        r = cls.multi_source_confirmed("finnhub", ["finnhub", "alpha_vantage"])
        d = r.to_dict()
        r2 = cls.from_dict(d)
        assert r2.confidence == r.confidence
        assert r2.source == r.source

    def test_multi_source_confirmed(self):
        cls = self._record()
        r = cls.multi_source_confirmed("finnhub", ["finnhub", "alpha_vantage"])
        assert r.confidence == "confirmed"
        assert r.source == "finnhub"
        assert len(r.alternatives) == 1
        assert r.alternatives[0]["source"] == "alpha_vantage"

    def test_disputed(self):
        cls = self._record()
        details = [{"source": "finnhub", "value": "2026-07-22"}, {"source": "av", "value": "2026-07-23"}]
        r = cls.disputed(details)
        assert r.confidence == "disputed"
        assert r.conflict_detected is True
        assert len(r.conflict_details) == 2

    def test_calculated(self):
        cls = self._record()
        r = cls.calculated("black_scholes", ["front_iv", "back_iv"])
        assert r.source == "calculated"
        assert "black_scholes" in r.selection_reason

    def test_unavailable(self):
        cls = self._record()
        r = cls.unavailable("No provider configured.")
        assert r.confidence == "no_data"

    def test_schema_version_present(self):
        from app.models.data_provenance import PROVENANCE_SCHEMA_VERSION, DataProvenanceRecord
        r = DataProvenanceRecord()
        assert r.schema_version == PROVENANCE_SCHEMA_VERSION

    def test_earnings_provenance_to_dict(self):
        from app.models.data_provenance import EarningsProvenance, DataProvenanceRecord
        ep = EarningsProvenance(
            date_provenance=DataProvenanceRecord.single_source("finnhub"),
            session_provenance=DataProvenanceRecord.unavailable(),
            sources_checked=["finnhub"],
            sources_returned_data=["finnhub"],
        )
        d = ep.to_dict()
        assert "date_provenance" in d
        assert "provider_detail" in d
        json.dumps(d)  # serializable

    def test_greek_provenance_to_dict(self):
        from app.models.data_provenance import GreekProvenance
        gp = GreekProvenance(delta_source="tradier", iv_source="tradier", calculation_method="provider_native")
        d = gp.to_dict()
        assert d["delta_source"] == "tradier"
        json.dumps(d)

    def test_chain_data_provenance_to_dict(self):
        from app.models.data_provenance import ChainDataProvenance
        cp = ChainDataProvenance(provider="tradier", retrieved_at="2026-07-13T10:00:00Z", completeness="complete")
        d = cp.to_dict()
        assert d["provider"] == "tradier"
        json.dumps(d)


# ─── Epic A: Data Provenance Service ──────────────────────────────────────────

class TestDataProvenanceService:
    def test_attach_and_get_provenance(self):
        from app.services.data_provenance_service import attach_provenance, get_provenance
        from app.models.data_provenance import DataProvenanceRecord
        data = {"price": 44.50}
        rec = DataProvenanceRecord.single_source("tradier")
        attach_provenance(data, "price", rec)
        assert "_provenance" in data
        got = get_provenance(data, "price")
        assert got is not None
        assert got.source == "tradier"

    def test_strip_provenance(self):
        from app.services.data_provenance_service import attach_provenance, strip_provenance
        from app.models.data_provenance import DataProvenanceRecord
        data = {"price": 44.50}
        attach_provenance(data, "price", DataProvenanceRecord.single_source("tradier"))
        stripped = strip_provenance(data)
        assert "_provenance" not in stripped
        assert stripped["price"] == 44.50

    def test_provenance_summary(self):
        from app.services.data_provenance_service import attach_provenance, provenance_summary
        from app.models.data_provenance import DataProvenanceRecord
        data = {}
        attach_provenance(data, "earnings_date", DataProvenanceRecord.single_source("finnhub"))
        attach_provenance(data, "iv", DataProvenanceRecord.single_source("tradier"))
        s = provenance_summary(data)
        assert s["field_count"] == 2
        assert "finnhub" in s["sources"]
        assert "tradier" in s["sources"]
        assert not s["has_conflicts"]

    def test_build_earnings_provenance_single_source(self):
        from app.services.data_provenance_service import build_earnings_provenance
        event = {
            "earnings_date": "2026-07-22",
            "sources_seen": ["finnhub"],
            "earnings_source_conflict": False,
        }
        prov = build_earnings_provenance(event)
        assert prov.date_provenance.confidence == "single_source"
        assert prov.date_provenance.source == "finnhub"
        assert not prov.date_agreement

    def test_build_earnings_provenance_multi_source(self):
        from app.services.data_provenance_service import build_earnings_provenance
        event = {
            "earnings_date": "2026-07-22",
            "sources_seen": ["finnhub", "alpha_vantage"],
            "earnings_source_conflict": False,
        }
        prov = build_earnings_provenance(event)
        assert prov.date_provenance.confidence == "confirmed"
        assert prov.date_agreement is True

    def test_build_earnings_provenance_conflict(self):
        from app.services.data_provenance_service import build_earnings_provenance
        event = {
            "earnings_date": "2026-07-22",
            "sources_seen": ["finnhub", "alpha_vantage"],
            "earnings_source_conflict": True,
            "earnings_conflict_details": [
                {"date": "2026-07-22", "sources": ["finnhub"]},
                {"date": "2026-07-24", "sources": ["alpha_vantage"]},
            ],
        }
        prov = build_earnings_provenance(event)
        assert prov.date_provenance.confidence == "disputed"
        assert prov.date_provenance.conflict_detected is True

    def test_detect_value_conflict_numeric(self):
        from app.services.data_provenance_service import detect_value_conflict
        r = detect_value_conflict("iv", {"tradier": 0.45, "robinhood": 0.60}, tolerance=0.01)
        assert r["has_conflict"] is True
        assert r["field"] == "iv"

    def test_detect_value_conflict_within_tolerance(self):
        from app.services.data_provenance_service import detect_value_conflict
        r = detect_value_conflict("iv", {"tradier": 0.45, "robinhood": 0.451}, tolerance=0.01)
        assert r["has_conflict"] is False

    def test_detect_value_conflict_single_source(self):
        from app.services.data_provenance_service import detect_value_conflict
        r = detect_value_conflict("iv", {"tradier": 0.45})
        assert r["has_conflict"] is False
        assert r["agreed_value"] == 0.45

    def test_annotate_freshness(self):
        from app.services.data_provenance_service import annotate_freshness
        data = {"price": 44.50}
        annotate_freshness(data)
        assert "retrieved_at" in data
        assert data["retrieved_at"]

    def test_freshness_age_seconds(self):
        from app.services.data_provenance_service import freshness_age_seconds
        ts = (datetime.now(timezone.utc) - timedelta(seconds=3600)).isoformat()
        age = freshness_age_seconds({"retrieved_at": ts})
        assert 3590 <= age <= 3610

    def test_freshness_label_fresh(self):
        from app.services.data_provenance_service import freshness_label
        assert freshness_label(300) == "fresh"

    def test_freshness_label_aging(self):
        from app.services.data_provenance_service import freshness_label
        assert freshness_label(30000) == "aging"

    def test_freshness_label_stale(self):
        from app.services.data_provenance_service import freshness_label
        assert freshness_label(100000) == "stale"

    def test_freshness_label_unknown(self):
        from app.services.data_provenance_service import freshness_label
        assert freshness_label(None) == "unknown"

    def test_compact_provenance_minimal(self):
        from app.services.data_provenance_service import compact_provenance
        from app.models.data_provenance import DataProvenanceRecord
        rec = DataProvenanceRecord.single_source("tradier", "2026-07-13T10:00:00+00:00")
        c = compact_provenance(rec)
        assert c["source"] == "tradier"
        assert c["confidence"] == "single_source"
        assert "retrieved_at" in c
        json.dumps(c)

    def test_build_forward_factor_provenance(self):
        from app.services.data_provenance_service import build_forward_factor_provenance
        from app.models.data_provenance import SOURCE_CALCULATED
        rec = build_forward_factor_provenance("tradier", "tradier", 60, 90)
        assert rec.source == SOURCE_CALCULATED
        assert "60d" in rec.selection_reason
        assert "90d" in rec.selection_reason


# ─── Epic B: Earnings Confidence Service ──────────────────────────────────────

class TestEarningsConfidenceService:
    def _event(self, **kwargs) -> dict:
        base = {
            "earnings_date": "2026-07-22",
            "sources_seen": ["finnhub", "alpha_vantage"],
            "earnings_source_conflict": False,
            "earnings_date_confidence": "confirmed",
            "session_label": "Before Open",
        }
        base.update(kwargs)
        return base

    def test_build_report_is_serializable(self):
        from app.services.earnings_confidence_service import build_earnings_confidence_report
        r = build_earnings_confidence_report(self._event())
        json.dumps(r)

    def test_multi_source_report_confirmed(self):
        from app.services.earnings_confidence_service import build_earnings_confidence_report
        r = build_earnings_confidence_report(self._event())
        assert r["date_confidence"] == "confirmed"
        assert r["provider_count"] == 2
        assert r["trade_allowed"] is True
        assert r["conflict_detected"] is False

    def test_single_source_report_warning(self):
        from app.services.earnings_confidence_service import build_earnings_confidence_report
        r = build_earnings_confidence_report(self._event(sources_seen=["finnhub"]))
        assert r["date_confidence"] == "single_source"
        assert r["provider_count"] == 1

    def test_conflict_blocks_trade(self):
        from app.services.earnings_confidence_service import build_earnings_confidence_report
        r = build_earnings_confidence_report(self._event(
            earnings_source_conflict=True,
            earnings_conflict_details=[
                {"date": "2026-07-22", "sources": ["finnhub"]},
                {"date": "2026-07-24", "sources": ["alpha_vantage"]},
            ],
        ))
        assert r["conflict_detected"] is True
        assert r["trade_allowed"] is False

    def test_report_has_provider_rows(self):
        from app.services.earnings_confidence_service import build_earnings_confidence_report
        r = build_earnings_confidence_report(self._event())
        # provider_rows may be empty if provider_detail is empty — that's OK
        assert isinstance(r["provider_rows"], list)

    def test_report_read_only(self):
        from app.services.earnings_confidence_service import build_earnings_confidence_report
        r = build_earnings_confidence_report(self._event())
        assert r["provider_calls_triggered"] is False
        assert r["read_only"] is True

    def test_build_bulk_earnings_confidence(self):
        from app.services.earnings_confidence_service import build_bulk_earnings_confidence
        events = {
            "BAC": self._event(),
            "AAPL": self._event(sources_seen=["finnhub"], earnings_date_confidence="single_source"),
        }
        result = build_bulk_earnings_confidence(events)
        assert "BAC" in result
        assert "AAPL" in result
        assert result["BAC"]["date_confidence"] == "confirmed"

    def test_enrich_event_with_confidence(self):
        from app.services.earnings_confidence_service import enrich_earnings_event_with_confidence
        event = self._event()
        enriched = enrich_earnings_event_with_confidence(event)
        assert "_confidence" in enriched
        assert enriched["_confidence"]["date_confidence"] == "confirmed"

    def test_public_confidence_label_confirmed(self):
        from app.services.earnings_confidence_service import (
            build_earnings_confidence_report, public_confidence_label,
        )
        r = build_earnings_confidence_report(self._event())
        label = public_confidence_label(r)
        assert "confirmed" in label.lower() or "finnhub" in label.lower() or "alpha" in label.lower()

    def test_public_confidence_label_conflict(self):
        from app.services.earnings_confidence_service import public_confidence_label
        label = public_confidence_label({"conflict_detected": True})
        assert "conflict" in label.lower() or "do not trade" in label.lower()

    def test_confidence_gate_passed(self):
        from app.services.earnings_confidence_service import (
            build_earnings_confidence_report, confidence_gate_passed,
        )
        r = build_earnings_confidence_report(self._event())
        assert confidence_gate_passed(r) is True

    def test_confidence_gate_fails_on_conflict(self):
        from app.services.earnings_confidence_service import confidence_gate_passed
        assert confidence_gate_passed({"trade_allowed": False, "conflict_detected": True}) is False

    def test_no_data_report_no_trade(self):
        from app.services.earnings_confidence_service import build_earnings_confidence_report
        r = build_earnings_confidence_report({})
        assert r["trade_allowed"] is False


# ─── Epic C: Provider Conflict Service ────────────────────────────────────────

class TestProviderConflictService:
    def test_no_conflict_single_source(self):
        from app.services.provider_conflict_service import detect_earnings_date_conflict
        conflicts = detect_earnings_date_conflict({"finnhub": "2026-07-22"})
        assert conflicts == []

    def test_no_conflict_agreement(self):
        from app.services.provider_conflict_service import detect_earnings_date_conflict
        conflicts = detect_earnings_date_conflict({"finnhub": "2026-07-22", "av": "2026-07-22"})
        assert conflicts == []

    def test_critical_conflict_detected(self):
        from app.services.provider_conflict_service import detect_earnings_date_conflict, ConflictRecord
        conflicts = detect_earnings_date_conflict(
            {"finnhub": "2026-07-22", "av": "2026-07-24"},
            conflict_threshold_days=2,
        )
        assert len(conflicts) == 1
        assert conflicts[0].field == "earnings_date"
        assert conflicts[0].severity == ConflictRecord.SEVERITY_CRITICAL

    def test_bleed_suspect_warning(self):
        from app.services.provider_conflict_service import detect_earnings_date_conflict, ConflictRecord
        conflicts = detect_earnings_date_conflict(
            {"finnhub": "2026-07-22", "av": "2026-07-27"},
            conflict_threshold_days=2,
            bleed_suspect_window_days=10,
        )
        assert len(conflicts) == 1
        assert conflicts[0].severity == ConflictRecord.SEVERITY_WARNING
        assert conflicts[0].conflict_type == "date_bleed_suspect"

    def test_conflict_record_to_dict(self):
        from app.services.provider_conflict_service import detect_earnings_date_conflict
        conflicts = detect_earnings_date_conflict(
            {"finnhub": "2026-07-22", "av": "2026-07-24"},
            conflict_threshold_days=2,
        )
        d = conflicts[0].to_dict()
        json.dumps(d)
        assert "earnings_date" == d["field"]

    def test_numeric_conflict_detected(self):
        from app.services.provider_conflict_service import detect_numeric_conflict
        c = detect_numeric_conflict("iv", {"tradier": 0.45, "robinhood": 0.70}, tolerance=0.05)
        assert c is not None
        assert c.field == "iv"

    def test_numeric_conflict_within_tolerance(self):
        from app.services.provider_conflict_service import detect_numeric_conflict
        c = detect_numeric_conflict("iv", {"tradier": 0.45, "robinhood": 0.451}, tolerance=0.01)
        assert c is None

    def test_build_conflict_summary_no_conflicts(self):
        from app.services.provider_conflict_service import build_conflict_summary
        s = build_conflict_summary([])
        assert s["has_conflicts"] is False
        assert s["conflict_count"] == 0

    def test_build_conflict_summary_with_conflicts(self):
        from app.services.provider_conflict_service import (
            detect_earnings_date_conflict, build_conflict_summary,
        )
        conflicts = detect_earnings_date_conflict({"finnhub": "2026-07-22", "av": "2026-07-24"})
        s = build_conflict_summary(conflicts)
        assert s["has_conflicts"] is True
        assert s["critical_count"] >= 1
        json.dumps(s)

    def test_resolve_conflict_to_provenance(self):
        from app.services.provider_conflict_service import (
            detect_earnings_date_conflict, resolve_conflict_to_provenance, ConflictRecord,
        )
        conflicts = detect_earnings_date_conflict({"finnhub": "2026-07-22", "av": "2026-07-24"})
        prov = resolve_conflict_to_provenance(conflicts[0])
        assert prov.conflict_detected is True
        assert prov.confidence in ("disputed", "single_source")


# ─── Epic D: Chain Accuracy Audit ─────────────────────────────────────────────

class TestChainAccuracyService:
    def _good_leg(self, **kwargs) -> dict:
        base = {
            "bid": 0.45,
            "ask": 0.50,
            "iv": 0.42,
            "delta": -0.35,
            "open_interest": 500,
            "expiration_date": "2026-07-18",
        }
        base.update(kwargs)
        return base

    def test_audit_empty_chain_fails(self):
        from app.services.chain_accuracy_service import audit_chain_legs
        r = audit_chain_legs([], "AAPL", "tradier")
        assert r.audit_passed is False
        assert "Empty" in (r.audit_errors[0] if r.audit_errors else "")

    def test_audit_good_chain_passes(self):
        from app.services.chain_accuracy_service import audit_chain_legs
        legs = [self._good_leg(), self._good_leg(bid=0.80, ask=0.90, iv=0.58, expiration_date="2026-07-25")]
        r = audit_chain_legs(legs, "BAC", "tradier")
        assert r.audit_passed is True
        assert r.total_legs == 2
        assert r.bid_ask_inversion_count == 0

    def test_audit_detects_bid_ask_inversion(self):
        from app.services.chain_accuracy_service import audit_chain_legs
        legs = [self._good_leg(bid=0.80, ask=0.50)]
        r = audit_chain_legs(legs, "BAC", "tradier")
        assert r.audit_passed is False
        assert r.bid_ask_inversion_count == 1

    def test_audit_detects_implausible_iv(self):
        from app.services.chain_accuracy_service import audit_chain_legs
        legs = [self._good_leg(iv=25.0)]
        r = audit_chain_legs(legs, "BAC", "tradier")
        assert r.iv_anomaly_count == 1

    def test_audit_detects_zero_bid(self):
        from app.services.chain_accuracy_service import audit_chain_legs
        legs = [self._good_leg(bid=0.0)] * 4 + [self._good_leg()]
        r = audit_chain_legs(legs, "BAC", "tradier")
        assert r.zero_bid_count == 4

    def test_audit_result_to_dict_serializable(self):
        from app.services.chain_accuracy_service import audit_chain_legs
        legs = [self._good_leg()]
        r = audit_chain_legs(legs, "BAC", "tradier")
        json.dumps(r.to_dict())

    def test_audit_chain_provenance_attached(self):
        from app.services.chain_accuracy_service import audit_chain_legs
        legs = [self._good_leg()]
        r = audit_chain_legs(legs, "BAC", "tradier")
        assert r.chain_provenance is not None
        assert r.chain_provenance.provider == "tradier"

    def test_two_leg_spread_same_expiry_error(self):
        from app.services.chain_accuracy_service import audit_two_leg_spread
        leg = self._good_leg()
        r = audit_two_leg_spread(leg, leg, "BAC", "tradier")
        assert r.audit_passed is False
        assert any("same expiration" in e for e in r.audit_errors)

    def test_two_leg_spread_good_calendar(self):
        from app.services.chain_accuracy_service import audit_two_leg_spread
        front = self._good_leg(iv=0.55, expiration_date="2026-07-18")
        back = self._good_leg(iv=0.42, expiration_date="2026-07-25")
        r = audit_two_leg_spread(front, back, "BAC", "tradier")
        # front_iv > back_iv → warning but not error
        assert r.bid_ask_inversion_count == 0

    def test_two_leg_spread_iv_ordering_warning(self):
        from app.services.chain_accuracy_service import audit_two_leg_spread
        front = self._good_leg(iv=0.35, expiration_date="2026-07-18")
        back = self._good_leg(iv=0.55, expiration_date="2026-07-25")
        r = audit_two_leg_spread(front, back, "BAC", "tradier")
        assert any("Front IV" in w for w in r.audit_warnings)

    def test_build_chain_audit_summary(self):
        from app.services.chain_accuracy_service import (
            audit_chain_legs, build_chain_audit_summary,
        )
        results = [
            audit_chain_legs([self._good_leg()], "BAC", "tradier"),
            audit_chain_legs([], "AAPL", "tradier"),
        ]
        s = build_chain_audit_summary(results)
        assert s["total_chains_audited"] == 2
        assert s["passed"] == 1
        assert s["failed"] == 1
        json.dumps(s)


# ─── Epic E: Greek Source Attribution ─────────────────────────────────────────

class TestGreekAttributionService:
    def _row(self) -> dict:
        return {
            "delta": -0.35,
            "gamma": 0.08,
            "theta": -0.02,
            "vega": 0.15,
            "rho": 0.01,
            "iv": 0.42,
            "expiration_date": "2026-07-18",
        }

    def test_build_greek_attribution_report_tradier(self):
        from app.services.greek_attribution_service import build_greek_attribution_report
        r = build_greek_attribution_report(self._row(), "tradier")
        assert r["provider"] == "tradier"
        assert r["greeks"]["delta"]["source"] == "tradier"
        assert r["greeks"]["delta"]["available"] is True

    def test_greek_iv_present(self):
        from app.services.greek_attribution_service import build_greek_attribution_report
        r = build_greek_attribution_report(self._row(), "tradier")
        assert r["greeks"]["iv"]["available"] is True

    def test_missing_greek_marked_unavailable(self):
        from app.services.greek_attribution_service import build_greek_attribution_report
        row = {"iv": 0.42}
        r = build_greek_attribution_report(row, "tradier")
        assert r["greeks"]["delta"]["available"] is False
        assert r["greeks"]["delta"]["confidence"] == "no_data"

    def test_report_is_read_only(self):
        from app.services.greek_attribution_service import build_greek_attribution_report
        r = build_greek_attribution_report(self._row(), "tradier")
        assert r["provider_calls_triggered"] is False
        assert r["read_only"] is True

    def test_compact_greek_sources(self):
        from app.services.greek_attribution_service import compact_greek_sources
        sources = compact_greek_sources(self._row(), "tradier")
        assert sources["delta"] == "tradier"
        assert sources["iv"] == "tradier"

    def test_compact_greek_sources_missing_greek_excluded(self):
        from app.services.greek_attribution_service import compact_greek_sources
        sources = compact_greek_sources({"iv": 0.42}, "tradier")
        assert "delta" not in sources
        assert "iv" in sources

    def test_enrich_option_row(self):
        from app.services.greek_attribution_service import enrich_option_row_with_greek_attribution
        row = self._row()
        enriched = enrich_option_row_with_greek_attribution(row, "tradier")
        assert "_greek_provenance" in enriched
        json.dumps(enriched["_greek_provenance"])

    def test_report_serializable(self):
        from app.services.greek_attribution_service import build_greek_attribution_report
        r = build_greek_attribution_report(self._row(), "tradier")
        json.dumps(r)


# ─── Epic F: Freshness (via data_provenance_service) ──────────────────────────

class TestFreshnessAnnotations:
    def test_annotate_freshness_stamps_retrieved_at(self):
        from app.services.data_provenance_service import annotate_freshness
        data = {"price": 44.50}
        annotate_freshness(data)
        assert "retrieved_at" in data
        # ISO 8601 format
        datetime.fromisoformat(data["retrieved_at"].replace("Z", "+00:00"))

    def test_annotate_freshness_does_not_overwrite(self):
        from app.services.data_provenance_service import annotate_freshness
        ts = "2026-07-13T10:00:00+00:00"
        data = {"retrieved_at": ts}
        annotate_freshness(data)
        assert data["retrieved_at"] == ts

    def test_freshness_age_seconds_none_when_missing(self):
        from app.services.data_provenance_service import freshness_age_seconds
        assert freshness_age_seconds({}) is None

    def test_freshness_age_seconds_correct(self):
        from app.services.data_provenance_service import freshness_age_seconds
        ts = (datetime.now(timezone.utc) - timedelta(seconds=7200)).isoformat()
        age = freshness_age_seconds({"retrieved_at": ts})
        assert 7190 <= age <= 7210

    def test_freshness_label_boundaries(self):
        from app.services.data_provenance_service import freshness_label
        assert freshness_label(0) == "fresh"
        assert freshness_label(21601) == "aging"
        assert freshness_label(86401) == "stale"


# ─── Epic G: Automated Data Validation ────────────────────────────────────────

class TestAutomatedDataValidation:
    def test_validate_good_earnings_event_passes(self):
        from app.services.automated_data_validation_service import validate_earnings_event
        event = {
            "earnings_date": "2026-07-22",
            "sources_seen": ["finnhub"],
            "earnings_date_confidence": "confirmed",
        }
        report = validate_earnings_event(event, "finnhub")
        assert report.passed is True
        assert report.error_count == 0

    def test_validate_earnings_missing_date_fails(self):
        from app.services.automated_data_validation_service import validate_earnings_event
        report = validate_earnings_event({}, "finnhub")
        assert report.error_count > 0
        assert not report.passed

    def test_validate_earnings_bad_date_format_fails(self):
        from app.services.automated_data_validation_service import validate_earnings_event
        report = validate_earnings_event({"earnings_date": "July 22 2026"}, "finnhub")
        assert report.error_count > 0

    def test_validate_good_option_leg_passes(self):
        from app.services.automated_data_validation_service import validate_options_leg
        leg = {"bid": 0.45, "ask": 0.50, "iv": 0.42, "expiration_date": "2026-07-18"}
        report = validate_options_leg(leg, "tradier")
        assert report.error_count == 0

    def test_validate_leg_bid_ask_inversion_error(self):
        from app.services.automated_data_validation_service import validate_options_leg
        leg = {"bid": 0.80, "ask": 0.50, "iv": 0.42, "expiration_date": "2026-07-18"}
        report = validate_options_leg(leg, "tradier")
        assert report.error_count > 0

    def test_validate_leg_implausible_iv_warning(self):
        from app.services.automated_data_validation_service import validate_options_leg
        leg = {"bid": 0.45, "ask": 0.50, "iv": 25.0, "expiration_date": "2026-07-18"}
        report = validate_options_leg(leg, "tradier")
        assert report.warning_count > 0

    def test_validate_quote_good_passes(self):
        from app.services.automated_data_validation_service import validate_quote
        q = {"last": 44.50, "retrieved_at": "2026-07-13T10:00:00Z"}
        report = validate_quote(q, "BAC", "tradier")
        assert report.error_count == 0

    def test_validate_quote_missing_price_fails(self):
        from app.services.automated_data_validation_service import validate_quote
        report = validate_quote({}, "BAC", "tradier")
        assert report.error_count > 0

    def test_cross_validate_earnings_agreement(self):
        from app.services.automated_data_validation_service import cross_validate_earnings_dates
        events = {
            "finnhub": {"earnings_date": "2026-07-22"},
            "alpha_vantage": {"earnings_date": "2026-07-22"},
        }
        report = cross_validate_earnings_dates(events, "BAC")
        assert report.error_count == 0

    def test_cross_validate_earnings_conflict(self):
        from app.services.automated_data_validation_service import cross_validate_earnings_dates
        events = {
            "finnhub": {"earnings_date": "2026-07-22"},
            "alpha_vantage": {"earnings_date": "2026-07-24"},
        }
        report = cross_validate_earnings_dates(events, "BAC")
        assert report.error_count > 0

    def test_validate_strategy_row_schema_good(self):
        from app.services.automated_data_validation_service import validate_strategy_row_schema
        row = {
            "ticker": "BAC",
            "action": "EARNINGS CALENDAR CANDIDATE",
            "score": 72.0,
            "daily_opportunity": {"eligible": True},
        }
        report = validate_strategy_row_schema(row, "earnings_calendar")
        assert report.error_count == 0

    def test_validate_strategy_row_schema_missing_ticker(self):
        from app.services.automated_data_validation_service import validate_strategy_row_schema
        row = {"action": "CANDIDATE", "score": 72.0}
        report = validate_strategy_row_schema(row, "earnings_calendar")
        assert report.error_count > 0

    def test_validate_strategy_row_schema_bad_eligible_type(self):
        from app.services.automated_data_validation_service import validate_strategy_row_schema
        row = {
            "ticker": "BAC", "action": "CANDIDATE", "score": 72.0,
            "daily_opportunity": {"eligible": "yes"},  # not bool
        }
        report = validate_strategy_row_schema(row, "earnings_calendar")
        assert report.error_count > 0

    def test_run_validation_suite(self):
        from app.services.automated_data_validation_service import run_validation_suite
        rows = [
            {"ticker": "BAC", "action": "CANDIDATE", "score": 72.0},
            {"ticker": "SBUX", "action": "CANDIDATE", "score": 65.0},
        ]
        result = run_validation_suite(rows, "earnings_calendar")
        assert result["provider_calls_triggered"] is False
        assert result["read_only"] is True
        assert result["validation_passed"] is True
        assert result["strategy_id"] == "earnings_calendar"

    def test_validation_report_to_dict_serializable(self):
        from app.services.automated_data_validation_service import validate_earnings_event
        report = validate_earnings_event({"earnings_date": "2026-07-22", "sources_seen": ["finnhub"]})
        json.dumps(report.to_dict())


# ─── Epic H: Strategy Data Diagnostics ────────────────────────────────────────

class TestStrategyDataDiagnostics:
    def test_basic_diagnostics_flow(self):
        from app.services.strategy_data_diagnostics_service import StrategyDataDiagnostics
        d = StrategyDataDiagnostics("earnings_calendar", "BAC")
        d.require("earnings_date", required=True)
        d.mark_present("earnings_date", source="finnhub", confidence="confirmed")
        d.require("options_chain", required=True)
        d.mark_missing("options_chain", reason="Tradier returned empty chain.")
        assert not d.data_complete
        assert "options_chain" in d.missing_required

    def test_diagnostics_complete_when_all_present(self):
        from app.services.strategy_data_diagnostics_service import StrategyDataDiagnostics
        d = StrategyDataDiagnostics("earnings_calendar", "BAC")
        d.require("earnings_date")
        d.mark_present("earnings_date", source="finnhub")
        assert d.data_complete

    def test_diagnostics_to_dict_serializable(self):
        from app.services.strategy_data_diagnostics_service import StrategyDataDiagnostics
        d = StrategyDataDiagnostics("earnings_calendar", "BAC")
        d.require("earnings_date")
        d.mark_present("earnings_date", source="finnhub", confidence="confirmed")
        result = d.to_dict()
        json.dumps(result)

    def test_diagnostics_to_dict_schema_version(self):
        from app.services.strategy_data_diagnostics_service import StrategyDataDiagnostics
        from app.models.data_provenance import PROVENANCE_SCHEMA_VERSION
        d = StrategyDataDiagnostics("test", "AAPL")
        r = d.to_dict()
        assert r["schema_version"] == PROVENANCE_SCHEMA_VERSION

    def test_attach_data_diagnostics(self):
        from app.services.strategy_data_diagnostics_service import (
            StrategyDataDiagnostics, attach_data_diagnostics, get_data_diagnostics,
        )
        row = {"ticker": "BAC", "score": 72.0}
        d = StrategyDataDiagnostics("earnings_calendar", "BAC")
        d.require("earnings_date")
        d.mark_present("earnings_date", source="finnhub")
        attach_data_diagnostics(row, d)
        got = get_data_diagnostics(row)
        assert got is not None
        assert got["ticker"] == "BAC"

    def test_mark_partial(self):
        from app.services.strategy_data_diagnostics_service import StrategyDataDiagnostics
        d = StrategyDataDiagnostics("ff_calendar", "SPY")
        d.mark_partial("options_chain", 0.6, source="tradier", reason="Missing 2 expirations.")
        r = d.to_dict()
        assert r["fields"]["options_chain"]["status"] == "partial"

    def test_rejection_reason_recorded(self):
        from app.services.strategy_data_diagnostics_service import StrategyDataDiagnostics
        d = StrategyDataDiagnostics("earnings_calendar", "BAC")
        d.set_rejection_reason("earnings_date_conflict")
        r = d.to_dict()
        assert r["rejection_reason"] == "earnings_date_conflict"

    def test_diagnostics_summary(self):
        from app.services.strategy_data_diagnostics_service import (
            StrategyDataDiagnostics, attach_data_diagnostics, diagnostics_summary,
        )
        rows = []
        for ticker in ["BAC", "AAPL"]:
            d = StrategyDataDiagnostics("ec", ticker)
            d.require("earnings_date")
            d.mark_present("earnings_date", source="finnhub")
            row = {"ticker": ticker}
            attach_data_diagnostics(row, d)
            rows.append(row)
        s = diagnostics_summary(rows)
        assert s["total_rows"] == 2
        assert s["data_complete_count"] == 2

    def test_stale_field_recorded(self):
        from app.services.strategy_data_diagnostics_service import StrategyDataDiagnostics
        d = StrategyDataDiagnostics("ec", "BAC")
        d.mark_present("quote", source="tradier", stale=True)
        r = d.to_dict()
        assert "quote" in r["stale_fields"]


# ─── Epic M: Provider Health Service ──────────────────────────────────────────

class TestProviderHealthService:
    def _manifest_tradier_ok(self) -> dict:
        return {
            "created_at": "2026-07-13T10:00:00Z",
            "provider_status": {
                "tradier": {"status": "ok", "positions_available": True},
                "robinhood": {"status": "auth_failed"},
            },
        }

    def test_build_from_manifest_healthy(self):
        from app.services.provider_health_service import (
            build_provider_health_from_manifest, HEALTH_HEALTHY,
        )
        records = build_provider_health_from_manifest(self._manifest_tradier_ok())
        assert records["tradier"].status == HEALTH_HEALTHY

    def test_build_from_manifest_auth_failed(self):
        from app.services.provider_health_service import (
            build_provider_health_from_manifest, HEALTH_UNAVAILABLE,
        )
        records = build_provider_health_from_manifest(self._manifest_tradier_ok())
        assert records["robinhood"].status == HEALTH_UNAVAILABLE
        assert records["robinhood"].error_code == "auth_failed"

    def test_build_from_manifest_unknown_provider(self):
        from app.services.provider_health_service import (
            build_provider_health_from_manifest, HEALTH_UNKNOWN,
        )
        records = build_provider_health_from_manifest({})
        for slug in ("finnhub", "alpha_vantage"):
            assert records[slug].status == HEALTH_UNKNOWN

    def test_build_health_summary_serializable(self):
        from app.services.provider_health_service import (
            build_provider_health_from_manifest, build_health_summary,
        )
        records = build_provider_health_from_manifest(self._manifest_tradier_ok())
        s = build_health_summary(records)
        json.dumps(s)

    def test_health_summary_read_only(self):
        from app.services.provider_health_service import (
            build_provider_health_from_manifest, build_health_summary,
        )
        records = build_provider_health_from_manifest({})
        s = build_health_summary(records)
        assert s["provider_calls_triggered"] is False
        assert s["read_only"] is True

    def test_health_summary_overall_degraded_when_some_unavailable(self):
        from app.services.provider_health_service import (
            build_provider_health_from_manifest, build_health_summary, HEALTH_DEGRADED,
        )
        records = build_provider_health_from_manifest(self._manifest_tradier_ok())
        s = build_health_summary(records)
        # robinhood is unavailable → overall degraded
        assert s["overall_status"] == HEALTH_DEGRADED

    def test_classify_degraded_reason(self):
        from app.services.provider_health_service import (
            build_provider_health_from_manifest, classify_degraded_reason_from_health,
        )
        records = build_provider_health_from_manifest(self._manifest_tradier_ok())
        reason = classify_degraded_reason_from_health(records)
        assert "robinhood" in reason

    def test_classify_healthy_all_ok(self):
        from app.services.provider_health_service import (
            build_provider_health_from_manifest, classify_degraded_reason_from_health,
        )
        manifest = {
            "created_at": "2026-07-13T10:00:00Z",
            "provider_status": {
                p: {"status": "ok"} for p in ("tradier", "robinhood", "finnhub", "alpha_vantage")
            },
        }
        records = build_provider_health_from_manifest(manifest)
        reason = classify_degraded_reason_from_health(records)
        assert reason == "all_providers_healthy"

    def test_health_record_to_dict_serializable(self):
        from app.services.provider_health_service import ProviderHealthRecord
        r = ProviderHealthRecord("tradier")
        json.dumps(r.to_dict())
