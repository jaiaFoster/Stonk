"""
ASA Patch 30D — Skew Momentum Vertical Journal Tests

Verifies that universal Skew rows are journal-ready:
  - Required journal fields exist
  - Rejected rows journal compactly
  - Raw option legs are not journaled in details
  - details namespace is skew_momentum_vertical
  - gates summary survives
  - No raw provider blobs in journal fields
"""
from __future__ import annotations

import json
import py_compile


class TestCompile:
    def test_universal_compiles(self):
        py_compile.compile("app/strategies/skew_momentum_vertical_universal.py", doraise=True)


# ─── Journal field readiness ───────────────────────────────────────────────────

class TestJournalFieldReadiness:
    def _build_row(self, verdict: str = "PASS / POSSIBLE ENTRY SETUP") -> dict:
        from app.strategies.skew_momentum_vertical_universal import build_skew_momentum_vertical_universal_row
        row = {
            "ticker": "AAPL",
            "strategy_id": "skew_momentum_vertical",
            "verdict": verdict,
            "score": 72.0,
            "direction": "bullish",
            "momentum_confirmed": True,
            "skew_pass": verdict.startswith("PASS"),
            "liquidity_pass": verdict.startswith("PASS"),
            "data_quality_pass": True,
            "event_risk": False,
            "possible_spread": {
                "expiration": "2026-08-15",
                "option_type": "call",
                "long_strike": 185.0,
                "short_strike": 190.0,
                "width": 5.0,
                "conservative_debit": 1.85,
            },
            "dte": 39,
            "underlying_price": 186.50,
            "conservative_debit": 1.85,
            "max_risk": 185.0,
            "reward_risk": 1.70,
            "daily_opportunity_eligible": verdict.startswith("PASS"),
        }
        build_skew_momentum_vertical_universal_row(row, run_id="run-smv-001")
        return row

    def test_strategy_id_present(self):
        row = self._build_row()
        assert row.get("strategy_id") == "skew_momentum_vertical"

    def test_row_id_present(self):
        row = self._build_row()
        assert isinstance(row.get("row_id"), str)

    def test_row_type_present(self):
        from app.strategies.schema import VALID_ROW_TYPES
        row = self._build_row()
        assert row.get("row_type") in VALID_ROW_TYPES

    def test_ticker_present(self):
        row = self._build_row()
        assert row.get("ticker") == "AAPL"

    def test_score_present(self):
        row = self._build_row()
        assert row.get("score") == 72.0

    def test_details_namespace_present(self):
        row = self._build_row()
        assert "skew_momentum_vertical" in (row.get("details") or {})

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

    def test_display_present(self):
        row = self._build_row()
        assert isinstance(row.get("display"), dict)


# ─── Rejected rows journal compactly ──────────────────────────────────────────

class TestRejectedRowsJournalCompactly:
    def _build_rejected(self) -> dict:
        from app.strategies.skew_momentum_vertical_universal import build_skew_momentum_vertical_universal_row
        row = {
            "ticker": "TSLA",
            "strategy_id": "skew_momentum_vertical",
            "verdict": "FAIL / DTE TOO SHORT",
            "score": 0.0,
            "direction": "bullish",
            "momentum_confirmed": True,
            "skew_pass": False,
            "liquidity_pass": False,
            "data_quality_pass": False,
            "event_risk": False,
            "earnings_trust_label": "",
            "daily_opportunity_eligible": False,
        }
        build_skew_momentum_vertical_universal_row(row, run_id="run-smv-001")
        return row

    def test_rejected_row_has_row_type_rejected_candidate(self):
        row = self._build_rejected()
        assert row["row_type"] == "rejected_candidate"

    def test_rejected_row_has_details(self):
        row = self._build_rejected()
        assert "skew_momentum_vertical" in (row.get("details") or {})

    def test_rejected_row_not_eligible(self):
        row = self._build_rejected()
        assert row["daily_opportunity"]["eligible"] is False

    def test_rejected_row_details_compact(self):
        row = self._build_rejected()
        ec_str = json.dumps(row["details"]["skew_momentum_vertical"])
        assert len(ec_str) < 5_000, f"Rejected row details too large: {len(ec_str)} bytes"


# ─── Raw leg exclusion ────────────────────────────────────────────────────────

class TestRawLegExclusion:
    def _build_row_with_legs(self) -> dict:
        from app.strategies.skew_momentum_vertical_universal import build_skew_momentum_vertical_universal_row
        row = {
            "ticker": "AAPL",
            "strategy_id": "skew_momentum_vertical",
            "verdict": "PASS / POSSIBLE ENTRY SETUP",
            "score": 72.0,
            "momentum_confirmed": True,
            "skew_pass": True,
            "liquidity_pass": True,
            "data_quality_pass": True,
            "event_risk": False,
            "possible_spread": {
                "expiration": "2026-08-15",
                "option_type": "call",
                "long_strike": 185.0,
                "short_strike": 190.0,
                "width": 5.0,
                "conservative_debit": 1.85,
            },
            "dte": 39,
            "underlying_price": 186.50,
            "conservative_debit": 1.85,
            "max_risk": 185.0,
            "reward_risk": 1.70,
            # Raw option legs — should NOT appear in details
            "long_leg": {"bid": 1.80, "ask": 1.90, "iv": 0.28, "delta": 0.52},
            "short_leg": {"bid": 0.05, "ask": 0.10, "iv": 0.32, "delta": 0.12},
            "payload": {"long_leg": {}, "short_leg": {}, "market_metrics": {}},
            "requirements": [{"name": "Momentum", "status": "PASS"}],
            "daily_opportunity_eligible": True,
        }
        build_skew_momentum_vertical_universal_row(row, run_id="run-smv-001")
        return row

    def test_details_does_not_contain_long_leg(self):
        row = self._build_row_with_legs()
        ec = row["details"]["skew_momentum_vertical"]
        assert "long_leg" not in ec

    def test_details_does_not_contain_short_leg(self):
        row = self._build_row_with_legs()
        ec = row["details"]["skew_momentum_vertical"]
        assert "short_leg" not in ec

    def test_details_does_not_contain_payload(self):
        row = self._build_row_with_legs()
        ec = row["details"]["skew_momentum_vertical"]
        assert "payload" not in ec

    def test_details_does_not_contain_requirements(self):
        row = self._build_row_with_legs()
        ec = row["details"]["skew_momentum_vertical"]
        assert "requirements" not in ec

    def test_raw_fields_still_on_parent_row(self):
        row = self._build_row_with_legs()
        # Raw fields stay on the outer row (legacy)
        assert "long_leg" in row
        assert "short_leg" in row

    def test_gate_groups_no_raw_legs(self):
        row = self._build_row_with_legs()
        gg_str = json.dumps(row["gate_groups"])
        assert "raw_chain" not in gg_str


# ─── Details namespace completeness ───────────────────────────────────────────

class TestDetailsNamespaceCompleteness:
    def test_details_namespace_is_skew_momentum_vertical(self):
        from app.strategies.skew_momentum_vertical_universal import build_skew_momentum_vertical_universal_row
        row = {"ticker": "AAPL", "verdict": "PASS / POSSIBLE ENTRY SETUP", "score": 72.0}
        build_skew_momentum_vertical_universal_row(row)
        assert "skew_momentum_vertical" in row["details"]
        assert "earnings_calendar" not in row["details"]
        assert "stock_momentum" not in row["details"]
