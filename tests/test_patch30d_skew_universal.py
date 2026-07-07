"""
ASA Patch 30D — Skew Momentum Vertical Universal Row Tests

Covers:
  - Compile check
  - Universal row builder (PASS / WATCH / FAIL verdicts)
  - details.skew_momentum_vertical fields
  - Gate groups structure
  - Row type inference
  - Idempotency
  - Payload discipline (raw legs excluded from details)
  - CAVEMAN MODE safety
"""
from __future__ import annotations

import json
import py_compile


class TestCompile:
    def test_universal_compiles(self):
        py_compile.compile("app/strategies/skew_momentum_vertical_universal.py", doraise=True)

    def test_verdict_service_compiles(self):
        py_compile.compile("app/services/skew_momentum_vertical_verdict_service.py", doraise=True)


# ─── Builder: PASS row ────────────────────────────────────────────────────────

class TestBuildPassRow:
    def _build_pass(self, run_id: str | None = None) -> dict:
        from app.strategies.skew_momentum_vertical_universal import build_skew_momentum_vertical_universal_row
        row = _pass_row()
        build_skew_momentum_vertical_universal_row(row, run_id=run_id)
        return row

    def test_schema_version_set(self):
        from app.strategies.schema import SCHEMA_VERSION
        row = self._build_pass()
        assert row["schema_version"] == SCHEMA_VERSION

    def test_row_type_is_new_candidate(self):
        row = self._build_pass()
        assert row["row_type"] == "new_candidate"

    def test_row_id_present(self):
        row = self._build_pass()
        assert isinstance(row.get("row_id"), str) and row["row_id"].startswith("smv:")

    def test_details_namespace_present(self):
        row = self._build_pass()
        assert "skew_momentum_vertical" in row["details"]

    def test_gate_groups_present(self):
        row = self._build_pass()
        assert isinstance(row.get("gate_groups"), dict)

    def test_display_present(self):
        row = self._build_pass()
        assert "display" in row
        assert row["display"]["title"] == "AAPL"

    def test_daily_opportunity_eligible(self):
        row = self._build_pass()
        assert row["daily_opportunity"]["eligible"] is True

    def test_daily_opportunity_bucket(self):
        row = self._build_pass()
        assert row["daily_opportunity"]["bucket"] == "skew_momentum_vertical"

    def test_daily_opportunity_priority_set(self):
        row = self._build_pass()
        assert row["daily_opportunity"]["priority"] is not None

    def test_run_id_embedded(self):
        row = self._build_pass(run_id="run-smv-001")
        assert row.get("row_id") is not None

    def test_strategy_id_preserved(self):
        row = self._build_pass()
        assert row.get("strategy_id") == "skew_momentum_vertical"


# ─── Builder: WATCH row ───────────────────────────────────────────────────────

class TestBuildWatchRow:
    def _build_watch(self) -> dict:
        from app.strategies.skew_momentum_vertical_universal import build_skew_momentum_vertical_universal_row
        row = _watch_row()
        build_skew_momentum_vertical_universal_row(row)
        return row

    def test_row_type_is_observation(self):
        row = self._build_watch()
        assert row["row_type"] == "observation"

    def test_daily_opportunity_not_eligible(self):
        row = self._build_watch()
        assert row["daily_opportunity"]["eligible"] is False

    def test_daily_opportunity_priority_none(self):
        row = self._build_watch()
        assert row["daily_opportunity"]["priority"] is None

    def test_schema_version_set(self):
        from app.strategies.schema import SCHEMA_VERSION
        row = self._build_watch()
        assert row["schema_version"] == SCHEMA_VERSION


# ─── Builder: FAIL row ────────────────────────────────────────────────────────

class TestBuildFailRow:
    def _build_fail(self) -> dict:
        from app.strategies.skew_momentum_vertical_universal import build_skew_momentum_vertical_universal_row
        row = _fail_row()
        build_skew_momentum_vertical_universal_row(row)
        return row

    def test_row_type_is_rejected_candidate(self):
        row = self._build_fail()
        assert row["row_type"] == "rejected_candidate"

    def test_daily_opportunity_not_eligible(self):
        row = self._build_fail()
        assert row["daily_opportunity"]["eligible"] is False

    def test_gate_groups_have_blocking_gate(self):
        row = self._build_fail()
        # At least one gate group should have a blocking gate
        found_blocking = False
        for grp_name, grp in row["gate_groups"].items():
            for gate_name, gate in grp.items():
                if isinstance(gate, dict) and gate.get("blocking"):
                    found_blocking = True
                    break
        assert found_blocking


# ─── Details block ────────────────────────────────────────────────────────────

class TestDetailsBlock:
    def _details(self) -> dict:
        from app.strategies.skew_momentum_vertical_universal import build_skew_momentum_vertical_universal_row
        row = _pass_row()
        build_skew_momentum_vertical_universal_row(row)
        return row["details"]["skew_momentum_vertical"]

    def test_direction_present(self):
        assert self._details()["direction"] == "bullish"

    def test_momentum_confirmed_present(self):
        assert self._details()["momentum_confirmed"] is True

    def test_option_type_present(self):
        assert self._details()["option_type"] == "call"

    def test_expiration_present(self):
        assert self._details()["expiration"] == "2026-08-15"

    def test_long_strike_present(self):
        assert self._details()["long_strike"] == 185.0

    def test_short_strike_present(self):
        assert self._details()["short_strike"] == 190.0

    def test_conservative_debit_present(self):
        assert self._details()["conservative_debit"] is not None

    def test_max_risk_present(self):
        assert self._details()["max_risk"] is not None

    def test_reward_risk_present(self):
        assert self._details()["reward_risk"] is not None

    def test_liquidity_pass_present(self):
        assert isinstance(self._details()["liquidity_pass"], bool)

    def test_event_risk_present(self):
        assert isinstance(self._details()["event_risk"], bool)

    def test_raw_legs_not_in_details(self):
        d = self._details()
        assert "long_leg" not in d
        assert "short_leg" not in d
        assert "payload" not in d
        assert "requirements" not in d

    def test_details_is_serializable(self):
        d = self._details()
        serialized = json.dumps(d)
        assert len(serialized) > 0

    def test_details_size_bounded(self):
        from app.strategies.skew_momentum_vertical_universal import build_skew_momentum_vertical_universal_row
        row = _pass_row_with_raw_chain()
        build_skew_momentum_vertical_universal_row(row)
        d = row["details"]["skew_momentum_vertical"]
        size = len(json.dumps(d))
        assert size < 10_000, f"details.skew_momentum_vertical too large: {size} bytes"


# ─── Gate groups ─────────────────────────────────────────────────────────────

class TestGateGroups:
    def _gate_groups(self, row_builder=None) -> dict:
        from app.strategies.skew_momentum_vertical_universal import build_skew_momentum_vertical_universal_row
        row = row_builder() if row_builder else _pass_row()
        build_skew_momentum_vertical_universal_row(row)
        return row["gate_groups"]

    def test_has_data_group(self):
        assert "data" in self._gate_groups()

    def test_has_setup_group(self):
        assert "setup" in self._gate_groups()

    def test_has_volatility_group(self):
        assert "volatility" in self._gate_groups()

    def test_has_structure_group(self):
        assert "structure" in self._gate_groups()

    def test_has_risk_group(self):
        assert "risk" in self._gate_groups()

    def test_has_liquidity_group(self):
        assert "liquidity" in self._gate_groups()

    def test_has_event_group(self):
        assert "event" in self._gate_groups()

    def test_has_daily_opportunity_group(self):
        assert "daily_opportunity" in self._gate_groups()

    def test_pass_row_momentum_gate_pass(self):
        gg = self._gate_groups()
        assert gg["setup"]["momentum"]["status"] == "pass"

    def test_pass_row_skew_gate_pass(self):
        gg = self._gate_groups()
        assert gg["volatility"]["skew_richness"]["status"] == "pass"

    def test_pass_row_liquidity_gate_pass(self):
        gg = self._gate_groups()
        assert gg["liquidity"]["bid_ask_spread"]["status"] == "pass"

    def test_fail_row_momentum_gate_fail(self):
        gg = self._gate_groups(row_builder=_fail_row)
        assert gg["setup"]["momentum"]["status"] == "fail"

    def test_gate_groups_serializable(self):
        gg = self._gate_groups()
        serialized = json.dumps(gg)
        assert len(serialized) > 0

    def test_gate_groups_no_raw_chains(self):
        from app.strategies.skew_momentum_vertical_universal import build_skew_momentum_vertical_universal_row
        row = _pass_row_with_raw_chain()
        build_skew_momentum_vertical_universal_row(row)
        gg_str = json.dumps(row["gate_groups"])
        assert "long_leg_raw" not in gg_str
        assert "short_leg_raw" not in gg_str


# ─── Idempotency ──────────────────────────────────────────────────────────────

class TestIdempotency:
    def test_calling_twice_produces_same_schema_version(self):
        from app.strategies.skew_momentum_vertical_universal import build_skew_momentum_vertical_universal_row
        from app.strategies.schema import SCHEMA_VERSION
        row = _pass_row()
        build_skew_momentum_vertical_universal_row(row)
        build_skew_momentum_vertical_universal_row(row)
        assert row["schema_version"] == SCHEMA_VERSION

    def test_calling_twice_does_not_duplicate_details(self):
        from app.strategies.skew_momentum_vertical_universal import build_skew_momentum_vertical_universal_row
        row = _pass_row()
        build_skew_momentum_vertical_universal_row(row)
        first_row_id = row["row_id"]
        build_skew_momentum_vertical_universal_row(row)
        assert row["row_id"] == first_row_id

    def test_returns_same_row_object(self):
        from app.strategies.skew_momentum_vertical_universal import build_skew_momentum_vertical_universal_row
        row = _pass_row()
        result = build_skew_momentum_vertical_universal_row(row)
        assert result is row


# ─── CAVEMAN MODE safety ──────────────────────────────────────────────────────

class TestCavemanModeSafety:
    def test_builder_does_not_execute_trades(self):
        from app.strategies.skew_momentum_vertical_universal import build_skew_momentum_vertical_universal_row
        row = _pass_row()
        result = build_skew_momentum_vertical_universal_row(row)
        assert isinstance(result, dict)

    def test_builder_returns_row_not_none(self):
        from app.strategies.skew_momentum_vertical_universal import build_skew_momentum_vertical_universal_row
        row = _minimal_row()
        result = build_skew_momentum_vertical_universal_row(row)
        assert result is not None
        assert result is row

    def test_minimal_row_does_not_raise(self):
        from app.strategies.skew_momentum_vertical_universal import build_skew_momentum_vertical_universal_row
        # Must not raise even with bare minimum fields
        row = {"ticker": "AAPL", "verdict": "PASS / POSSIBLE ENTRY SETUP", "score": 55.0}
        build_skew_momentum_vertical_universal_row(row)


# ─── Row type inference ───────────────────────────────────────────────────────

class TestRowTypeInference:
    def _infer(self, verdict: str) -> str:
        from app.strategies.skew_momentum_vertical_universal import _infer_row_type
        return _infer_row_type(verdict.upper())

    def test_pass_verdict_is_new_candidate(self):
        assert self._infer("PASS / POSSIBLE ENTRY SETUP") == "new_candidate"

    def test_fail_verdict_is_rejected_candidate(self):
        assert self._infer("FAIL / DTE TOO SHORT") == "rejected_candidate"

    def test_fail_data_quality_is_rejected_candidate(self):
        assert self._infer("FAIL / DATA QUALITY") == "rejected_candidate"

    def test_watch_verdict_is_observation(self):
        assert self._infer("WATCH / MOMENTUM NOT CONFIRMED") == "observation"

    def test_watch_skew_is_observation(self):
        assert self._infer("WATCH / SKEW NOT RICH ENOUGH") == "observation"

    def test_watch_event_risk_is_observation(self):
        assert self._infer("WATCH / EVENT RISK") == "observation"


# ─── Verdict service integration ─────────────────────────────────────────────

class TestVerdictServiceIntegration:
    def test_verdict_service_row_has_schema_version(self):
        from app.strategies.schema import SCHEMA_VERSION
        from app.services.skew_momentum_vertical_verdict_service import apply_skew_momentum_vertical_verdict
        candidate = _verdict_candidate(pass_all=True)
        row = apply_skew_momentum_vertical_verdict(candidate)
        assert row.get("schema_version") == SCHEMA_VERSION

    def test_verdict_service_row_has_details(self):
        from app.services.skew_momentum_vertical_verdict_service import apply_skew_momentum_vertical_verdict
        candidate = _verdict_candidate(pass_all=True)
        row = apply_skew_momentum_vertical_verdict(candidate)
        assert "details" in row
        assert "skew_momentum_vertical" in row["details"]

    def test_verdict_service_row_has_gate_groups(self):
        from app.services.skew_momentum_vertical_verdict_service import apply_skew_momentum_vertical_verdict
        candidate = _verdict_candidate(pass_all=True)
        row = apply_skew_momentum_vertical_verdict(candidate)
        assert "gate_groups" in row

    def test_verdict_service_row_has_daily_opportunity(self):
        from app.services.skew_momentum_vertical_verdict_service import apply_skew_momentum_vertical_verdict
        candidate = _verdict_candidate(pass_all=True)
        row = apply_skew_momentum_vertical_verdict(candidate)
        assert "daily_opportunity" in row


# ─── Fixtures ─────────────────────────────────────────────────────────────────

def _pass_row() -> dict:
    return {
        "ticker": "AAPL",
        "strategy_id": "skew_momentum_vertical",
        "verdict": "PASS / POSSIBLE ENTRY SETUP",
        "score": 72.0,
        "direction": "bullish",
        "momentum_confirmed": True,
        "momentum_score": 75.0,
        "momentum_reason": "Bullish momentum confirmed.",
        "skew_pass": True,
        "short_iv_edge": 0.045,
        "short_premium_financing_pct": 22.5,
        "adjusted_skew_score": 14.2,
        "skew_gap_to_pass": 0.0,
        "possible_spread": {
            "expiration": "2026-08-15",
            "option_type": "call",
            "long_strike": 185.0,
            "short_strike": 190.0,
            "width": 5.0,
            "conservative_debit": 1.85,
            "mid_debit": 1.70,
        },
        "dte": 39,
        "underlying_price": 186.50,
        "conservative_debit": 1.85,
        "mid_debit": 1.70,
        "max_risk": 185.0,
        "max_profit": 315.0,
        "reward_risk": 1.70,
        "breakeven": 186.85,
        "debit_pct_of_width": 37.0,
        "long_leg_spread_pct": 1.2,
        "short_leg_spread_pct": 1.8,
        "spread_market_width_pct": 8.5,
        "liquidity_pass": True,
        "data_quality_pass": True,
        "event_risk": False,
        "event_risk_allowed": True,
        "earnings_trust_label": "multi_source_confirmed",
        "stale_structure": False,
        "daily_opportunity_eligible": True,
        "daily_opportunity_reason": "Eligible for Daily Opportunity based on strategy result.",
        "long_leg": {"bid": 1.80, "ask": 1.90, "strike": 185.0, "iv": 0.28},
        "short_leg": {"bid": 0.05, "ask": 0.10, "strike": 190.0, "iv": 0.32},
    }


def _watch_row() -> dict:
    return {
        "ticker": "NVDA",
        "strategy_id": "skew_momentum_vertical",
        "verdict": "WATCH / SKEW NOT RICH ENOUGH",
        "score": 40.0,
        "direction": "bullish",
        "momentum_confirmed": True,
        "momentum_score": 70.0,
        "skew_pass": False,
        "adjusted_skew_score": 8.5,
        "skew_gap_to_pass": 4.0,
        "possible_spread": {
            "expiration": "2026-08-15",
            "option_type": "call",
            "long_strike": 120.0,
            "short_strike": 125.0,
            "width": 5.0,
            "conservative_debit": 1.50,
        },
        "dte": 39,
        "underlying_price": 121.0,
        "conservative_debit": 1.50,
        "max_risk": 150.0,
        "reward_risk": 2.33,
        "liquidity_pass": True,
        "data_quality_pass": True,
        "event_risk": False,
        "earnings_trust_label": "",
        "stale_structure": False,
        "daily_opportunity_eligible": False,
        "daily_opportunity_reason": "Skew not rich enough.",
    }


def _fail_row() -> dict:
    return {
        "ticker": "TSLA",
        "strategy_id": "skew_momentum_vertical",
        "verdict": "FAIL / DATA QUALITY",
        "score": 0.0,
        "direction": None,
        "momentum_confirmed": False,
        "momentum_reason": "Momentum data unavailable.",
        "skew_pass": False,
        "liquidity_pass": False,
        "data_quality_pass": False,
        "event_risk": False,
        "earnings_trust_label": "",
        "stale_structure": False,
        "daily_opportunity_eligible": False,
        "daily_opportunity_reason": "Did not qualify.",
    }


def _minimal_row() -> dict:
    return {"ticker": "MSFT", "verdict": "PASS / POSSIBLE ENTRY SETUP", "score": 55.0}


def _pass_row_with_raw_chain() -> dict:
    row = _pass_row()
    row["long_leg_raw"] = [{"strike": 185.0, "bid": 1.80, "ask": 1.90, "iv": 0.28}] * 50
    row["short_leg_raw"] = [{"strike": 190.0, "bid": 0.05, "ask": 0.10, "iv": 0.32}] * 50
    return row


def _verdict_candidate(pass_all: bool = True) -> dict:
    return {
        "strategy_id": "skew_momentum_vertical",
        "ticker": "AAPL",
        "direction": "bullish",
        "momentum_confirmed": pass_all,
        "momentum_score": 75.0,
        "momentum_reason": "Bullish momentum confirmed.",
        "skew_pass": pass_all,
        "short_iv_edge": 0.045,
        "short_premium_financing_pct": 22.5,
        "adjusted_skew_score": 14.2,
        "skew_gap_to_pass": 0.0,
        "possible_spread": {
            "expiration": "2026-08-15",
            "option_type": "call",
            "long_strike": 185.0,
            "short_strike": 190.0,
            "width": 5.0,
            "conservative_debit": 1.85,
            "mid_debit": 1.70,
        },
        "dte": 39,
        "underlying_price": 186.50,
        "conservative_debit": 1.85,
        "mid_debit": 1.70,
        "max_risk": 185.0,
        "max_profit": 315.0,
        "reward_risk": 1.70,
        "breakeven": 186.85,
        "debit_pct_of_width": 37.0,
        "long_leg_spread_pct": 1.2,
        "short_leg_spread_pct": 1.8,
        "spread_market_width_pct": 8.5,
        "liquidity_pass": pass_all,
        "data_quality_pass": pass_all,
        "event_risk": False,
        "event_risk_allowed": True,
        "earnings_trust_label": "multi_source_confirmed" if pass_all else "",
        "stale_structure": False,
        "requirements": [],
        "risk_notes": [],
        "provider_notes": [],
    }
