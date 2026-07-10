"""TKT-OPEN-POSITIONS-LIFECYCLE-COMPLETENESS — both call and put calendars survive.

When a ticker (e.g. SBUX) has both an open call calendar and an open put calendar,
both must appear as distinct entries through the open-positions endpoint:
- active_calendar_count == 2
- Each structure has the correct option_type
- Neither calendar replaces the other due to a collision in row_id / observation_key

Root cause: _hash_row() and _observation_key() previously omitted option_type, so
two rows with the same ticker/front/back but different option_type got the same
row_id and the second INSERT OR REPLACE discarded the first.

Fix:
1. _hash_row() now includes option_type in the hash key.
2. _structure_summary() now includes option_type in the extracted fields.
3. _observation_key() for earnings_calendar now appends option_type to structure_type.
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


def _make_lifecycle_row(ticker: str, option_type: str, front: str = "2026-08-21", back: str = "2026-09-18", strike: float = 110.0) -> dict:
    return {
        "ticker": ticker,
        "strategy_id": "earnings_calendar",
        "row_type": "lifecycle_check",
        "verdict": "HOLD / MONITOR",
        "option_type": option_type,
        "front_expiration": front,
        "back_expiration": back,
        "strike": strike,
        "structure_type": "calendar_spread",
        "decision_class": "lifecycle",
        "action_type": "active_calendar",
        "eligibility_status": "eligible",
        "daily_opportunity_eligible": False,
    }


class TestHashRowDistinguishesOptionType:
    """_hash_row must produce distinct hashes for call vs put calendars on the same ticker."""

    def test_call_and_put_have_distinct_hashes(self):
        from app.services.strategy_row_repository import _hash_row
        call_row = _make_lifecycle_row("SBUX", "call")
        put_row = _make_lifecycle_row("SBUX", "put")
        call_hash = _hash_row("earnings_calendar", call_row)
        put_hash = _hash_row("earnings_calendar", put_row)
        assert call_hash != put_hash, (
            f"Expected distinct hashes: call={call_hash!r}, put={put_hash!r}"
        )

    def test_same_option_type_same_hash(self):
        from app.services.strategy_row_repository import _hash_row
        row_a = _make_lifecycle_row("SBUX", "call")
        row_b = _make_lifecycle_row("SBUX", "call")
        assert _hash_row("earnings_calendar", row_a) == _hash_row("earnings_calendar", row_b)

    def test_no_option_type_produces_consistent_hash(self):
        from app.services.strategy_row_repository import _hash_row
        row = {"ticker": "AAPL", "verdict": "HOLD", "front_expiration": "2026-08-21"}
        h1 = _hash_row("earnings_calendar", row)
        h2 = _hash_row("earnings_calendar", row)
        assert h1 == h2


class TestStructureSummaryIncludesOptionType:
    """_structure_summary must include option_type so the API can distinguish call vs put."""

    def test_option_type_included_in_summary(self):
        from app.services.strategy_row_repository import _structure_summary
        row = _make_lifecycle_row("SBUX", "call")
        summary = _structure_summary(row)
        assert "option_type" in summary
        assert summary["option_type"] == "call"

    def test_put_option_type_included(self):
        from app.services.strategy_row_repository import _structure_summary
        row = _make_lifecycle_row("SBUX", "put")
        summary = _structure_summary(row)
        assert summary.get("option_type") == "put"

    def test_no_option_type_not_in_summary(self):
        from app.services.strategy_row_repository import _structure_summary
        row = {"ticker": "AAPL", "front_expiration": "2026-08-21", "back_expiration": "2026-09-18", "strike": 100.0}
        summary = _structure_summary(row)
        # option_type should not appear if None (filtered by `if row.get(key) is not None`).
        assert summary.get("option_type") is None or "option_type" not in summary


class TestBothCalendarsStoredAndRetrieved:
    """Both SBUX call and put calendars must be stored as distinct rows and retrieved."""

    def test_call_and_put_both_stored(self, tmp_path):
        from app.services.strategy_row_repository import StrategyRowRepository
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        repo = StrategyRowRepository(db_path=str(tmp_path / "test.db"))
        call_row = _make_lifecycle_row("SBUX", "call")
        put_row = _make_lifecycle_row("SBUX", "put")
        # Normalize both rows to simulate the production pipeline.
        for row in (call_row, put_row):
            normalize_strategy_row(row, "earnings_calendar")
        repo.write_run(
            "run-dual-calendar-1",
            {"earnings_calendar": {"canonical_opportunities": [call_row, put_row], "errors": []}},
        )
        result = repo.read_latest("earnings_calendar")
        rows = result.get("rows") or []
        assert len(rows) == 2, (
            f"Expected 2 rows (call + put), got {len(rows)}. "
            "Likely row_id collision — option_type missing from hash."
        )

    def test_call_and_put_have_distinct_row_ids(self, tmp_path):
        from app.services.strategy_row_repository import StrategyRowRepository, _hash_row
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        call_row = _make_lifecycle_row("SBUX", "call")
        put_row = _make_lifecycle_row("SBUX", "put")
        for row in (call_row, put_row):
            normalize_strategy_row(row, "earnings_calendar")
        call_row_id = call_row.get("observation_key") or _hash_row("earnings_calendar", call_row)
        put_row_id = put_row.get("observation_key") or _hash_row("earnings_calendar", put_row)
        assert call_row_id != put_row_id, (
            f"Call and put row IDs must be distinct: {call_row_id!r} == {put_row_id!r}"
        )

    def test_active_calendar_count_is_two(self, tmp_path):
        from app.services.strategy_row_repository import StrategyRowRepository
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        from app.api.open_positions_api import _open_positions_from_row_store
        import unittest.mock as mock
        repo = StrategyRowRepository(db_path=str(tmp_path / "pos.db"))
        call_row = _make_lifecycle_row("SBUX", "call")
        put_row = _make_lifecycle_row("SBUX", "put")
        for row in (call_row, put_row):
            normalize_strategy_row(row, "earnings_calendar")
        repo.write_run(
            "run-active-count",
            {"earnings_calendar": {"canonical_opportunities": [call_row, put_row], "errors": []}},
        )
        # Patch at the module where StrategyRowRepository is defined and imported from.
        with mock.patch("app.services.strategy_row_repository.StrategyRowRepository", return_value=repo):
            response = _open_positions_from_row_store()
        active_count = response.get("active_calendar_count", 0)
        assert active_count == 2, (
            f"Expected active_calendar_count=2, got {active_count}. "
            f"Response: {response.get('calendar_structures', [])}"
        )

    def test_calendar_structures_have_distinct_option_types(self, tmp_path):
        from app.services.strategy_row_repository import StrategyRowRepository
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        from app.api.open_positions_api import _open_positions_from_row_store
        import unittest.mock as mock
        repo = StrategyRowRepository(db_path=str(tmp_path / "pos2.db"))
        call_row = _make_lifecycle_row("SBUX", "call")
        put_row = _make_lifecycle_row("SBUX", "put")
        for row in (call_row, put_row):
            normalize_strategy_row(row, "earnings_calendar")
        repo.write_run(
            "run-opt-types",
            {"earnings_calendar": {"canonical_opportunities": [call_row, put_row], "errors": []}},
        )
        with mock.patch("app.services.strategy_row_repository.StrategyRowRepository", return_value=repo):
            response = _open_positions_from_row_store()
        structures = response.get("calendar_structures") or []
        option_types = {s.get("option_type") for s in structures}
        assert "call" in option_types, f"Expected 'call' in option_types, got {option_types}"
        assert "put" in option_types, f"Expected 'put' in option_types, got {option_types}"
