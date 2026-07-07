"""
ASA Patch 30D — Skew Momentum Vertical Daily Opportunity Compatibility Tests

Verifies that universalization does NOT change existing Daily Opportunity behavior:
  - PASS verdict → eligible (skew_momentum_vertical daily_opportunity_allowed=True)
  - WATCH / FAIL → not eligible
  - Universal daily_opportunity dict agrees with existing daily_opportunity_eligible bool
  - Baseline: skew rows have 0 DO rows if all are WATCH/FAIL (existing behavior stable)
"""
from __future__ import annotations

import py_compile


class TestCompile:
    def test_universal_compiles(self):
        py_compile.compile("app/strategies/skew_momentum_vertical_universal.py", doraise=True)

    def test_normalization_compiles(self):
        py_compile.compile("app/services/strategy_row_normalization_service.py", doraise=True)


# ─── Legacy DO eligibility rules unchanged ────────────────────────────────────

class TestDailyOpportunityEligibilityLegacy:
    def _normalize(self, row: dict, strategy_id: str) -> dict:
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        normalize_strategy_row(row, strategy_id)
        return row

    def test_pass_verdict_is_eligible(self):
        row = {"ticker": "AAPL", "verdict": "PASS / POSSIBLE ENTRY SETUP", "score": 72.0}
        self._normalize(row, "skew_momentum_vertical")
        assert row.get("daily_opportunity_eligible") is True

    def test_watch_verdict_not_eligible(self):
        row = {"ticker": "NVDA", "verdict": "WATCH / SKEW NOT RICH ENOUGH", "score": 40.0}
        self._normalize(row, "skew_momentum_vertical")
        assert row.get("daily_opportunity_eligible") is False

    def test_fail_verdict_not_eligible(self):
        row = {"ticker": "TSLA", "verdict": "FAIL / DATA QUALITY", "score": 0.0}
        self._normalize(row, "skew_momentum_vertical")
        assert row.get("daily_opportunity_eligible") is False

    def test_fail_dte_not_eligible(self):
        row = {"ticker": "TSLA", "verdict": "FAIL / DTE TOO SHORT", "score": 0.0}
        self._normalize(row, "skew_momentum_vertical")
        assert row.get("daily_opportunity_eligible") is False

    def test_open_vertical_conflict_not_eligible(self):
        row = {"ticker": "AAPL", "verdict": "WATCH / OPEN VERTICAL CONFLICT", "score": 60.0}
        self._normalize(row, "skew_momentum_vertical")
        assert row.get("daily_opportunity_eligible") is False


# ─── Universal dict agrees with existing bool ──────────────────────────────────

class TestUniversalDODictAgreement:
    def _build_row(self, verdict: str, eligible: bool) -> dict:
        from app.strategies.skew_momentum_vertical_universal import build_skew_momentum_vertical_universal_row
        row = {
            "ticker": "AAPL",
            "verdict": verdict,
            "score": 72.0 if eligible else 20.0,
            "daily_opportunity_eligible": eligible,
            "daily_opportunity_reason": "Eligible for Daily Opportunity based on strategy result." if eligible else "Not eligible.",
        }
        build_skew_momentum_vertical_universal_row(row)
        return row

    def test_pass_eligible_true_dict_agrees(self):
        row = self._build_row("PASS / POSSIBLE ENTRY SETUP", True)
        assert row["daily_opportunity"]["eligible"] is True

    def test_watch_eligible_false_dict_agrees(self):
        row = self._build_row("WATCH / SKEW NOT RICH ENOUGH", False)
        assert row["daily_opportunity"]["eligible"] is False

    def test_fail_eligible_false_dict_agrees(self):
        row = self._build_row("FAIL / DATA QUALITY", False)
        assert row["daily_opportunity"]["eligible"] is False

    def test_exclusion_reason_empty_when_eligible(self):
        row = self._build_row("PASS / POSSIBLE ENTRY SETUP", True)
        assert row["daily_opportunity"]["exclusion_reason"] == ""

    def test_exclusion_reason_set_when_ineligible(self):
        row = self._build_row("WATCH / SKEW NOT RICH ENOUGH", False)
        assert row["daily_opportunity"]["exclusion_reason"]

    def test_bucket_is_skew_momentum_vertical(self):
        row = self._build_row("PASS / POSSIBLE ENTRY SETUP", True)
        assert row["daily_opportunity"]["bucket"] == "skew_momentum_vertical"

    def test_priority_set_when_eligible(self):
        row = self._build_row("PASS / POSSIBLE ENTRY SETUP", True)
        assert row["daily_opportunity"]["priority"] is not None

    def test_priority_none_when_ineligible(self):
        row = self._build_row("WATCH / SKEW NOT RICH ENOUGH", False)
        assert row["daily_opportunity"]["priority"] is None


# ─── Spec allows DO ───────────────────────────────────────────────────────────

class TestSkewSpecDOAllowed:
    def test_skew_spec_daily_opportunity_allowed(self):
        from app.services.strategy_spec_registry import get_spec
        spec = get_spec("skew_momentum_vertical")
        assert spec.get("daily_opportunity_allowed") is True

    def test_skew_spec_not_dry_run(self):
        from app.services.strategy_spec_registry import get_spec
        spec = get_spec("skew_momentum_vertical")
        assert spec.get("dry_run") is not True


# ─── End-to-end: DO count consistency ────────────────────────────────────────

class TestDOCountConsistency:
    def test_legacy_and_universal_do_counts_match_all_pass(self):
        from app.strategies.skew_momentum_vertical_universal import build_skew_momentum_vertical_universal_row
        rows = [_make_row("AAPL", "PASS / POSSIBLE ENTRY SETUP", True),
                _make_row("NVDA", "PASS / POSSIBLE ENTRY SETUP", True)]
        for row in rows:
            build_skew_momentum_vertical_universal_row(row)
        legacy = sum(1 for r in rows if r.get("daily_opportunity_eligible") is True)
        universal = sum(1 for r in rows if (r.get("daily_opportunity") or {}).get("eligible") is True)
        assert legacy == universal == 2

    def test_legacy_and_universal_do_counts_match_none_pass(self):
        from app.strategies.skew_momentum_vertical_universal import build_skew_momentum_vertical_universal_row
        rows = [_make_row("TSLA", "FAIL / DATA QUALITY", False),
                _make_row("NVDA", "WATCH / SKEW NOT RICH ENOUGH", False)]
        for row in rows:
            build_skew_momentum_vertical_universal_row(row)
        legacy = sum(1 for r in rows if r.get("daily_opportunity_eligible") is True)
        universal = sum(1 for r in rows if (r.get("daily_opportunity") or {}).get("eligible") is True)
        assert legacy == universal == 0

    def test_mixed_do_count_agrees(self):
        from app.strategies.skew_momentum_vertical_universal import build_skew_momentum_vertical_universal_row
        rows = [_make_row("AAPL", "PASS / POSSIBLE ENTRY SETUP", True),
                _make_row("TSLA", "FAIL / DATA QUALITY", False),
                _make_row("NVDA", "WATCH / SKEW NOT RICH ENOUGH", False)]
        for row in rows:
            build_skew_momentum_vertical_universal_row(row)
        legacy = sum(1 for r in rows if r.get("daily_opportunity_eligible") is True)
        universal = sum(1 for r in rows if (r.get("daily_opportunity") or {}).get("eligible") is True)
        assert legacy == universal


# ─── Fixtures ─────────────────────────────────────────────────────────────────

def _make_row(ticker: str, verdict: str, eligible: bool) -> dict:
    return {
        "ticker": ticker,
        "strategy_id": "skew_momentum_vertical",
        "verdict": verdict,
        "score": 72.0 if eligible else 20.0,
        "daily_opportunity_eligible": eligible,
        "daily_opportunity_reason": "Eligible for Daily Opportunity based on strategy result." if eligible else "Not eligible.",
    }
