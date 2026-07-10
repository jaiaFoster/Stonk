"""
ASA Patch 30C — Earnings Calendar Payload Discipline Tests

Verifies that:
  - Universal rows exclude raw option chains from details
  - Universal rows exclude full provider blobs
  - details.earnings_calendar fields are compact and serializable
  - gate_groups.custom fields are compact
  - Payload discipline is preserved (no bloat from universal enrichment)
  - CAVEMAN MODE safety: FF dry-run, no broker writes, no trade execution
"""
from __future__ import annotations

import json
import py_compile


class TestCompile:
    def test_universal_compiles(self):
        py_compile.compile("app/strategies/earnings_calendar_universal.py", doraise=True)


# ─── Details compactness ──────────────────────────────────────────────────────

class TestDetailsCompactness:
    def _build_row(self) -> dict:
        from app.strategies.earnings_calendar_universal import build_earnings_calendar_universal_row
        row = {
            "ticker": "BAC",
            "action": "EARNINGS CALENDAR CANDIDATE",
            "score": 72.0,
            "earnings_date": "2026-07-22",
            "earnings_trust_label": "multi_source_confirmed",
            "earnings_sources_seen": ["finnhub", "tradier"],
            "date_confidence": "high",
            "date_conflict": False,
            "earnings_relation": "long_leg_captures_earnings",
            "front_expiration": "2026-07-18",
            "back_expiration": "2026-07-25",
            "strike": 45.0,
            "underlying_price": 44.50,
            "front_iv": 0.42,
            "back_iv": 0.58,
            "iv_edge": 0.16,
            "iv_relationship_status": "favorable",
            "conservative_debit": 0.35,
            "debit_pct_underlying": 0.79,
            "max_leg_spread_pct": 3.2,
            "min_leg_open_interest": 120,
            "min_leg_volume": 35,
            "calendar_entry_allowed": True,
            "liquidity_status": "pass",
            "spread_status": "pass",
            "debit_status": "pass",
            "structure_status": "long_leg_captures_earnings",
            "daily_opportunity_eligible": True,
            "reasons": ["Preferred structure."],
            # Simulate large raw data that should NOT bloat details
            "base_calendar_candidate": {"symbol": "BAC", "raw": "x" * 2000},
            "short_front_leg": {"bid": 0.45, "ask": 0.50, "chain": ["a"] * 50},
            "long_back_leg": {"bid": 0.80, "ask": 0.90, "chain": ["b"] * 50},
        }
        build_earnings_calendar_universal_row(row)
        return row

    def test_details_is_serializable(self):
        row = self._build_row()
        # Should not raise
        serialized = json.dumps(row["details"])
        assert len(serialized) > 0

    def test_details_earnings_calendar_excludes_raw_chain(self):
        row = self._build_row()
        ec_str = json.dumps(row["details"]["earnings_calendar"])
        assert "base_calendar_candidate" not in ec_str
        assert "short_front_leg" not in ec_str
        assert "long_back_leg" not in ec_str

    def test_details_earnings_calendar_size_is_bounded(self):
        row = self._build_row()
        ec_str = json.dumps(row["details"]["earnings_calendar"])
        # Should be well under 10KB
        assert len(ec_str) < 10_000, f"details.earnings_calendar too large: {len(ec_str)} bytes"

    def test_gate_groups_are_serializable(self):
        row = self._build_row()
        serialized = json.dumps(row["gate_groups"])
        assert len(serialized) > 0

    def test_gate_groups_custom_fields_compact(self):
        row = self._build_row()
        gate_str = json.dumps(row["gate_groups"])
        # Custom fields should be small primitives, not embedded raw chains
        assert "base_calendar_candidate" not in gate_str
        assert len(gate_str) < 20_000, f"gate_groups too large: {len(gate_str)} bytes"

    def test_display_is_compact(self):
        row = self._build_row()
        display_str = json.dumps(row["display"])
        assert len(display_str) < 2_000, f"display too large: {len(display_str)} bytes"

    def test_daily_opportunity_is_compact(self):
        row = self._build_row()
        do_str = json.dumps(row["daily_opportunity"])
        assert len(do_str) < 500, f"daily_opportunity too large: {len(do_str)} bytes"


# ─── Lifecycle row compactness ─────────────────────────────────────────────────

class TestLifecycleRowCompactness:
    def _build_check(self) -> dict:
        from app.strategies.earnings_calendar_universal import build_earnings_lifecycle_universal_row
        check = {
            "ticker": "SBUX",
            "action": "HOLD / MONITOR",
            "strike": 80.0,
            "front_expiration": "2026-07-18",
            "underlying_price": 79.50,
            "current_mid_debit": 0.40,
            "assignment_risk_level": "Low",
            "lifecycle_priority_score": 30.0,
            "reasons": ["Current spread value is available."],
            # Simulate raw broker payload that should NOT bloat details
            "short_leg_quote": {"bid": 0.22, "ask": 0.28, "raw_payload": "x" * 1000},
            "long_leg_quote": {"bid": 0.60, "ask": 0.70, "raw_payload": "y" * 1000},
        }
        build_earnings_lifecycle_universal_row(check)
        return check

    def test_lifecycle_details_is_serializable(self):
        check = self._build_check()
        serialized = json.dumps(check["details"])
        assert len(serialized) > 0

    def test_lifecycle_details_excludes_raw_broker_payload(self):
        check = self._build_check()
        ec_str = json.dumps(check["details"]["earnings_calendar"])
        assert "raw_payload" not in ec_str

    def test_lifecycle_details_compact(self):
        check = self._build_check()
        ec_str = json.dumps(check["details"]["earnings_calendar"])
        assert len(ec_str) < 5_000, f"lifecycle details too large: {len(ec_str)} bytes"


# ─── Normal summary excludes raw provider responses ────────────────────────────

class TestSummaryPayloadDiscipline:
    def test_production_service_items_have_compact_details(self):
        from app.services.earnings_calendar_strategy_service import evaluate_earnings_calendar_candidates
        candidates = [_compact_candidate()]
        earnings = {"BAC": _bac_earnings()}
        result = evaluate_earnings_calendar_candidates(candidates, earnings)
        for item in result.get("items") or []:
            ec = (item.get("details") or {}).get("earnings_calendar") or {}
            ec_str = json.dumps(ec)
            assert len(ec_str) < 10_000, f"details too large for {item.get('ticker')}: {len(ec_str)} bytes"

    def test_production_service_does_not_add_raw_chains_to_details(self):
        from app.services.earnings_calendar_strategy_service import evaluate_earnings_calendar_candidates
        candidates = [_compact_candidate()]
        earnings = {"BAC": _bac_earnings()}
        result = evaluate_earnings_calendar_candidates(candidates, earnings)
        for item in result.get("items") or []:
            ec = (item.get("details") or {}).get("earnings_calendar") or {}
            for field in ("base_calendar_candidate", "short_front_leg", "long_back_leg", "_raw_chain"):
                assert field not in ec, f"Raw field {field!r} found in details.earnings_calendar"


# ─── CAVEMAN MODE safety tests ─────────────────────────────────────────────────

class TestCavemanModeSafety:
    def test_ff_dry_run_unchanged(self):
        assert cfg_forward_factor_dry_run() is True

    def test_no_trade_execution_in_config(self):
        from app import config as cfg
        assert not getattr(cfg, "TRADE_EXECUTION_ENABLED", False)

    def test_universal_row_builder_never_executes_trades(self):
        from app.strategies.earnings_calendar_universal import build_earnings_calendar_universal_row
        row = {
            "ticker": "BAC",
            "action": "EARNINGS CALENDAR CANDIDATE",
            "score": 72.0,
            "calendar_entry_allowed": True,
            "daily_opportunity_eligible": True,
            "reasons": [],
        }
        result = build_earnings_calendar_universal_row(row)
        # Should return the enriched row, not raise or execute anything
        assert isinstance(result, dict)
        assert result is row

    def test_lifecycle_builder_never_executes_trades(self):
        from app.strategies.earnings_calendar_universal import build_earnings_lifecycle_universal_row
        check = {
            "ticker": "SBUX",
            "action": "HOLD / MONITOR",
            "lifecycle_priority_score": 30.0,
        }
        result = build_earnings_lifecycle_universal_row(check)
        assert isinstance(result, dict)

    def test_earnings_calendar_lifecycle_rows_never_do_eligible(self):
        from app.strategies.earnings_calendar_universal import build_earnings_lifecycle_universal_row
        check = {
            "ticker": "SBUX",
            "action": "TAKE PROFIT / REVIEW EXIT",
            "lifecycle_priority_score": 50.0,
        }
        build_earnings_lifecycle_universal_row(check)
        assert check["daily_opportunity"]["eligible"] is False

    def test_ff_spec_dry_run_true(self):
        from app.services.strategy_spec_registry import get_spec
        assert get_spec("forward_factor_calendar")["dry_run"] is True

    def test_ff_spec_daily_opportunity_not_allowed(self):
        # 32C: FF promoted; daily_opportunity_allowed=True (research signals; dry_run enforces no execution).
        from app.services.strategy_spec_registry import get_spec
        assert get_spec("forward_factor_calendar")["daily_opportunity_allowed"] is True


def cfg_forward_factor_dry_run() -> bool:
    from app import config as cfg
    return bool(cfg.FORWARD_FACTOR_DRY_RUN)


# ─── Fixtures ─────────────────────────────────────────────────────────────────

def _compact_candidate() -> dict:
    return {
        "ticker": "BAC",
        "score": 72.0,
        "front_expiration": "2026-07-18",
        "back_expiration": "2026-07-25",
        "front_dte": 11,
        "back_dte": 18,
        "strike": 45.0,
        "option_type": "call",
        "underlying_price": 44.50,
        "front_iv": 0.42,
        "back_iv": 0.58,
        "iv_edge": 0.16,
        "conservative_debit": 0.35,
        "debit_pct_underlying": 0.79,
        "max_leg_spread_pct": 3.2,
        "min_leg_open_interest": 120,
        "min_leg_volume": 35,
    }


def _bac_earnings() -> dict:
    return {
        "ticker": "BAC",
        "has_data": True,
        "earnings_date": "2026-07-22",
        "date": "2026-07-22",
        "time_of_day": "before_open",
        "session_label": "Before Open",
        "is_timestamp_confirmed": True,
        "earnings_date_confidence": "multi_source",
        "date_confidence": "high",
        "date_conflict": False,
        "date_sources": ["finnhub", "tradier"],
        "sources_seen": ["finnhub", "tradier"],
        "earnings_source_count": 2,
        "earnings_source_conflict": False,
    }
