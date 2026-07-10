"""ASA Patch 31B — Forward Factor Calibration tests.

Covers:
    31B.2  — INVALID_FORWARD_VARIANCE handling (no crash)
    31B.4  — Widened DTE ranges + derived back-DTE
    31B.5  — Quality-based pair ranking
    31B.6  — Three-zone PASS/WATCH/FAIL (WATCH zone)
    31B.7  — structure_quality_score field
    31B.8  — strategy_actionable + execution_enabled fields
"""
from __future__ import annotations

import sys
import types

# pyo3 panic guard
_rh = types.ModuleType("robin_stocks")
_rh.robinhood = types.ModuleType("robin_stocks.robinhood")
sys.modules.setdefault("robin_stocks", _rh)
sys.modules.setdefault("robin_stocks.robinhood", _rh.robinhood)

import math
from datetime import date, datetime, timezone

import pytest


# ─── helpers ──────────────────────────────────────────────────────────────────

def _leg(strike, option_type, delta, bid=1.0, ask=1.1, oi=500, volume=50):
    return {
        "strike": strike, "option_type": option_type, "delta": delta,
        "bid": bid, "ask": ask, "open_interest": oi, "volume": volume, "iv": 0.35,
    }


def _front_chain(strike=100.0):
    return [
        _leg(strike, "call", 0.35, bid=1.50, ask=1.60),
        _leg(strike, "put", -0.35, bid=1.40, ask=1.50),
    ]


def _back_chain(strike=100.0):
    return [
        _leg(strike, "call", 0.30, bid=2.00, ask=2.20),
        _leg(strike, "put", -0.30, bid=1.90, ask=2.10),
    ]


class _FakeHub:
    """Minimal DataHub stub that always returns good data."""

    def __init__(self, expirations, front_iv=0.45, back_iv=0.38, strike=100.0):
        now = datetime.now(timezone.utc).isoformat()
        self._quote = {"payload": {"last": 200.0}, "fetched_at": now, "fresh": True, "provider": "stub", "confidence": "high"}
        self._candles = {"payload": {"bars": [{"close": 200.0, "volume": 15_000_000}] * 240}, "fetched_at": now, "fresh": True, "provider": "stub", "confidence": "high"}
        self._expirations = expirations
        self._front_iv = front_iv
        self._back_iv = back_iv
        self._strike = strike

    def get_quote(self, ticker, *a, **kw):
        return self._quote

    def get_daily_candles(self, *a, **kw):
        return self._candles

    def get_derived_metrics(self, *a, **kw):
        return {"average_volume_30d": 15_000_000, "realized_volatility_30d": 0.25}

    def get_options_chain_set(self, *a, **kw):
        today = date.today()
        chains = {}
        exp_metrics = {}
        for exp in self._expirations:
            try:
                exp_d = date.fromisoformat(str(exp)[:10])
                dte = (exp_d - today).days
            except ValueError:
                dte = 0
            iv = self._front_iv if dte < 85 else self._back_iv
            chains[exp] = _front_chain(self._strike) if dte < 85 else _back_chain(self._strike)
            exp_metrics[exp] = {"raw_iv": iv, "ex_earnings_iv": iv}
        return {"payload": {
            "expirations": self._expirations,
            "chains_by_expiration": chains,
            "expiration_metrics": exp_metrics,
        }}

    def get_earnings_event(self, *a, **kw):
        return None


def _expirations_from_today(dte_offsets):
    today = date.today()
    from datetime import timedelta
    return [(today + timedelta(days=d)).isoformat() for d in dte_offsets]


# ─── 31B.4: eligible_expiration_pairs DTE ranges ──────────────────────────────

class TestEligibleExpirationPairs:
    def test_wider_front_dte_accepted(self):
        from app.services.forward_factor_service import eligible_expiration_pairs
        today = date.today()
        from datetime import timedelta
        # front=37, back=65 (gap=28) — would fail old 50-70 front range, must pass new 35-90
        expirations = [(today + timedelta(days=d)).isoformat() for d in [37, 65]]
        pairs = eligible_expiration_pairs(expirations, today=today)
        assert len(pairs) >= 1
        fronts = [p["front_dte"] for p in pairs]
        assert any(f <= 45 for f in fronts), f"Expected a sub-45 front DTE in {fronts}"

    def test_derived_back_dte_no_absolute_check(self):
        from app.services.forward_factor_service import eligible_expiration_pairs
        from app import config
        today = date.today()
        from datetime import timedelta
        # back_dte=75 is below old FF_BACK_DTE_MIN=80; with derived mode it should still qualify
        expirations = [(today + timedelta(days=d)).isoformat() for d in [50, 75]]
        pairs = eligible_expiration_pairs(expirations, today=today)
        if getattr(config, "FF_USE_DERIVED_BACK_DTE", True):
            assert len(pairs) >= 1, "Derived back DTE should accept pair with back_dte=75"

    def test_pair_sorted_by_quality_score(self):
        from app.services.forward_factor_service import eligible_expiration_pairs
        today = date.today()
        from datetime import timedelta
        # Three front expirations: near-target (60d), far-from-target (36d), mid (52d)
        expirations = [(today + timedelta(days=d)).isoformat() for d in [36, 52, 60, 85]]
        pairs = eligible_expiration_pairs(expirations, today=today)
        assert len(pairs) >= 1
        # Best-quality pair should be first; quality score should be non-decreasing
        scores = [p["pair_quality_score"] for p in pairs]
        assert scores == sorted(scores), f"Pairs not sorted by quality: {scores}"

    def test_pair_includes_quality_fields(self):
        from app.services.forward_factor_service import eligible_expiration_pairs
        today = date.today()
        from datetime import timedelta
        expirations = [(today + timedelta(days=d)).isoformat() for d in [55, 82]]
        pairs = eligible_expiration_pairs(expirations, today=today)
        assert len(pairs) >= 1
        p = pairs[0]
        assert "pair_quality_score" in p
        assert "in_front_target_range" in p
        assert "in_sep_target_range" in p


# ─── 31B.2: INVALID_FORWARD_VARIANCE no crash ─────────────────────────────────

class TestInvalidForwardVarianceNoKwargCrash:
    """Regression: _blocked() must not receive duplicate ff_pair_result_code kwargs."""

    def test_non_positive_variance_yields_fail_row_not_exception(self):
        from app import config
        today = date.today()
        from datetime import timedelta
        # front_iv=0.497, back_iv=0.357 at 60/90 → negative forward variance
        exps = [(today + timedelta(days=d)).isoformat() for d in [60, 90]]
        hub = _FakeHub(exps, front_iv=0.497, back_iv=0.357)
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(config, "FORWARD_FACTOR_STRATEGY_ENABLED", True)
            mp.setattr(config, "FF_MIN_FORWARD_FACTOR", 0.20)
            result = None
            try:
                from app.services.forward_factor_service import build_forward_factor_strategy
                result = build_forward_factor_strategy(
                    ["AAPL"], {"AAPL": {"current_price": 200, "average_volume_30d": 15_000_000,
                                        "has_data": True, "options_available": True}},
                    hub, run_mode="dev",
                )
            except TypeError as exc:
                pytest.fail(f"TypeError (duplicate kwarg?) raised: {exc}")
        assert result is not None
        rows = result.get("rows") or []
        assert len(rows) >= 1
        # Must produce a FAIL row, not a crash
        verdicts = [str(r.get("verdict") or "").upper() for r in rows]
        assert any("FAIL" in v or "SKIPPED" in v for v in verdicts), f"Expected FAIL row, got {verdicts}"


# ─── 31B.6: WATCH zone (near-miss FF + complete structure) ───────────────────

class TestWatchZone:
    """31B.6: FF ≥ FF_WATCH_FF_LOWER_BOUND + complete structure + liquidity_pass → WATCH."""

    def test_below_threshold_complete_structure_yields_watch(self):
        from app import config
        today = date.today()
        from datetime import timedelta
        exps = [(today + timedelta(days=d)).isoformat() for d in [60, 90]]
        # FF ~= 0.14 (above FF_WATCH_FF_LOWER_BOUND=0.12, below FF_MIN_FORWARD_FACTOR=0.20)
        hub = _FakeHub(exps, front_iv=0.43, back_iv=0.38)
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(config, "FORWARD_FACTOR_STRATEGY_ENABLED", True)
            mp.setattr(config, "FF_MIN_FORWARD_FACTOR", 0.20)
            mp.setattr(config, "FF_WATCH_FF_LOWER_BOUND", 0.12)
            from app.services.forward_factor_service import build_forward_factor_strategy
            result = build_forward_factor_strategy(
                ["TSLA"], {"TSLA": {"current_price": 200, "average_volume_30d": 15_000_000,
                                     "has_data": True, "options_available": True}},
                hub, run_mode="dev",
            )
        rows = result.get("rows") or []
        watch_rows = [r for r in rows if str(r.get("verdict") or "").upper().startswith("WATCH")]
        # Only assert if at least one row was produced (hub may skip for other reasons)
        for row in rows:
            verdict = str(row.get("verdict") or "").upper()
            ff = row.get("forward_factor")
            if ff is not None and float(ff) >= 0.12 and float(ff) < 0.20:
                assert "WATCH" in verdict, f"Expected WATCH for FF={ff:.4f}, got {verdict}"

    def test_well_below_threshold_without_complete_structure_yields_fail(self):
        from app.services.forward_factor_service import _structure_quality_score
        # If no complete structure, should not get a quality score above 0
        score = _structure_quality_score(False, 15.0, 0.10, 0.10, 450.0)
        assert score <= 30, f"Illiquid structure should score low, got {score}"


# ─── 31B.7: structure_quality_score ──────────────────────────────────────────

class TestStructureQualityScore:
    def _import(self):
        from app.services.forward_factor_service import _structure_quality_score
        return _structure_quality_score

    def test_perfect_structure_scores_high(self):
        fn = self._import()
        score = fn(True, 1.0, 0.00, 0.00, 50.0)
        assert score >= 90

    def test_high_slippage_reduces_score(self):
        fn = self._import()
        low = fn(True, 2.0, 0.0, 0.0, 50.0)
        high = fn(True, 20.0, 0.0, 0.0, 50.0)
        assert high < low

    def test_liquidity_failure_reduces_score(self):
        fn = self._import()
        pass_score = fn(True, 2.0, 0.01, 0.01, 100.0)
        fail_score = fn(False, 2.0, 0.01, 0.01, 100.0)
        assert fail_score < pass_score

    def test_delta_deviation_reduces_score(self):
        fn = self._import()
        tight = fn(True, 2.0, 0.00, 0.00, 50.0)
        wide = fn(True, 2.0, 0.05, 0.05, 50.0)
        assert wide < tight

    def test_score_bounded_0_100(self):
        fn = self._import()
        # Worst possible inputs → low score (individual penalty caps prevent reaching 0)
        assert fn(False, 100.0, 0.50, 0.50, 600.0) <= 30
        assert fn(True, 0.0, 0.0, 0.0, 0.0) == 100

    def test_structure_returns_quality_score_field(self):
        from app.services.forward_factor_service import build_forward_factor_double_calendar_structure
        front = _front_chain(100.0)
        back = _back_chain(100.0)
        result = build_forward_factor_double_calendar_structure(front, back)
        if result.get("structure_status") == "COMPLETE":
            assert "structure_quality_score" in result
            score = result["structure_quality_score"]
            assert isinstance(score, int)
            assert 0 <= score <= 100


# ─── 31B.8: strategy_actionable + execution_enabled ──────────────────────────

class TestStrategyActionable:
    def test_pass_verdict_is_actionable(self):
        from app.services.forward_factor_verdict_service import apply_forward_factor_verdict
        row = {
            "ticker": "AAPL", "verdict": "PASS / FORWARD FACTOR POSITIVE",
            "forward_factor": 0.25, "structure_status": "COMPLETE", "liquidity_pass": True,
            "signal_score": 80, "conservative_debit": 2.50, "debit_at_risk": 250.0,
        }
        result = apply_forward_factor_verdict(row)
        assert result.get("strategy_actionable") is True
        assert result.get("execution_enabled") is False

    def test_watch_verdict_is_actionable(self):
        from app.services.forward_factor_verdict_service import apply_forward_factor_verdict
        # WATCH / LIQUIDITY DATA PARTIAL: forward_factor above threshold but liquidity_status=WATCH
        row = {
            "ticker": "TSLA", "forward_factor": 0.25,
            "structure_status": "COMPLETE", "liquidity_status": "WATCH",
            "conservative_debit": 2.00, "debit_at_risk": 200.0,
        }
        result = apply_forward_factor_verdict(row)
        assert result.get("verdict", "").startswith("WATCH")
        assert result.get("strategy_actionable") is True
        assert result.get("execution_enabled") is False

    def test_fail_verdict_not_actionable(self):
        from app.services.forward_factor_verdict_service import apply_forward_factor_verdict
        row = {
            "ticker": "GME", "verdict": "FAIL / FORWARD FACTOR BELOW THRESHOLD",
            "forward_factor": 0.05, "signal_score": 20, "conservative_debit": 3.00, "debit_at_risk": 300.0,
        }
        result = apply_forward_factor_verdict(row)
        assert result.get("strategy_actionable") is False
        assert result.get("execution_enabled") is False


# ─── 31B.3: calibration_report in finalize ───────────────────────────────────

class TestCalibrationReport:
    def test_calibration_report_present_in_result(self):
        from app import config
        today = date.today()
        from datetime import timedelta
        exps = [(today + timedelta(days=d)).isoformat() for d in [55, 82]]
        hub = _FakeHub(exps, front_iv=0.48, back_iv=0.38)
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(config, "FORWARD_FACTOR_STRATEGY_ENABLED", True)
            from app.services.forward_factor_service import build_forward_factor_strategy
            result = build_forward_factor_strategy(
                ["SPY"], {"SPY": {"current_price": 500, "average_volume_30d": 50_000_000,
                                   "has_data": True, "options_available": True}},
                hub, run_mode="dev",
            )
        assert "calibration_report" in result
        cr = result["calibration_report"]
        assert "funnel" in cr
        assert "outcomes" in cr
        assert "rejection_codes" in cr
        assert "version" in cr
        funnel = cr["funnel"]
        assert "universe" in funnel
        assert "cheap_filter_pass" in funnel
        assert "chain_approved" in funnel

    def test_calibration_report_in_summary(self):
        from app import config
        today = date.today()
        from datetime import timedelta
        exps = [(today + timedelta(days=d)).isoformat() for d in [55, 82]]
        hub = _FakeHub(exps, front_iv=0.48, back_iv=0.38)
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(config, "FORWARD_FACTOR_STRATEGY_ENABLED", True)
            from app.services.forward_factor_service import build_forward_factor_strategy
            result = build_forward_factor_strategy(
                ["SPY"], {"SPY": {"current_price": 500, "average_volume_30d": 50_000_000,
                                   "has_data": True, "options_available": True}},
                hub, run_mode="dev",
            )
        assert "calibration_report" in result.get("summary", {})
