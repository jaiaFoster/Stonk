"""
ASA Patch 30C — Earnings Calendar Daily Opportunity Compatibility Tests

Verifies current canonical Daily Opportunity behavior:
  - trade_verdict=PASS + entry_allowed=True + recommended_action=ENTER → eligible
  - calendar_entry_allowed=False → excluded
  - FF always excluded
  - Universal daily_opportunity dict agrees with existing daily_opportunity_eligible bool
  - Lifecycle rows are always excluded from Daily Opportunity
"""
from __future__ import annotations

import py_compile


class TestCompile:
    def test_universal_compiles(self):
        py_compile.compile("app/strategies/earnings_calendar_universal.py", doraise=True)

    def test_service_compiles(self):
        py_compile.compile("app/services/earnings_calendar_strategy_service.py", doraise=True)


# ─── Legacy DO eligibility rules unchanged ────────────────────────────────────

class TestDailyOpportunityEligibility:
    def _normalize(self, row: dict, strategy_id: str) -> dict:
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        normalize_strategy_row(row, strategy_id)
        return row

    def test_canonical_calendar_entry_is_eligible(self):
        row = {"ticker": "BAC", "verdict": "PASS / CALENDAR", "trade_verdict": "PASS", "entry_allowed": True, "recommended_action": "ENTER"}
        self._normalize(row, "earnings_calendar")
        assert row.get("daily_opportunity_eligible") is True

    def test_calendar_entry_allowed_false_is_not_eligible(self):
        row = {"ticker": "FAST", "action": "AVOID / SHORT LEG EVENT RISK", "calendar_entry_allowed": False}
        self._normalize(row, "earnings_calendar")
        assert row.get("daily_opportunity_eligible") is False

    def test_conflict_row_not_eligible(self):
        row = {"ticker": "FAST", "action": "FAIL / EARNINGS DATE CONFLICT", "calendar_entry_allowed": False}
        self._normalize(row, "earnings_calendar")
        assert row.get("daily_opportunity_eligible") is False

    def test_manual_review_not_eligible(self):
        row = {"ticker": "AAPL", "action": "MANUAL REVIEW / TIMESTAMP NEEDED", "calendar_entry_allowed": False}
        self._normalize(row, "earnings_calendar")
        assert row.get("daily_opportunity_eligible") is False

    def test_ff_always_excluded(self):
        row = {"ticker": "AAPL", "verdict": "PASS — FF signal"}
        self._normalize(row, "forward_factor_calendar")
        assert row.get("daily_opportunity_eligible") is False

    def test_ff_can_trade_live_false(self):
        row = {"ticker": "AAPL", "verdict": "PASS — FF signal"}
        self._normalize(row, "forward_factor_calendar")
        assert row.get("can_trade_live") is False


# ─── Universal dict agrees with existing bool ──────────────────────────────────

class TestUniversalDODictAgreement:
    def _build_row(self, action: str, calendar_entry_allowed: bool) -> dict:
        from app.strategies.earnings_calendar_universal import build_earnings_calendar_universal_row
        row = {
            "ticker": "BAC",
            "action": action,
            "score": 72.0,
            "calendar_entry_allowed": calendar_entry_allowed,
            "daily_opportunity_eligible": calendar_entry_allowed,
            "daily_opportunity_reason": "Eligible for Daily Opportunity." if calendar_entry_allowed else "Not eligible.",
        }
        build_earnings_calendar_universal_row(row)
        return row

    def test_universal_eligible_matches_bool_true(self):
        row = self._build_row("EARNINGS CALENDAR CANDIDATE", True)
        assert row["daily_opportunity"]["eligible"] is True

    def test_universal_eligible_matches_bool_false(self):
        row = self._build_row("AVOID / SHORT LEG EVENT RISK", False)
        assert row["daily_opportunity"]["eligible"] is False

    def test_exclusion_reason_empty_when_eligible(self):
        row = self._build_row("EARNINGS CALENDAR CANDIDATE", True)
        assert row["daily_opportunity"]["exclusion_reason"] == ""

    def test_exclusion_reason_set_when_ineligible(self):
        row = self._build_row("FAIL / EARNINGS DATE CONFLICT", False)
        assert row["daily_opportunity"]["exclusion_reason"]

    def test_bucket_is_earnings_calendar(self):
        row = self._build_row("EARNINGS CALENDAR CANDIDATE", True)
        assert row["daily_opportunity"]["bucket"] == "earnings_calendar"

    def test_priority_set_when_eligible(self):
        row = self._build_row("EARNINGS CALENDAR CANDIDATE", True)
        assert row["daily_opportunity"]["priority"] is not None

    def test_priority_none_when_ineligible(self):
        row = self._build_row("AVOID / SHORT LEG EVENT RISK", False)
        assert row["daily_opportunity"]["priority"] is None


# ─── Lifecycle rows always excluded from Daily Opportunity ────────────────────

class TestLifecycleDOExclusion:
    def _build_check(self, action: str = "HOLD / MONITOR") -> dict:
        from app.strategies.earnings_calendar_universal import build_earnings_lifecycle_universal_row
        check = {
            "ticker": "SBUX",
            "action": action,
            "lifecycle_priority_score": 30.0,
        }
        build_earnings_lifecycle_universal_row(check)
        return check

    def test_hold_monitor_not_eligible(self):
        check = self._build_check("HOLD / MONITOR")
        assert check["daily_opportunity"]["eligible"] is False

    def test_take_profit_not_eligible(self):
        check = self._build_check("TAKE PROFIT / REVIEW EXIT")
        assert check["daily_opportunity"]["eligible"] is False

    def test_lifecycle_do_gate_is_skipped(self):
        check = self._build_check()
        gate = check["gate_groups"]["daily_opportunity"]["eligible"]
        assert gate["status"] == "skipped"


# ─── End-to-end: production service DO counts stable ─────────────────────────

class TestProductionDOCountsStable:
    def _run(self, candidates: list, earnings: dict) -> dict:
        from app.services.earnings_calendar_strategy_service import evaluate_earnings_calendar_candidates
        return evaluate_earnings_calendar_candidates(candidates, earnings)

    def _do_count_legacy(self, result: dict) -> int:
        return sum(1 for item in result.get("items") or [] if item.get("daily_opportunity_eligible") is True)

    def _do_count_universal(self, result: dict) -> int:
        return sum(
            1 for item in result.get("items") or []
            if (item.get("daily_opportunity") or {}).get("eligible") is True
        )

    def test_legacy_and_universal_do_counts_match(self):
        candidates = [_base_candidate("BAC"), _base_candidate("FAST")]
        earnings = {
            "BAC": _bac_earnings(),
            "FAST": _conflict_earnings("FAST"),
        }
        result = self._run(candidates, earnings)
        legacy = self._do_count_legacy(result)
        universal = self._do_count_universal(result)
        assert legacy == universal, f"Legacy DO count {legacy} != universal DO count {universal}"

    def test_conflict_row_do_agrees(self):
        candidates = [_base_candidate("FAST")]
        earnings = {"FAST": _conflict_earnings("FAST")}
        result = self._run(candidates, earnings)
        for item in result.get("items") or []:
            if item["ticker"] == "FAST":
                bool_eligible = item.get("daily_opportunity_eligible")
                dict_eligible = (item.get("daily_opportunity") or {}).get("eligible")
                assert bool_eligible == dict_eligible

    def test_no_reconciliation_needed_for_matching_counts(self):
        candidates = [_base_candidate("BAC")]
        earnings = {"BAC": _bac_earnings()}
        result = self._run(candidates, earnings)
        for item in result.get("items") or []:
            bool_eligible = item.get("daily_opportunity_eligible")
            dict_eligible = (item.get("daily_opportunity") or {}).get("eligible")
            assert bool_eligible == dict_eligible, (
                f"{item['ticker']}: bool={bool_eligible} != dict={dict_eligible}"
            )


# ─── Fixtures ─────────────────────────────────────────────────────────────────

def _base_candidate(ticker: str = "BAC") -> dict:
    return {
        "ticker": ticker,
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


def _conflict_earnings(ticker: str) -> dict:
    return {
        "ticker": ticker,
        "has_data": True,
        "earnings_date": "2026-07-14",
        "date": "2026-07-14",
        "time_of_day": "after_close",
        "session_label": "After Close",
        "is_timestamp_confirmed": False,
        "earnings_date_confidence": "disputed",
        "date_confidence": "low",
        "date_conflict": True,
        "date_sources": ["finnhub", "tradier"],
        "sources_seen": ["finnhub", "tradier"],
        "earnings_source_count": 2,
        "earnings_source_conflict": True,
    }
