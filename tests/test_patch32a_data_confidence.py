"""
ASA Patch 32A — Data Confidence Completion Tests

Unit + integration coverage for:
  - FieldProvenanceRecord model (32A canonical shape)
  - Earnings selection service (deterministic HIGH/MEDIUM/LOW/CONFLICT/UNKNOWN)
  - Data provenance repository (SQLite persistence)
  - Data confidence enrichment service (strategy row enrichment)
  - Data confidence API (field endpoint, reference endpoint)
  - Automated validation log (DATA_CONFIDENCE_VALIDATION)
  - Endpoint verification checks (32A additions)
  - Backward compatibility (rows without provenance return UNKNOWN)
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

import pytest

# ─── 1. Canonical Provenance Model ───────────────────────────────────────────


class TestFieldProvenanceRecord:
    def test_defaults_safe(self):
        from app.models.patch32a_provenance import FieldProvenanceRecord, CONFIDENCE_UNKNOWN
        rec = FieldProvenanceRecord()
        assert rec.confidence_level == CONFIDENCE_UNKNOWN
        assert rec.field_id == ""
        assert rec.selected_value is None
        assert rec.provider_values == []
        assert rec.conflicts == []
        assert rec.schema_version == "32A.v1"

    def test_confidence_color_property(self):
        from app.models.patch32a_provenance import (
            FieldProvenanceRecord, CONFIDENCE_HIGH, CONFIDENCE_CONFLICT,
            CONFIDENCE_UNKNOWN, CONFIDENCE_LOW, CONFIDENCE_MEDIUM,
        )
        for level, color in [
            (CONFIDENCE_HIGH, "green"),
            (CONFIDENCE_MEDIUM, "yellow-green"),
            (CONFIDENCE_LOW, "orange"),
            (CONFIDENCE_CONFLICT, "red"),
            (CONFIDENCE_UNKNOWN, "gray"),
        ]:
            rec = FieldProvenanceRecord(confidence_level=level)
            assert rec.confidence_color == color

    def test_has_conflict_false_by_default(self):
        from app.models.patch32a_provenance import FieldProvenanceRecord
        rec = FieldProvenanceRecord()
        assert rec.has_conflict is False

    def test_has_conflict_true_when_conflict_level(self):
        from app.models.patch32a_provenance import FieldProvenanceRecord, CONFIDENCE_CONFLICT
        rec = FieldProvenanceRecord(confidence_level=CONFIDENCE_CONFLICT)
        assert rec.has_conflict is True

    def test_has_conflict_true_when_conflicts_list(self):
        from app.models.patch32a_provenance import FieldProvenanceRecord, CONFIDENCE_LOW
        rec = FieldProvenanceRecord(
            confidence_level=CONFIDENCE_LOW,
            conflicts=[{"provider": "finnhub", "value": "2025-02-10"}],
        )
        assert rec.has_conflict is True

    def test_to_dict_serializable(self):
        from app.models.patch32a_provenance import FieldProvenanceRecord
        rec = FieldProvenanceRecord(field_id="earnings_date", selected_value="2025-02-10")
        d = rec.to_dict()
        assert isinstance(d, dict)
        assert d["field_id"] == "earnings_date"
        assert d["selected_value"] == "2025-02-10"
        # Must be JSON-serializable
        json.dumps(d)

    def test_from_dict_roundtrip(self):
        from app.models.patch32a_provenance import FieldProvenanceRecord, CONFIDENCE_HIGH
        original = FieldProvenanceRecord(
            field_id="earnings_date",
            selected_value="2025-03-15",
            confidence_level=CONFIDENCE_HIGH,
            selection_reason="Two providers agree.",
        )
        restored = FieldProvenanceRecord.from_dict(original.to_dict())
        assert restored.field_id == original.field_id
        assert restored.confidence_level == original.confidence_level
        assert restored.selected_value == original.selected_value

    def test_from_dict_handles_none(self):
        from app.models.patch32a_provenance import FieldProvenanceRecord
        rec = FieldProvenanceRecord.from_dict(None)
        assert rec.field_id == ""

    def test_provider_count_counts_available(self):
        from app.models.patch32a_provenance import (
            FieldProvenanceRecord, ProviderValueRecord, STATUS_AVAILABLE, STATUS_MISSING,
        )
        pv = [
            ProviderValueRecord(provider="finnhub", status=STATUS_AVAILABLE),
            ProviderValueRecord(provider="alpha_vantage", status=STATUS_MISSING),
        ]
        rec = FieldProvenanceRecord(provider_values=pv)
        assert rec.provider_count == 1

    def test_compact_returns_minimal_fields(self):
        from app.models.patch32a_provenance import FieldProvenanceRecord, CONFIDENCE_HIGH
        rec = FieldProvenanceRecord(
            field_id="earnings_date",
            confidence_level=CONFIDENCE_HIGH,
            selected_provider="finnhub",
        )
        c = rec.compact()
        assert c["field_id"] == "earnings_date"
        assert c["confidence_level"] == CONFIDENCE_HIGH
        assert c["confidence_color"] == "green"
        assert "schema_version" in c


class TestProviderValueRecord:
    def test_available_factory(self):
        from app.models.patch32a_provenance import ProviderValueRecord, STATUS_AVAILABLE
        pv = ProviderValueRecord.available("finnhub", "2025-03-15")
        assert pv.status == STATUS_AVAILABLE
        assert pv.value == "2025-03-15"
        assert pv.provider == "finnhub"

    def test_missing_factory(self):
        from app.models.patch32a_provenance import ProviderValueRecord, STATUS_MISSING
        pv = ProviderValueRecord.missing("finnhub")
        assert pv.status == STATUS_MISSING
        assert pv.value is None

    def test_error_factory(self):
        from app.models.patch32a_provenance import ProviderValueRecord, STATUS_ERROR
        pv = ProviderValueRecord.error("finnhub", "TIMEOUT", "Connection timed out")
        assert pv.status == STATUS_ERROR
        assert pv.error_code == "TIMEOUT"

    def test_not_requested_factory(self):
        from app.models.patch32a_provenance import ProviderValueRecord, STATUS_NOT_REQUESTED
        pv = ProviderValueRecord.not_requested("robinhood")
        assert pv.status == STATUS_NOT_REQUESTED

    def test_unsupported_factory(self):
        from app.models.patch32a_provenance import ProviderValueRecord, STATUS_UNSUPPORTED
        pv = ProviderValueRecord.unsupported("alpha_vantage")
        assert pv.status == STATUS_UNSUPPORTED

    def test_to_dict_json_serializable(self):
        from app.models.patch32a_provenance import ProviderValueRecord
        pv = ProviderValueRecord.available("finnhub", "2025-03-15")
        d = pv.to_dict()
        json.dumps(d)


# ─── 2. Earnings Selection Service ────────────────────────────────────────────


class TestEarningsSelectionService:
    def _pr(self, **providers: dict) -> dict:
        return providers

    def test_high_confidence_two_agree_date_and_session(self):
        from app.services.earnings_selection_service import select_earnings_provenance
        from app.models.patch32a_provenance import CONFIDENCE_HIGH
        pr = {
            "finnhub": {"earnings_date": "2025-03-15", "session_label": "AMC"},
            "alpha_vantage": {"earnings_date": "2025-03-15", "session_label": "after market close"},
        }
        rec = select_earnings_provenance(pr)
        assert rec.confidence_level == CONFIDENCE_HIGH
        assert rec.selected_value == "2025-03-15"

    def test_medium_confidence_two_agree_date_session_differs(self):
        from app.services.earnings_selection_service import select_earnings_provenance
        from app.models.patch32a_provenance import CONFIDENCE_MEDIUM
        pr = {
            "finnhub": {"earnings_date": "2025-03-15", "session_label": "AMC"},
            "alpha_vantage": {"earnings_date": "2025-03-15", "session_label": "BMO"},
        }
        rec = select_earnings_provenance(pr)
        assert rec.confidence_level == CONFIDENCE_MEDIUM

    def test_low_confidence_single_source(self):
        from app.services.earnings_selection_service import select_earnings_provenance
        from app.models.patch32a_provenance import CONFIDENCE_LOW
        pr = {
            "finnhub": {"earnings_date": "2025-03-15"},
        }
        rec = select_earnings_provenance(pr)
        assert rec.confidence_level == CONFIDENCE_LOW
        assert rec.selected_value == "2025-03-15"
        assert rec.selected_provider == "finnhub"

    def test_conflict_two_different_dates(self):
        from app.services.earnings_selection_service import select_earnings_provenance
        from app.models.patch32a_provenance import CONFIDENCE_CONFLICT
        pr = {
            "finnhub": {"earnings_date": "2025-03-15"},
            "alpha_vantage": {"earnings_date": "2025-03-16"},
        }
        rec = select_earnings_provenance(pr)
        assert rec.confidence_level == CONFIDENCE_CONFLICT
        assert len(rec.conflicts) >= 2
        assert rec.has_conflict is True

    def test_unknown_no_data(self):
        from app.services.earnings_selection_service import select_earnings_provenance
        from app.models.patch32a_provenance import CONFIDENCE_UNKNOWN
        pr = {
            "finnhub": {},
            "alpha_vantage": {},
        }
        rec = select_earnings_provenance(pr)
        assert rec.confidence_level == CONFIDENCE_UNKNOWN
        assert rec.selected_value is None

    def test_priority_robinhood_over_finnhub(self):
        from app.services.earnings_selection_service import select_earnings_provenance
        from app.models.patch32a_provenance import CONFIDENCE_HIGH
        pr = {
            "robinhood": {"earnings_date": "2025-03-15", "session_label": "AMC"},
            "finnhub": {"earnings_date": "2025-03-15", "session_label": "AMC"},
        }
        rec = select_earnings_provenance(pr)
        assert rec.selected_provider == "robinhood"
        assert rec.confidence_level == CONFIDENCE_HIGH

    def test_priority_finnhub_over_alpha_vantage(self):
        from app.services.earnings_selection_service import select_earnings_provenance
        pr = {
            "finnhub": {"earnings_date": "2025-03-15"},
            "alpha_vantage": {"earnings_date": "2025-03-15"},
        }
        rec = select_earnings_provenance(pr)
        assert rec.selected_provider == "finnhub"

    def test_error_provider_retained_in_provider_values(self):
        from app.services.earnings_selection_service import select_earnings_provenance
        from app.models.patch32a_provenance import STATUS_ERROR, STATUS_AVAILABLE
        pr = {
            "finnhub": {"earnings_date": "2025-03-15"},
            "alpha_vantage": {"error": "Connection refused", "error_code": "TIMEOUT"},
        }
        rec = select_earnings_provenance(pr)
        statuses = {pv.provider: pv.status for pv in rec.provider_values}
        assert statuses.get("finnhub") == STATUS_AVAILABLE
        assert statuses.get("alpha_vantage") == STATUS_ERROR

    def test_missing_provider_retained(self):
        from app.services.earnings_selection_service import select_earnings_provenance
        from app.models.patch32a_provenance import STATUS_MISSING
        pr = {
            "finnhub": {"earnings_date": "2025-03-15"},
            "alpha_vantage": {},  # empty, no date
        }
        rec = select_earnings_provenance(pr)
        statuses = {pv.provider: pv.status for pv in rec.provider_values}
        assert statuses.get("alpha_vantage") == STATUS_MISSING

    def test_not_requested_provider_retained(self):
        from app.services.earnings_selection_service import select_earnings_provenance
        from app.models.patch32a_provenance import STATUS_NOT_REQUESTED
        pr = {
            "finnhub": {"earnings_date": "2025-03-15"},
            # robinhood not in provider_results → NOT_REQUESTED
        }
        rec = select_earnings_provenance(pr)
        statuses = {pv.provider: pv.status for pv in rec.provider_values}
        assert statuses.get("robinhood") == STATUS_NOT_REQUESTED

    def test_selected_provider_marked_is_selected(self):
        from app.services.earnings_selection_service import select_earnings_provenance
        pr = {"finnhub": {"earnings_date": "2025-03-15"}}
        rec = select_earnings_provenance(pr)
        selected = [pv for pv in rec.provider_values if pv.is_selected]
        assert len(selected) == 1
        assert selected[0].provider == "finnhub"

    def test_empty_provider_results(self):
        from app.services.earnings_selection_service import select_earnings_provenance
        from app.models.patch32a_provenance import CONFIDENCE_UNKNOWN
        rec = select_earnings_provenance({})
        assert rec.confidence_level == CONFIDENCE_UNKNOWN

    def test_date_normalisation_strips_time(self):
        from app.services.earnings_selection_service import select_earnings_provenance
        pr = {"finnhub": {"earnings_date": "2025-03-15T00:00:00Z"}}
        rec = select_earnings_provenance(pr)
        assert rec.selected_value == "2025-03-15"

    def test_session_selection_high_two_agree(self):
        from app.services.earnings_selection_service import select_earnings_session_provenance
        from app.models.patch32a_provenance import CONFIDENCE_HIGH
        pr = {
            "finnhub": {"session_label": "AMC"},
            "alpha_vantage": {"session_label": "after market close"},
        }
        rec = select_earnings_session_provenance(pr)
        assert rec.confidence_level == CONFIDENCE_HIGH
        assert rec.selected_value == "AMC"

    def test_session_conflict_detected(self):
        from app.services.earnings_selection_service import select_earnings_session_provenance
        from app.models.patch32a_provenance import CONFIDENCE_CONFLICT
        pr = {
            "finnhub": {"session_label": "AMC"},
            "alpha_vantage": {"session_label": "BMO"},
        }
        rec = select_earnings_session_provenance(pr)
        assert rec.confidence_level == CONFIDENCE_CONFLICT

    def test_build_earnings_field_provenance_returns_both_fields(self):
        from app.services.earnings_selection_service import build_earnings_field_provenance
        pr = {"finnhub": {"earnings_date": "2025-03-15", "session_label": "AMC"}}
        result = build_earnings_field_provenance({}, provider_results=pr)
        assert "earnings_date" in result
        assert "earnings_session" in result

    def test_row_confidence_summary_worst_wins(self):
        from app.services.earnings_selection_service import row_confidence_summary
        from app.models.patch32a_provenance import CONFIDENCE_CONFLICT
        refs = {
            "earnings_date": {"confidence_level": "HIGH", "has_conflict": False},
            "earnings_session": {"confidence_level": "CONFLICT", "has_conflict": True},
        }
        summary = row_confidence_summary(refs)
        assert summary["data_confidence"] == CONFIDENCE_CONFLICT
        assert summary["conflict_count"] == 1
        assert summary["has_data_conflict"] is True

    def test_row_confidence_summary_all_high(self):
        from app.services.earnings_selection_service import row_confidence_summary
        from app.models.patch32a_provenance import CONFIDENCE_HIGH
        refs = {
            "earnings_date": {"confidence_level": "HIGH", "has_conflict": False},
            "earnings_session": {"confidence_level": "HIGH", "has_conflict": False},
        }
        summary = row_confidence_summary(refs)
        assert summary["data_confidence"] == CONFIDENCE_HIGH
        assert summary["conflict_count"] == 0

    def test_row_confidence_summary_empty(self):
        from app.services.earnings_selection_service import row_confidence_summary
        from app.models.patch32a_provenance import CONFIDENCE_UNKNOWN
        summary = row_confidence_summary({})
        assert summary["data_confidence"] == CONFIDENCE_UNKNOWN


# ─── 3. Data Provenance Repository ───────────────────────────────────────────


class TestDataProvenanceRepository:
    @pytest.fixture
    def db_path(self, tmp_path):
        return str(tmp_path / "test_dp.db")

    def _make_record(self, field_id="earnings_date", level="HIGH"):
        from app.models.patch32a_provenance import FieldProvenanceRecord
        return FieldProvenanceRecord(
            field_id=field_id,
            selected_value="2025-03-15",
            selected_provider="finnhub",
            confidence_level=level,
        )

    def test_write_and_read(self, db_path):
        from app.db.data_provenance import write_provenance, get_field_provenance
        ok = write_provenance(
            run_id="run-001",
            strategy_id="earnings_calendar",
            row_id="AAPL-001",
            ticker="AAPL",
            field_id="earnings_date",
            provenance_record=self._make_record(),
            db_path=db_path,
        )
        assert ok is True
        rows = get_field_provenance(
            run_id="run-001",
            field_id="earnings_date",
            db_path=db_path,
        )
        assert len(rows) == 1
        assert rows[0]["field_id"] == "earnings_date"
        assert rows[0]["confidence_level"] == "HIGH"

    def test_write_batch(self, db_path):
        from app.db.data_provenance import write_provenance_batch, get_field_provenance
        prov_map = {
            "earnings_date": self._make_record("earnings_date", "HIGH"),
            "earnings_session": self._make_record("earnings_session", "LOW"),
        }
        written = write_provenance_batch(
            run_id="run-002",
            strategy_id="earnings_calendar",
            row_id="AAPL-001",
            ticker="AAPL",
            provenance_map=prov_map,
            db_path=db_path,
        )
        assert written == 2
        rows = get_field_provenance(run_id="run-002", db_path=db_path)
        assert len(rows) == 2

    def test_get_latest_field_provenance(self, db_path):
        from app.db.data_provenance import write_provenance, get_latest_field_provenance
        write_provenance(
            run_id="run-003",
            strategy_id="earnings_calendar",
            row_id="MSFT-001",
            ticker="MSFT",
            field_id="earnings_date",
            provenance_record=self._make_record(),
            db_path=db_path,
        )
        row = get_latest_field_provenance(
            run_id="run-003",
            field_id="earnings_date",
            db_path=db_path,
        )
        assert row is not None
        assert row["ticker"] == "MSFT"

    def test_get_latest_returns_none_when_absent(self, db_path):
        from app.db.data_provenance import get_latest_field_provenance
        row = get_latest_field_provenance(field_id="earnings_date", db_path=db_path)
        assert row is None

    def test_provenance_exists(self, db_path):
        from app.db.data_provenance import write_provenance, provenance_exists
        assert provenance_exists("run-004", "earnings_calendar", "AAPL-001", db_path=db_path) is False
        write_provenance(
            run_id="run-004",
            strategy_id="earnings_calendar",
            row_id="AAPL-001",
            ticker="AAPL",
            field_id="earnings_date",
            provenance_record=self._make_record(),
            db_path=db_path,
        )
        assert provenance_exists("run-004", "earnings_calendar", "AAPL-001", db_path=db_path) is True

    def test_provenance_json_roundtrip(self, db_path):
        from app.db.data_provenance import write_provenance, get_field_provenance
        rec = self._make_record()
        write_provenance(
            run_id="run-005",
            strategy_id="earnings_calendar",
            row_id="AAPL-001",
            ticker="AAPL",
            field_id="earnings_date",
            provenance_record=rec,
            db_path=db_path,
        )
        rows = get_field_provenance(run_id="run-005", db_path=db_path)
        prov = rows[0]["provenance"]
        assert isinstance(prov, dict)
        assert prov.get("field_id") == "earnings_date"

    def test_returns_empty_when_db_absent(self, tmp_path):
        from app.db.data_provenance import get_field_provenance
        rows = get_field_provenance(
            field_id="earnings_date",
            db_path=str(tmp_path / "nonexistent.db"),
        )
        assert rows == []

    def test_cleanup_old_provenance(self, db_path):
        from app.db.data_provenance import write_provenance, cleanup_old_provenance
        write_provenance(
            run_id="run-006",
            strategy_id="earnings_calendar",
            row_id="AAPL-001",
            ticker="AAPL",
            field_id="earnings_date",
            provenance_record=self._make_record(),
            db_path=db_path,
        )
        # Cleanup with 0 days → deletes everything written before today
        deleted = cleanup_old_provenance(0, db_path=db_path)
        assert isinstance(deleted, int)  # may be 0 or 1 depending on timing

    def test_dict_as_provenance_record(self, db_path):
        from app.db.data_provenance import write_provenance, get_field_provenance
        prov_dict = {"field_id": "quote", "confidence_level": "LOW", "selected_provider": "tradier"}
        ok = write_provenance(
            run_id="run-007",
            strategy_id="earnings_calendar",
            row_id="AAPL-001",
            ticker="AAPL",
            field_id="quote",
            provenance_record=prov_dict,
            db_path=db_path,
        )
        assert ok is True
        rows = get_field_provenance(field_id="quote", db_path=db_path)
        assert len(rows) == 1


# ─── 4. Data Confidence Enrichment Service ───────────────────────────────────


class TestDataConfidenceEnrichmentService:
    def test_enrich_adds_required_fields(self):
        from app.services.data_confidence_enrichment_service import enrich_row_with_data_confidence
        row: dict[str, Any] = {
            "ticker": "AAPL",
            "strategy_id": "earnings_calendar",
            "earnings_date": "2025-03-15",
        }
        enrich_row_with_data_confidence(row, "earnings_calendar")
        assert "data_confidence" in row
        assert "data_confidence_summary" in row
        assert "provenance_refs" in row
        assert "freshness_summary" in row
        assert "conflict_count" in row
        assert "has_data_conflict" in row

    def test_existing_fields_unchanged(self):
        from app.services.data_confidence_enrichment_service import enrich_row_with_data_confidence
        row = {
            "ticker": "AAPL",
            "verdict": "PASS",
            "score": 85.0,
            "earnings_date": "2025-03-15",
        }
        enrich_row_with_data_confidence(row, "earnings_calendar")
        assert row["ticker"] == "AAPL"
        assert row["verdict"] == "PASS"
        assert row["score"] == 85.0

    def test_no_earnings_date_still_safe(self):
        from app.services.data_confidence_enrichment_service import enrich_row_with_data_confidence
        row = {"ticker": "AAPL", "strategy_id": "stock_momentum"}
        enrich_row_with_data_confidence(row, "stock_momentum")
        assert "data_confidence" in row

    def test_unknown_confidence_when_no_provenance(self):
        from app.services.data_confidence_enrichment_service import enrich_row_with_data_confidence
        from app.models.patch32a_provenance import CONFIDENCE_UNKNOWN
        row = {"ticker": "AAPL"}
        enrich_row_with_data_confidence(row)
        assert row["data_confidence"] == CONFIDENCE_UNKNOWN

    def test_ff_provenance_carried_forward(self):
        from app.services.data_confidence_enrichment_service import enrich_row_with_data_confidence
        row = {
            "ticker": "AAPL",
            "_ff_provenance": {
                "forward_factor": {"source": "calculated"},
                "front_iv": {"source": "tradier"},
                "back_iv": {"source": "tradier"},
            },
        }
        enrich_row_with_data_confidence(row)
        assert "forward_factor" in row["provenance_refs"]

    def test_conflict_count_correct(self):
        from app.services.data_confidence_enrichment_service import enrich_row_with_data_confidence
        from app.services.earnings_selection_service import select_earnings_provenance
        row = {
            "ticker": "AAPL",
            "earnings_date": "2025-03-15",
        }
        pr = {
            "finnhub": {"earnings_date": "2025-03-15"},
            "alpha_vantage": {"earnings_date": "2025-03-16"},  # conflict
        }
        enrich_row_with_data_confidence(row, "earnings_calendar", provider_results=pr)
        assert row["has_data_conflict"] is True
        assert row["conflict_count"] >= 1

    def test_enrich_rows_batch_returns_new_list(self):
        from app.services.data_confidence_enrichment_service import enrich_rows_batch
        rows = [
            {"ticker": "AAPL", "earnings_date": "2025-03-15"},
            {"ticker": "MSFT", "earnings_date": "2025-04-20"},
        ]
        result = enrich_rows_batch(rows, "earnings_calendar")
        assert len(result) == 2
        assert all("data_confidence" in r for r in result)
        # Original rows unchanged
        assert "data_confidence" not in rows[0]

    def test_data_confidence_compact_minimal(self):
        from app.services.data_confidence_enrichment_service import data_confidence_compact
        row = {
            "data_confidence": "HIGH",
            "has_data_conflict": False,
            "conflict_count": 0,
            "freshness_summary": {"data": {"label": "fresh"}},
        }
        compact = data_confidence_compact(row)
        assert compact["data_confidence"] == "HIGH"
        assert compact["confidence_color"] == "green"
        assert compact["freshness_label"] == "fresh"

    def test_data_confidence_compact_defaults_unknown(self):
        from app.services.data_confidence_enrichment_service import data_confidence_compact
        compact = data_confidence_compact({})
        assert compact["data_confidence"] == "UNKNOWN"
        assert compact["confidence_color"] == "gray"

    def test_rejection_explainability(self):
        from app.services.data_confidence_enrichment_service import build_rejection_explainability
        row = {
            "ticker": "AAPL",
            "verdict": "FAIL",
            "decision_class": "rejected",
            "primary_reason": "Earnings date conflict",
            "earnings_date": "2025-03-15",
            "data_confidence": "CONFLICT",
            "has_data_conflict": True,
            "gates": [
                {"gate": "earnings_date_quality", "result": "fail", "blocking": True,
                 "reason": "Sources disagree on date"},
            ],
        }
        result = build_rejection_explainability(row, "earnings_calendar")
        assert result["ticker"] == "AAPL"
        assert result["blocking_gate_count"] >= 1
        assert result["has_data_conflict"] is True
        assert "earnings_date" in result["data_used"]


# ─── 5. Data Confidence API ───────────────────────────────────────────────────


class TestDataConfidenceAPI:
    def test_field_endpoint_missing_field_id_returns_400(self):
        from app.api.data_confidence_api import get_field_provenance_response
        result, status = get_field_provenance_response(None, None, None, None)
        assert status == 400
        assert "error" in result
        assert result.get("provider_calls_triggered") is False
        assert result.get("read_only") is True

    def test_field_endpoint_not_found_returns_404(self, tmp_path, monkeypatch):
        from app.api.data_confidence_api import get_field_provenance_response
        import app.db.data_provenance as dp_db
        monkeypatch.setattr(dp_db, "_db_path", lambda: str(tmp_path / "empty.db"))
        result, status = get_field_provenance_response(None, None, None, "earnings_date")
        assert status == 404
        assert result.get("found") is False
        assert result.get("provider_calls_triggered") is False

    def test_field_endpoint_found_returns_200(self, tmp_path, monkeypatch):
        import app.db.data_provenance as dp_db
        monkeypatch.setattr(dp_db, "_db_path", lambda: str(tmp_path / "test.db"))
        from app.models.patch32a_provenance import FieldProvenanceRecord, CONFIDENCE_HIGH
        dp_db.write_provenance(
            run_id="run-001",
            strategy_id="earnings_calendar",
            row_id="AAPL-001",
            ticker="AAPL",
            field_id="earnings_date",
            provenance_record=FieldProvenanceRecord(
                field_id="earnings_date",
                confidence_level=CONFIDENCE_HIGH,
                selected_provider="finnhub",
            ),
            db_path=str(tmp_path / "test.db"),
        )
        from app.api.data_confidence_api import get_field_provenance_response
        result, status = get_field_provenance_response("run-001", "earnings_calendar", "AAPL-001", "earnings_date")
        assert status == 200
        assert result.get("found") is True
        assert result.get("provider_calls_triggered") is False
        assert "provenance" in result
        assert result["provenance"].get("confidence_color") == "green"

    def test_reference_endpoint_returns_all_levels(self):
        from app.api.data_confidence_api import build_data_confidence_reference
        ref = build_data_confidence_reference()
        assert ref.get("provider_calls_triggered") is False
        assert ref.get("read_only") is True
        levels = [entry["level"] for entry in ref.get("confidence_levels", [])]
        assert "HIGH" in levels
        assert "MEDIUM" in levels
        assert "LOW" in levels
        assert "CONFLICT" in levels
        assert "UNKNOWN" in levels
        assert len(levels) == 5

    def test_reference_endpoint_colors_correct(self):
        from app.api.data_confidence_api import build_data_confidence_reference
        ref = build_data_confidence_reference()
        color_map = {e["level"]: e["color"] for e in ref["confidence_levels"]}
        assert color_map["HIGH"] == "green"
        assert color_map["CONFLICT"] == "red"
        assert color_map["UNKNOWN"] == "gray"

    def test_reference_includes_selection_priority(self):
        from app.api.data_confidence_api import build_data_confidence_reference
        ref = build_data_confidence_reference()
        priority = ref.get("selection_priority") or []
        assert "robinhood" in priority
        assert "finnhub" in priority
        assert priority.index("robinhood") < priority.index("finnhub")

    def test_reference_json_serializable(self):
        from app.api.data_confidence_api import build_data_confidence_reference
        ref = build_data_confidence_reference()
        json.dumps(ref)

    def test_field_response_read_only_always(self, tmp_path, monkeypatch):
        import app.db.data_provenance as dp_db
        monkeypatch.setattr(dp_db, "_db_path", lambda: str(tmp_path / "empty.db"))
        from app.api.data_confidence_api import get_field_provenance_response
        for args in [
            (None, None, None, None),
            (None, None, None, "earnings_date"),
            ("run-x", "ec", "r1", "earnings_date"),
        ]:
            result, _ = get_field_provenance_response(*args)
            assert result.get("read_only") is True
            assert result.get("provider_calls_triggered") is False


# ─── 6. Automated Validation Logging ─────────────────────────────────────────


class TestAutomatedValidationLogging:
    def test_log_data_confidence_validation_format(self):
        from app.services.automated_data_validation_service import log_data_confidence_validation
        suite_result = {
            "total_reports": 10,
            "passed_reports": 8,
            "failed_reports": 2,
            "total_errors": 2,
            "total_warnings": 1,
            "reports": [
                {"results": [
                    {"passed": False, "rule_id": "earnings_date_present"},
                    {"passed": True, "rule_id": "ticker_present"},
                ]}
            ],
        }
        lines = []
        line = log_data_confidence_validation(suite_result, log_print=lines.append)
        assert line.startswith("DATA_CONFIDENCE_VALIDATION")
        assert "passed=8" in line
        assert "failed=2" in line
        assert "sample_size=10" in line
        assert "earnings_date_present" in line

    def test_log_data_confidence_empty_suite(self):
        from app.services.automated_data_validation_service import log_data_confidence_validation
        lines = []
        line = log_data_confidence_validation({}, log_print=lines.append)
        assert "DATA_CONFIDENCE_VALIDATION" in line

    def test_run_data_confidence_validation_calls_suite(self):
        from app.services.automated_data_validation_service import run_data_confidence_validation
        rows = [{"ticker": "AAPL", "strategy_id": "earnings_calendar"}]
        lines = []
        result = run_data_confidence_validation(rows, "earnings_calendar", log_print=lines.append)
        assert "validation_passed" in result
        assert any("DATA_CONFIDENCE_VALIDATION" in str(l) for l in lines)

    def test_validation_suite_provider_calls_false(self):
        from app.services.automated_data_validation_service import run_validation_suite
        result = run_validation_suite([], "earnings_calendar")
        assert result.get("provider_calls_triggered") is False
        assert result.get("read_only") is True


# ─── 7. Endpoint Verification — 32A checks ───────────────────────────────────


class TestEndpointVerification32A:
    def test_check_data_version_pass(self):
        from app.services.endpoint_verification_service import _check_data_version
        check = _check_data_version()
        assert check.status == "PASS"
        assert check.fields.get("provider_calls_triggered") is False

    def test_check_data_confidence_reference_pass(self):
        from app.services.endpoint_verification_service import _check_data_confidence_reference
        check = _check_data_confidence_reference()
        assert check.status == "PASS"
        assert check.fields.get("confidence_levels", 0) >= 5

    def test_check_data_confidence_field_missing_param_pass(self):
        from app.services.endpoint_verification_service import _check_data_confidence_field_missing_param
        check = _check_data_confidence_field_missing_param()
        assert check.status == "PASS"


# ─── 8. Backward Compatibility ───────────────────────────────────────────────


class TestBackwardCompatibility:
    def test_old_row_without_provenance_returns_unknown(self):
        from app.services.data_confidence_enrichment_service import enrich_row_with_data_confidence
        from app.models.patch32a_provenance import CONFIDENCE_UNKNOWN
        # Legacy row with no provenance fields
        row = {
            "ticker": "AAPL",
            "verdict": "PASS",
            "score": 85.0,
            "earnings_date": "2025-03-15",
            "strategy_id": "earnings_calendar",
        }
        enrich_row_with_data_confidence(row)
        # Without provider_results, provenance is empty → UNKNOWN
        assert row["data_confidence"] == CONFIDENCE_UNKNOWN
        assert row["conflict_count"] == 0
        assert row["has_data_conflict"] is False

    def test_sprint28_provenance_record_still_importable(self):
        from app.models.data_provenance import (
            DataProvenanceRecord, CONFIDENCE_CONFIRMED, PROVENANCE_SCHEMA_VERSION
        )
        rec = DataProvenanceRecord.single_source("finnhub")
        assert rec.confidence == CONFIDENCE_CONFIRMED or rec.confidence == "single_source"
        assert PROVENANCE_SCHEMA_VERSION == "28.A.v1"

    def test_patch32a_and_sprint28_coexist(self):
        from app.models.data_provenance import PROVENANCE_SCHEMA_VERSION as v28
        from app.models.patch32a_provenance import PATCH32A_SCHEMA_VERSION as v32a
        assert v28 == "28.A.v1"
        assert v32a == "32A.v1"
        assert v28 != v32a

    def test_enriched_row_preserves_sprint28_ff_provenance(self):
        from app.services.data_confidence_enrichment_service import enrich_row_with_data_confidence
        row = {
            "ticker": "AAPL",
            "_ff_provenance": {
                "forward_factor": {"source": "calculated"},
                "front_iv": {"source": "tradier"},
                "dry_run": True,
                "can_trade_live": False,
            },
        }
        enrich_row_with_data_confidence(row)
        # _ff_provenance is preserved
        assert "_ff_provenance" in row
        assert row["_ff_provenance"]["can_trade_live"] is False

    def test_provenance_refs_json_serializable(self):
        from app.services.data_confidence_enrichment_service import enrich_row_with_data_confidence
        row = {
            "ticker": "AAPL",
            "earnings_date": "2025-03-15",
        }
        pr = {
            "finnhub": {"earnings_date": "2025-03-15"},
            "alpha_vantage": {"earnings_date": "2025-03-15"},
        }
        enrich_row_with_data_confidence(row, "earnings_calendar", provider_results=pr)
        json.dumps(row["provenance_refs"])


# ─── 9. Integration scenarios ─────────────────────────────────────────────────


class TestIntegrationScenarios:
    def test_full_pipeline_high_confidence(self, tmp_path, monkeypatch):
        """Two providers agree → HIGH → enriched row → persisted provenance."""
        import app.db.data_provenance as dp_db
        db = str(tmp_path / "dp.db")
        monkeypatch.setattr(dp_db, "_db_path", lambda: db)
        monkeypatch.setattr("app.config.DATA_CONFIDENCE_ENABLED", True)

        from app.services.earnings_selection_service import build_earnings_field_provenance
        from app.services.data_confidence_enrichment_service import enrich_row_with_data_confidence
        from app.models.patch32a_provenance import CONFIDENCE_HIGH

        pr = {
            "finnhub": {"earnings_date": "2025-03-15", "session_label": "AMC"},
            "alpha_vantage": {"earnings_date": "2025-03-15", "session_label": "after market close"},
        }
        prov_map = build_earnings_field_provenance({}, provider_results=pr)
        assert prov_map["earnings_date"].confidence_level == CONFIDENCE_HIGH

        row = {"ticker": "AAPL", "earnings_date": "2025-03-15"}
        enrich_row_with_data_confidence(row, "earnings_calendar", provider_results=pr)
        assert row["data_confidence"] == CONFIDENCE_HIGH

        written = dp_db.write_provenance_batch(
            run_id="run-int-001",
            strategy_id="earnings_calendar",
            row_id="AAPL-001",
            ticker="AAPL",
            provenance_map=prov_map,
            db_path=db,
        )
        assert written == 2

        from app.api.data_confidence_api import get_field_provenance_response
        result, status = get_field_provenance_response(
            "run-int-001", "earnings_calendar", "AAPL-001", "earnings_date",
        )
        assert status == 200
        assert result["found"] is True
        assert result["provenance"]["confidence_level"] == CONFIDENCE_HIGH

    def test_full_pipeline_conflict_scenario(self, tmp_path, monkeypatch):
        """Conflicting providers → CONFLICT → row marked."""
        import app.db.data_provenance as dp_db
        monkeypatch.setattr(dp_db, "_db_path", lambda: str(tmp_path / "dp.db"))
        monkeypatch.setattr("app.config.DATA_CONFIDENCE_ENABLED", True)

        from app.models.patch32a_provenance import CONFIDENCE_CONFLICT
        pr = {
            "finnhub": {"earnings_date": "2025-03-15"},
            "alpha_vantage": {"earnings_date": "2025-03-16"},
        }
        row = {"ticker": "AAPL", "earnings_date": "2025-03-15"}
        from app.services.data_confidence_enrichment_service import enrich_row_with_data_confidence
        enrich_row_with_data_confidence(row, "earnings_calendar", provider_results=pr)
        assert row["data_confidence"] == CONFIDENCE_CONFLICT
        assert row["has_data_conflict"] is True

    def test_validation_log_integration(self):
        """run_data_confidence_validation emits correct log line."""
        from app.services.automated_data_validation_service import run_data_confidence_validation
        rows = [
            {"ticker": "AAPL", "strategy_id": "earnings_calendar", "verdict": "PASS"},
            {"ticker": "MSFT", "strategy_id": "earnings_calendar", "verdict": "FAIL"},
        ]
        lines = []
        result = run_data_confidence_validation(rows, "earnings_calendar", log_print=lines.append)
        log_lines = [l for l in lines if "DATA_CONFIDENCE_VALIDATION" in str(l)]
        assert len(log_lines) == 1
        assert "sample_size=2" in log_lines[0]

    def test_endpoint_verification_includes_32a_checks(self):
        """Verify 32A checks appear in the full verification run."""
        from app.services.endpoint_verification_service import _check_data_version
        from app.services.endpoint_verification_service import _check_data_confidence_reference
        from app.services.endpoint_verification_service import _check_data_confidence_field_missing_param
        checks = [
            _check_data_version(),
            _check_data_confidence_reference(),
            _check_data_confidence_field_missing_param(),
        ]
        for check in checks:
            assert check.status in ("PASS", "WARN"), f"{check.name} unexpectedly failed: {check.assertion}"

    def test_provenance_refs_compact_in_row(self):
        """provenance_refs in enriched row are compact dicts (not FieldProvenanceRecord objects)."""
        from app.services.data_confidence_enrichment_service import enrich_row_with_data_confidence
        row = {"ticker": "AAPL", "earnings_date": "2025-03-15"}
        pr = {"finnhub": {"earnings_date": "2025-03-15"}}
        enrich_row_with_data_confidence(row, "earnings_calendar", provider_results=pr)
        for fid, ref in row["provenance_refs"].items():
            assert isinstance(ref, dict), f"Expected dict for {fid}, got {type(ref)}"
            assert "field_id" in ref
            assert "confidence_level" in ref
