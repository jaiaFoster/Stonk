"""
ASA Patch 30E — Forward Factor Universal Row Tests

Covers:
  - Compile check
  - Universal row builder (PASS / WATCH / FAIL / dry-run)
  - details.forward_factor fields
  - Gate groups structure
  - Row type inference
  - Idempotency
  - Daily Opportunity always excluded
  - CAVEMAN MODE safety
"""
from __future__ import annotations

import json
import py_compile


class TestCompile:
    def test_universal_compiles(self):
        py_compile.compile("app/strategies/forward_factor_universal.py", doraise=True)

    def test_schema_compiles(self):
        py_compile.compile("app/strategies/schema.py", doraise=True)


# ─── Builder: PASS row ────────────────────────────────────────────────────────

class TestBuildPassRow:
    def _build_pass(self, run_id: str | None = None) -> dict:
        from app.strategies.forward_factor_universal import build_forward_factor_universal_row
        row = _pass_row()
        build_forward_factor_universal_row(row, run_id=run_id)
        return row

    def test_schema_version_set(self):
        from app.strategies.schema import SCHEMA_VERSION
        row = self._build_pass()
        assert row["schema_version"] == SCHEMA_VERSION

    def test_strategy_id_set(self):
        row = self._build_pass()
        assert row.get("strategy_id") == "forward_factor_calendar"

    def test_row_id_present_with_ffc_prefix(self):
        row = self._build_pass(run_id="run-001")
        assert isinstance(row.get("row_id"), str)
        assert row["row_id"].startswith("ffc:")

    def test_details_namespace_present(self):
        row = self._build_pass()
        assert "forward_factor" in row["details"]

    def test_gate_groups_present(self):
        row = self._build_pass()
        assert isinstance(row.get("gate_groups"), dict)

    def test_display_present(self):
        row = self._build_pass()
        assert "display" in row
        assert row["display"]["title"] == "AAPL"

    def test_daily_opportunity_always_false(self):
        row = self._build_pass()
        assert row["daily_opportunity"]["eligible"] is False

    def test_daily_opportunity_bucket_set(self):
        row = self._build_pass()
        assert row["daily_opportunity"]["bucket"] == "forward_factor_calendar"

    def test_daily_opportunity_priority_none(self):
        row = self._build_pass()
        assert row["daily_opportunity"]["priority"] is None

    def test_run_id_embedded_in_row_id(self):
        row_with = self._build_pass(run_id="run-ffc-001")
        row_without = _pass_row()
        from app.strategies.forward_factor_universal import build_forward_factor_universal_row
        build_forward_factor_universal_row(row_without, run_id="run-ffc-999")
        assert row_with["row_id"] != row_without["row_id"]

    def test_returns_row_object(self):
        from app.strategies.forward_factor_universal import build_forward_factor_universal_row
        row = _pass_row()
        result = build_forward_factor_universal_row(row)
        assert result is row


# ─── Builder: dry-run row ─────────────────────────────────────────────────────

class TestDryRunRow:
    def _build_dry_run(self) -> dict:
        from app.strategies.forward_factor_universal import build_forward_factor_universal_row
        import app.config as config
        original = getattr(config, "FORWARD_FACTOR_DRY_RUN", True)
        config.FORWARD_FACTOR_DRY_RUN = True
        try:
            row = _pass_row()
            build_forward_factor_universal_row(row)
            return row
        finally:
            config.FORWARD_FACTOR_DRY_RUN = original

    def test_dry_run_row_type_is_observation(self):
        row = self._build_dry_run()
        assert row["row_type"] == "observation"

    def test_dry_run_flag_in_details(self):
        row = self._build_dry_run()
        assert row["details"]["forward_factor"]["is_dry_run"] is True

    def test_dry_run_daily_opportunity_still_excluded(self):
        row = self._build_dry_run()
        assert row["daily_opportunity"]["eligible"] is False


# ─── Builder: FAIL row ────────────────────────────────────────────────────────

class TestBuildFailRow:
    def _build_fail(self) -> dict:
        from app.strategies.forward_factor_universal import build_forward_factor_universal_row
        import app.config as config
        config.FORWARD_FACTOR_DRY_RUN = False
        try:
            row = _fail_row()
            build_forward_factor_universal_row(row)
            return row
        finally:
            config.FORWARD_FACTOR_DRY_RUN = True

    def test_row_type_is_rejected_candidate(self):
        row = self._build_fail()
        assert row["row_type"] == "rejected_candidate"

    def test_daily_opportunity_not_eligible(self):
        row = self._build_fail()
        assert row["daily_opportunity"]["eligible"] is False

    def test_schema_version_set(self):
        from app.strategies.schema import SCHEMA_VERSION
        row = self._build_fail()
        assert row["schema_version"] == SCHEMA_VERSION


# ─── Details block ────────────────────────────────────────────────────────────

class TestDetailsBlock:
    def _details(self) -> dict:
        from app.strategies.forward_factor_universal import build_forward_factor_universal_row
        row = _pass_row()
        build_forward_factor_universal_row(row)
        return row["details"]["forward_factor"]

    def test_forward_factor_field_present(self):
        assert self._details()["forward_factor"] == 0.32

    def test_front_dte_present(self):
        assert self._details()["front_dte"] == 14

    def test_back_dte_present(self):
        assert self._details()["back_dte"] == 42

    def test_front_expiration_present(self):
        assert self._details()["front_expiration"] == "2026-07-18"

    def test_back_expiration_present(self):
        assert self._details()["back_expiration"] == "2026-08-15"

    def test_front_iv_present(self):
        assert self._details()["front_iv"] is not None

    def test_back_iv_present(self):
        assert self._details()["back_iv"] is not None

    def test_earnings_contaminated_is_bool(self):
        assert isinstance(self._details()["earnings_contaminated"], bool)

    def test_is_dry_run_is_bool(self):
        assert isinstance(self._details()["is_dry_run"], bool)

    def test_liquidity_pass_is_bool(self):
        assert isinstance(self._details()["liquidity_pass"], bool)

    def test_near_miss_ff_is_bool(self):
        assert isinstance(self._details()["near_miss_ff"], bool)

    def test_conservative_debit_present(self):
        assert self._details()["conservative_debit"] is not None

    def test_details_is_serializable(self):
        d = self._details()
        serialized = json.dumps(d)
        assert len(serialized) > 0

    def test_details_size_bounded(self):
        from app.strategies.forward_factor_universal import build_forward_factor_universal_row
        row = _pass_row()
        build_forward_factor_universal_row(row)
        d = row["details"]["forward_factor"]
        size = len(json.dumps(d))
        assert size < 10_000, f"details.forward_factor too large: {size} bytes"


# ─── Gate groups ─────────────────────────────────────────────────────────────

class TestGateGroups:
    def _gate_groups(self) -> dict:
        from app.strategies.forward_factor_universal import build_forward_factor_universal_row
        row = _pass_row()
        build_forward_factor_universal_row(row)
        return row["gate_groups"]

    def test_has_data_group(self):
        assert "data" in self._gate_groups()

    def test_has_candidate_group(self):
        assert "candidate" in self._gate_groups()

    def test_has_forward_vol_group(self):
        assert "forward_vol" in self._gate_groups()

    def test_has_earnings_group(self):
        assert "earnings" in self._gate_groups()

    def test_has_liquidity_group(self):
        assert "liquidity" in self._gate_groups()

    def test_has_setup_group(self):
        assert "setup" in self._gate_groups()

    def test_has_budget_group(self):
        assert "budget" in self._gate_groups()

    def test_has_risk_group(self):
        assert "risk" in self._gate_groups()

    def test_has_daily_opportunity_group(self):
        assert "daily_opportunity" in self._gate_groups()

    def test_daily_opportunity_gate_status_fail(self):
        gg = self._gate_groups()
        assert gg["daily_opportunity"]["eligible"]["status"] == "fail"

    def test_gate_groups_serializable(self):
        gg = self._gate_groups()
        serialized = json.dumps(gg)
        assert len(serialized) > 0


# ─── Idempotency ──────────────────────────────────────────────────────────────

class TestIdempotency:
    def test_calling_twice_produces_same_schema_version(self):
        from app.strategies.forward_factor_universal import build_forward_factor_universal_row
        from app.strategies.schema import SCHEMA_VERSION
        row = _pass_row()
        build_forward_factor_universal_row(row)
        build_forward_factor_universal_row(row)
        assert row["schema_version"] == SCHEMA_VERSION

    def test_calling_twice_preserves_row_id(self):
        from app.strategies.forward_factor_universal import build_forward_factor_universal_row
        row = _pass_row()
        build_forward_factor_universal_row(row)
        first_row_id = row["row_id"]
        build_forward_factor_universal_row(row)
        assert row["row_id"] == first_row_id

    def test_returns_same_row_object(self):
        from app.strategies.forward_factor_universal import build_forward_factor_universal_row
        row = _pass_row()
        result = build_forward_factor_universal_row(row)
        assert result is row


# ─── Row type inference ───────────────────────────────────────────────────────

class TestRowTypeInference:
    def _infer(self, verdict: str, *, is_dry_run: bool = False) -> str:
        from app.strategies.forward_factor_universal import _infer_row_type
        return _infer_row_type(verdict.upper(), is_dry_run=is_dry_run)

    def test_dry_run_always_observation(self):
        assert self._infer("PASS / FF=0.32", is_dry_run=True) == "observation"

    def test_pass_verdict_is_new_candidate(self):
        assert self._infer("PASS / FF=0.32", is_dry_run=False) == "new_candidate"

    def test_fail_verdict_is_rejected_candidate(self):
        assert self._infer("FAIL / LOW FF", is_dry_run=False) == "rejected_candidate"

    def test_skipped_verdict_is_observation(self):
        assert self._infer("SKIPPED", is_dry_run=False) == "observation"

    def test_watch_verdict_is_observation(self):
        assert self._infer("WATCH / NEAR MISS", is_dry_run=False) == "observation"

    def test_unknown_verdict_is_observation(self):
        assert self._infer("", is_dry_run=False) == "observation"


# ─── CAVEMAN MODE safety ──────────────────────────────────────────────────────

class TestCavemanModeSafety:
    def test_builder_returns_dict(self):
        from app.strategies.forward_factor_universal import build_forward_factor_universal_row
        row = _pass_row()
        result = build_forward_factor_universal_row(row)
        assert isinstance(result, dict)

    def test_minimal_row_does_not_raise(self):
        from app.strategies.forward_factor_universal import build_forward_factor_universal_row
        row = {"ticker": "AAPL", "verdict": "PASS", "score": 0.5}
        build_forward_factor_universal_row(row)

    def test_empty_row_does_not_raise(self):
        from app.strategies.forward_factor_universal import build_forward_factor_universal_row
        row: dict = {}
        build_forward_factor_universal_row(row)
        assert row.get("strategy_id") == "forward_factor_calendar"

    def test_daily_opportunity_never_eligible(self):
        from app.strategies.forward_factor_universal import build_forward_factor_universal_row
        for verdict in ("PASS", "FAIL", "WATCH", "SKIPPED", ""):
            row = {"ticker": "TEST", "verdict": verdict}
            build_forward_factor_universal_row(row)
            assert row["daily_opportunity"]["eligible"] is False, f"verdict={verdict!r} should never be DO eligible"


# ─── Fixtures ─────────────────────────────────────────────────────────────────

def _pass_row() -> dict:
    return {
        "ticker": "AAPL",
        "strategy_id": "forward_factor_calendar",
        "verdict": "PASS / FF_HIGH",
        "score": 0.85,
        "actionability_score": 0.85,
        "forward_factor": 0.32,
        "front_dte": 14,
        "back_dte": 42,
        "front_expiration": "2026-07-18",
        "back_expiration": "2026-08-15",
        "front_ex_earnings_iv": 0.28,
        "back_ex_earnings_iv": 0.22,
        "front_raw_iv": 0.30,
        "back_raw_iv": 0.24,
        "earnings_contaminated": False,
        "front_iv_derivation_method": "ex_earnings",
        "conservative_debit": 1.20,
        "mid_debit": 1.10,
        "debit_at_risk": 120.0,
        "liquidity_pass": True,
        "liquidity_status": "PASS",
        "package_slippage_pct": 2.5,
        "near_miss_ff": False,
        "source_qualification": "full_chain",
        "ff_candidate_stage": "evaluated",
        "structure_status": "COMPLETE",
        "data_eligibility": {"eligible": True},
        "friendly_verdict": "Strong FF",
        "primary_blocker": "",
    }


def _fail_row() -> dict:
    return {
        "ticker": "TSLA",
        "strategy_id": "forward_factor_calendar",
        "verdict": "FAIL / LOW_FF",
        "score": 0.10,
        "forward_factor": 0.08,
        "front_dte": 7,
        "back_dte": 35,
        "front_expiration": "2026-07-11",
        "back_expiration": "2026-08-08",
        "front_ex_earnings_iv": 0.45,
        "back_ex_earnings_iv": 0.40,
        "earnings_contaminated": False,
        "liquidity_pass": False,
        "liquidity_status": "FAIL",
        "structure_status": "COMPLETE",
        "data_eligibility": {"eligible": True},
        "primary_blocker": "Forward factor below threshold.",
    }
