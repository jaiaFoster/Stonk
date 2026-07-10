"""TKT-CALENDAR-REJECTED-ELIGIBILITY — persistence boundary eligibility invariants.

Rejected calendar candidates must emerge from the persistence boundary with:
- daily_opportunity_eligible = False (never appear in daily opportunity)
- journal_eligible = True (rejected rows ARE valid journal entries — learn from them)
- eligibility_status = "ineligible" (overridden at persistence boundary)
- action_type = None / null (no action to take on a rejected row)

Asserts that both the normalization service invariant (31B.G, updated) and the
persistence boundary invariant (31B.1 Ticket 3) agree on the correct values.
"""
from __future__ import annotations

import sys
import types

# ── pyo3 panic guard ──────────────────────────────────────────────────────────
_rh_stub = types.ModuleType("robin_stocks")
_rh_stub.robinhood = types.ModuleType("robin_stocks.robinhood")
sys.modules.setdefault("robin_stocks", _rh_stub)
sys.modules.setdefault("robin_stocks.robinhood", _rh_stub.robinhood)

import pytest


def _make_rejected_row(ticker: str = "AAPL") -> dict:
    return {
        "ticker": ticker,
        "strategy_id": "earnings_calendar",
        "row_type": "rejected_candidate",
        "verdict": "FAIL / ENTRY WINDOW CLOSED",
        "score": 0.1,
        "daily_opportunity_eligible": False,
        "decision_class": "rejected",
        "action_type": "calendar_entry",  # wrong — invariant should override to None
        "eligibility_status": "eligible",  # wrong — invariant should set to ineligible
    }


class TestNormalizationServiceRejectedRowInvariant:
    """31B.G invariant in strategy_row_normalization_service — updated for Ticket 3."""

    def _normalize(self, row: dict, strategy_id: str = "earnings_calendar") -> dict:
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        row_copy = dict(row)
        normalize_strategy_row(row_copy, strategy_id)
        return row_copy

    def test_rejected_row_journal_eligible_is_true(self):
        """Rejected rows must be journal-eligible (spec: TKT-CALENDAR-REJECTED-ELIGIBILITY)."""
        row = _make_rejected_row()
        result = self._normalize(row)
        assert result.get("journal_eligible") is True, (
            f"Expected journal_eligible=True for rejected_candidate, got {result.get('journal_eligible')!r}"
        )

    def test_rejected_row_daily_opportunity_eligible_is_false(self):
        row = _make_rejected_row()
        row["daily_opportunity_eligible"] = True  # should be overridden
        result = self._normalize(row)
        assert result.get("daily_opportunity_eligible") is False

    def test_rejected_row_decision_class_is_rejected(self):
        row = _make_rejected_row()
        result = self._normalize(row)
        assert result.get("decision_class") == "rejected"

    def test_fail_verdict_triggers_invariant(self):
        """A FAIL verdict triggers the rejected-row invariant even without row_type set."""
        row = {
            "ticker": "SBUX",
            "strategy_id": "earnings_calendar",
            "verdict": "FAIL / DEBIT TOO LARGE",
            "daily_opportunity_eligible": True,
        }
        result = self._normalize(row, "earnings_calendar")
        assert result.get("daily_opportunity_eligible") is False
        assert result.get("journal_eligible") is True

    def test_pass_verdict_not_affected(self):
        """PASS rows must NOT have journal_eligible forced — let the default compute."""
        row = {
            "ticker": "AAPL",
            "strategy_id": "earnings_calendar",
            "verdict": "PASS / ENTRY WINDOW OPEN",
            "daily_opportunity_eligible": True,
            "decision_class": "entry",
            "action_type": "calendar_entry",
            "eligibility_status": "eligible",
        }
        result = self._normalize(row, "earnings_calendar")
        # PASS rows should remain eligible.
        assert result.get("daily_opportunity_eligible") is True
        assert result.get("decision_class") != "rejected"


class TestPersistenceBoundaryRejectedInvariant:
    """Rejected_candidate rows must have correct fields at the DB persistence boundary."""

    def _write_and_read(self, row: dict, strategy_id: str = "earnings_calendar", tmp_path=None) -> dict:
        import tempfile, os
        from app.services.strategy_row_repository import StrategyRowRepository
        db_path = str(tmp_path / "test.db") if tmp_path else ":memory:"
        # Can't use :memory: for multiple connections, use temp file.
        if db_path == ":memory:":
            import tempfile
            tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
            db_path = tmp.name
            tmp.close()
        repo = StrategyRowRepository(db_path=db_path)
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        normalized = dict(row)
        normalize_strategy_row(normalized, strategy_id)
        normalized.setdefault("row_id", f"{strategy_id}-{row.get('ticker', 'UNKNOWN')}-test")
        result = repo.write_run("test-run-1", {
            strategy_id: {"canonical_opportunities": [normalized], "errors": []}
        })
        stored = repo.read_latest(strategy_id)
        rows = stored.get("rows") or []
        return rows[0] if rows else {}

    def test_rejected_candidate_eligibility_status_is_ineligible(self, tmp_path):
        """Persistence boundary: rejected_candidate must not carry 'eligible' status.

        _decision_semantics sets 'excluded' for rejected rows (more specific than 'ineligible'),
        so the stored value is in the ineligible family, never 'eligible'.
        """
        row = _make_rejected_row()
        stored = self._write_and_read(row, tmp_path=tmp_path)
        stored_elig = stored.get("eligibility_status")
        assert stored_elig in {"ineligible", "excluded", "blocked", "dry_run_excluded"}, (
            f"Expected an ineligible status, got {stored_elig!r}"
        )
        assert stored_elig != "eligible", f"Rejected candidate must not be 'eligible'; got {stored_elig!r}"

    def test_rejected_candidate_action_type_is_none(self, tmp_path):
        """Persistence boundary: rejected_candidate.action_type must be None or 'none' (no action)."""
        row = _make_rejected_row()
        stored = self._write_and_read(row, tmp_path=tmp_path)
        actual = stored.get("action_type")
        # "none" (string sentinel) and None (null) both represent "no action to take".
        assert actual in (None, "none"), (
            f"Expected None or 'none', got {actual!r}"
        )

    def test_rejected_candidate_daily_opportunity_false(self, tmp_path):
        row = _make_rejected_row()
        stored = self._write_and_read(row, tmp_path=tmp_path)
        assert stored.get("daily_opportunity_eligible") is False

    def test_rejected_candidate_daily_opportunity_overridden_when_true_in_input(self, tmp_path):
        """Persistence boundary must override daily_opportunity_eligible=True for rejected rows.

        This is the exact failure mode from VERIFY[FAIL] invalid_eligible_rejected_rows=3:
        the input row carries daily_opportunity_eligible=True but the boundary must force 0.
        """
        row = _make_rejected_row()
        row["daily_opportunity_eligible"] = True  # adversarial input — boundary must override
        stored = self._write_and_read(row, tmp_path=tmp_path)
        actual = stored.get("daily_opportunity_eligible")
        assert actual is False or actual == 0, (
            f"Persistence boundary must force daily_opportunity_eligible=False/0 for rejected_candidate; "
            f"got {actual!r} (input had True)"
        )

    def test_non_rejected_row_eligibility_preserved(self, tmp_path):
        """Active lifecycle rows must NOT have eligibility overridden to ineligible."""
        from app.services.strategy_row_repository import StrategyRowRepository
        repo = StrategyRowRepository(db_path=str(tmp_path / "test2.db"))
        lifecycle_row = {
            "ticker": "SBUX",
            "strategy_id": "earnings_calendar",
            "row_type": "lifecycle_check",
            "verdict": "HOLD / MONITOR",
            "daily_opportunity_eligible": False,
            "decision_class": "lifecycle",
            "action_type": "active_calendar",
            "eligibility_status": "eligible",
            "row_id": "sbux-lifecycle-1",
        }
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        normalized = dict(lifecycle_row)
        normalize_strategy_row(normalized, "earnings_calendar")
        repo.write_run("run-lc-1", {
            "earnings_calendar": {"canonical_opportunities": [normalized], "errors": []}
        })
        stored = repo.read_latest("earnings_calendar")
        rows = stored.get("rows") or []
        assert rows, "Expected lifecycle row to be stored"
        row = rows[0]
        # lifecycle rows should NOT have eligibility_status forced to ineligible.
        assert row.get("eligibility_status") != "ineligible" or row.get("row_type") != "lifecycle_check"


class TestObservationKeyOptionTypeDistinction:
    """observation_key must distinguish call vs put calendars on the same ticker."""

    def _get_key(self, row: dict, strategy_id: str = "earnings_calendar") -> str:
        from app.services.strategy_row_normalization_service import _observation_key
        return _observation_key(row, strategy_id)

    def test_call_and_put_calendars_have_distinct_observation_keys(self):
        call_row = {
            "ticker": "SBUX",
            "option_type": "call",
            "front_expiration": "2026-08-21",
            "structure_type": "calendar_spread",
        }
        put_row = {
            "ticker": "SBUX",
            "option_type": "put",
            "front_expiration": "2026-08-21",
            "structure_type": "calendar_spread",
        }
        call_key = self._get_key(call_row)
        put_key = self._get_key(put_row)
        assert call_key != put_key, (
            f"call_key={call_key!r} and put_key={put_key!r} must be distinct"
        )

    def test_same_option_type_same_key(self):
        row_a = {"ticker": "SBUX", "option_type": "call", "front_expiration": "2026-08-21"}
        row_b = {"ticker": "SBUX", "option_type": "call", "front_expiration": "2026-08-21"}
        assert self._get_key(row_a) == self._get_key(row_b)

    def test_no_option_type_produces_key(self):
        """Rows without option_type still get a valid observation key."""
        row = {"ticker": "AAPL", "front_expiration": "2026-09-18"}
        key = self._get_key(row)
        assert key.startswith("earnings_calendar:AAPL")
