"""
ASA Patch 30C — Earnings Calendar Journal Tests

Verifies that universal Earnings Calendar rows are journal-ready:
  - Required journal fields exist
  - Rejected rows journal compactly
  - Raw option chains are not journaled
  - details namespace survives
  - gates summary survives
  - No raw provider blobs in journal fields
"""
from __future__ import annotations

import py_compile


class TestCompile:
    def test_universal_compiles(self):
        py_compile.compile("app/strategies/earnings_calendar_universal.py", doraise=True)


# ─── Journal field readiness ───────────────────────────────────────────────────

class TestJournalFieldReadiness:
    def _build_row(self, action: str = "EARNINGS CALENDAR CANDIDATE") -> dict:
        from app.strategies.earnings_calendar_universal import build_earnings_calendar_universal_row
        row = {
            "ticker": "BAC",
            "strategy_id": "earnings_calendar",
            "action": action,
            "score": 72.0,
            "earnings_date": "2026-07-22",
            "earnings_trust_label": "multi_source_confirmed",
            "date_confidence": "high",
            "date_conflict": False,
            "earnings_relation": "long_leg_captures_earnings",
            "front_expiration": "2026-07-18",
            "back_expiration": "2026-07-25",
            "calendar_entry_allowed": action == "EARNINGS CALENDAR CANDIDATE",
            "daily_opportunity_eligible": action == "EARNINGS CALENDAR CANDIDATE",
            "reasons": ["Preferred structure: short leg expires before earnings."],
        }
        build_earnings_calendar_universal_row(row, run_id="run-ec-001")
        return row

    def test_strategy_id_present(self):
        row = self._build_row()
        assert row.get("strategy_id") == "earnings_calendar"

    def test_row_id_present(self):
        row = self._build_row()
        assert isinstance(row.get("row_id"), str)

    def test_row_type_present(self):
        row = self._build_row()
        assert row.get("row_type") in ("new_candidate", "rejected_candidate", "observation")

    def test_ticker_present(self):
        row = self._build_row()
        assert row.get("ticker") == "BAC"

    def test_score_present(self):
        row = self._build_row()
        assert row.get("score") == 72.0

    def test_details_namespace_present(self):
        row = self._build_row()
        assert "earnings_calendar" in (row.get("details") or {})

    def test_gate_groups_present(self):
        row = self._build_row()
        assert isinstance(row.get("gate_groups"), dict)

    def test_daily_opportunity_dict_present(self):
        row = self._build_row()
        assert isinstance(row.get("daily_opportunity"), dict)

    def test_schema_version_present(self):
        from app.strategies.schema import SCHEMA_VERSION
        row = self._build_row()
        assert row.get("schema_version") == SCHEMA_VERSION


# ─── Rejected rows journal compactly ──────────────────────────────────────────

class TestRejectedRowsJournalCompactly:
    def _build_rejected(self) -> dict:
        from app.strategies.earnings_calendar_universal import build_earnings_calendar_universal_row
        row = {
            "ticker": "FAST",
            "strategy_id": "earnings_calendar",
            "action": "FAIL / EARNINGS DATE CONFLICT",
            "score": 30.0,
            "earnings_date": "2026-07-14",
            "earnings_trust_label": "conflict_do_not_trade",
            "date_confidence": "low",
            "date_conflict": True,
            "earnings_relation": "long_leg_captures_earnings",
            "calendar_entry_allowed": False,
            "daily_opportunity_eligible": False,
            "reasons": ["Earnings date conflict between providers."],
        }
        build_earnings_calendar_universal_row(row, run_id="run-ec-001")
        return row

    def test_rejected_row_has_row_type_rejected_candidate(self):
        row = self._build_rejected()
        assert row["row_type"] == "rejected_candidate"

    def test_rejected_row_has_details(self):
        row = self._build_rejected()
        assert "earnings_calendar" in (row.get("details") or {})

    def test_rejected_row_conflict_gate_is_fail(self):
        row = self._build_rejected()
        assert row["gate_groups"]["event"]["earnings_conflict"]["status"] == "fail"

    def test_rejected_row_trust_gate_is_fail(self):
        row = self._build_rejected()
        assert row["gate_groups"]["event"]["earnings_source_quality"]["status"] == "fail"

    def test_rejected_row_not_eligible(self):
        row = self._build_rejected()
        assert row["daily_opportunity"]["eligible"] is False


# ─── Raw chain exclusion ──────────────────────────────────────────────────────

class TestRawChainExclusion:
    def _build_row_with_chain(self) -> dict:
        from app.strategies.earnings_calendar_universal import build_earnings_calendar_universal_row
        row = {
            "ticker": "BAC",
            "strategy_id": "earnings_calendar",
            "action": "EARNINGS CALENDAR CANDIDATE",
            "score": 72.0,
            "calendar_entry_allowed": True,
            "daily_opportunity_eligible": True,
            "reasons": ["Preferred structure."],
            # Simulate large raw chain that should NOT appear in details
            "short_front_leg": {"symbol": "BAC260718C00045000", "bid": 0.45, "ask": 0.50},
            "long_back_leg": {"symbol": "BAC260725C00045000", "bid": 0.80, "ask": 0.90},
            "base_calendar_candidate": {"very": "large", "data": "here"},
        }
        build_earnings_calendar_universal_row(row, run_id="run-ec-001")
        return row

    def test_details_does_not_contain_raw_legs(self):
        row = self._build_row_with_chain()
        ec = row["details"]["earnings_calendar"]
        assert "short_front_leg" not in ec
        assert "long_back_leg" not in ec

    def test_details_does_not_contain_base_candidate(self):
        row = self._build_row_with_chain()
        ec = row["details"]["earnings_calendar"]
        assert "base_calendar_candidate" not in ec

    def test_raw_fields_still_on_parent_row(self):
        row = self._build_row_with_chain()
        # Raw fields stay on the outer row (legacy); only excluded from details
        assert "short_front_leg" in row
        assert "long_back_leg" in row

    def test_gate_groups_custom_does_not_embed_raw_chains(self):
        row = self._build_row_with_chain()
        import json
        # Serialize and measure; no raw chain data should appear in custom fields
        raw_json = json.dumps(row["gate_groups"])
        assert "base_calendar_candidate" not in raw_json


# ─── Details namespace completeness ───────────────────────────────────────────

class TestDetailsNamespaceCompleteness:
    def test_details_namespace_is_earnings_calendar(self):
        from app.strategies.earnings_calendar_universal import build_earnings_calendar_universal_row
        row = {"ticker": "BAC", "action": "EARNINGS CALENDAR CANDIDATE", "score": 72.0, "reasons": []}
        build_earnings_calendar_universal_row(row)
        assert "earnings_calendar" in row["details"]
        assert "stock_momentum" not in row["details"]

    def test_lifecycle_details_namespace_is_earnings_calendar(self):
        from app.strategies.earnings_calendar_universal import build_earnings_lifecycle_universal_row
        check = {"ticker": "SBUX", "action": "HOLD / MONITOR", "lifecycle_priority_score": 30.0}
        build_earnings_lifecycle_universal_row(check)
        assert "earnings_calendar" in check["details"]
