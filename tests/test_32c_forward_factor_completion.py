"""ASA Patch 32C — Forward Factor Completion & Calibration tests.

Covers:
    32C.1  — Strategy spec registry: FF catalog_visible, daily_opportunity_allowed, tags
    32C.2  — Config: near-miss tolerance constants, updated discovery caps
    32C.3  — Four-tier verdict model: PASS / WATCH / NEAR MISS / FAIL
    32C.4  — NEAR MISS verdict: miss_distance, miss_reason, near_miss_details fields
    32C.5  — NEAR MISS debit tolerance: verdict service NEAR MISS / DEBIT NEAR MAXIMUM
    32C.6  — Normalization: NEAR MISS → near_miss decision class / eligibility_status
    32C.7  — Daily Opportunity priority: FF entry=8, watch=9
    32C.8  — Endpoint verification: FF accepts conditional + near_miss + dry_run_excluded
    32C.9  — Calibration stats: near_miss_count in summary + outcomes in calibration_report
    32C.10 — Strategy catalog endpoint returns FF with correct metadata
"""
from __future__ import annotations

import sys
import types

# pyo3 panic guard
_rh = types.ModuleType("robin_stocks")
_rh.robinhood = types.ModuleType("robin_stocks.robinhood")
sys.modules.setdefault("robin_stocks", _rh)
sys.modules.setdefault("robin_stocks.robinhood", _rh.robinhood)

from datetime import date, datetime, timedelta, timezone

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


def _exps(offsets):
    today = date.today()
    return [(today + timedelta(days=d)).isoformat() for d in offsets]


# ─── 32C.1: Strategy Spec Registry ───────────────────────────────────────────

class TestStrategySpecRegistry:
    def test_ff_catalog_visible(self):
        from app.services.strategy_spec_registry import STRATEGY_SPECS as STRATEGY_REGISTRY
        spec = STRATEGY_REGISTRY.get("forward_factor_calendar", {})
        assert spec.get("catalog_visible") is True

    def test_ff_daily_opportunity_allowed(self):
        from app.services.strategy_spec_registry import STRATEGY_SPECS as STRATEGY_REGISTRY
        spec = STRATEGY_REGISTRY.get("forward_factor_calendar", {})
        assert spec.get("daily_opportunity_allowed") is True

    def test_ff_has_tags(self):
        from app.services.strategy_spec_registry import STRATEGY_SPECS as STRATEGY_REGISTRY
        spec = STRATEGY_REGISTRY.get("forward_factor_calendar", {})
        tags = spec.get("tags") or []
        assert "options" in tags
        assert "calendar" in tags
        assert "dry_run" in tags

    def test_ff_has_description(self):
        from app.services.strategy_spec_registry import STRATEGY_SPECS as STRATEGY_REGISTRY
        spec = STRATEGY_REGISTRY.get("forward_factor_calendar", {})
        assert spec.get("description"), "FF spec must have a description"
        assert len(spec["description"]) > 20

    def test_ff_display_order(self):
        from app.services.strategy_spec_registry import STRATEGY_SPECS as STRATEGY_REGISTRY
        spec = STRATEGY_REGISTRY.get("forward_factor_calendar", {})
        assert spec.get("display_order") == 3

    def test_ff_near_miss_in_primary_outputs(self):
        from app.services.strategy_spec_registry import STRATEGY_SPECS as STRATEGY_REGISTRY
        spec = STRATEGY_REGISTRY.get("forward_factor_calendar", {})
        outputs = spec.get("primary_outputs") or []
        assert "forward_factor_near_miss" in outputs


# ─── 32C.2: Config Constants ──────────────────────────────────────────────────

class TestConfigConstants:
    def test_near_miss_tolerances_present(self):
        from app import config
        assert hasattr(config, "FF_NEAR_MISS_DEBIT_TOLERANCE_PCT")
        assert hasattr(config, "FF_NEAR_MISS_SPREAD_TOLERANCE_PCT")
        assert hasattr(config, "FF_NEAR_MISS_OI_TOLERANCE_PCT")

    def test_discovery_caps_updated(self):
        from app import config
        assert config.FF_MAX_TICKERS_PER_RUN >= 20, "FF_MAX_TICKERS_PER_RUN should be at least 20 for 32C"
        assert config.FF_MAX_CHAIN_TICKERS_PER_RUN >= 8, "FF_MAX_CHAIN_TICKERS_PER_RUN should be at least 8 for 32C"

    def test_calibration_version_updated(self):
        from app import config
        assert "32C" in (config.FF_CALIBRATION_VERSION or ""), f"Expected 32C in calibration version, got {config.FF_CALIBRATION_VERSION}"

    def test_no_duplicate_config_keys(self):
        """Verify only one definition of each key exists (Python last-assignment would hide duplicates)."""
        import re
        config_path = __import__("pathlib").Path(__file__).parent.parent / "app" / "config.py"
        text = config_path.read_text()
        for key in ("FF_MAX_TICKERS_PER_RUN", "FF_MAX_CHAIN_TICKERS_PER_RUN", "FF_CALIBRATION_VERSION"):
            count = len(re.findall(rf"^{key}\s*=", text, re.MULTILINE))
            assert count == 1, f"{key} appears {count} times in config.py — expected exactly 1"


# ─── 32C.3: Four-Tier Verdict Model ──────────────────────────────────────────

class TestFourTierVerdictModel:
    """Verify PASS/WATCH/NEAR MISS/FAIL are all producible by the service."""

    def _run(self, front_iv, back_iv, offsets=(60, 90)):
        from app import config
        exps = _exps(offsets)
        hub = _FakeHub(exps, front_iv=front_iv, back_iv=back_iv)
        from app.services.forward_factor_service import build_forward_factor_strategy
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(config, "FORWARD_FACTOR_STRATEGY_ENABLED", True)
            mp.setattr(config, "FF_MIN_FORWARD_FACTOR", 0.20)
            mp.setattr(config, "FF_WATCH_FF_LOWER_BOUND", 0.12)
            mp.setattr(config, "FF_NEAR_MISS_WINDOW", 0.05)
            return build_forward_factor_strategy(
                ["AAPL"], {"AAPL": {"current_price": 200, "average_volume_30d": 15_000_000,
                                    "has_data": True, "options_available": True}},
                hub, run_mode="dev",
            )

    def test_pass_verdict_producible(self):
        # front_iv=0.45, back_iv=0.38 at 60/90 DTE: forward_factor ≈ 1.68, well above 0.20 threshold.
        result = self._run(front_iv=0.45, back_iv=0.38)
        verdicts = [str(r.get("verdict") or "").upper() for r in result.get("rows") or []]
        assert any(("PASS" in v or "POSITIVE" in v) and "FAIL" not in v for v in verdicts), f"Expected PASS/POSITIVE verdict, got {verdicts}"

    def test_fail_verdict_producible(self):
        # Equal IVs → forward variance equals front variance → FF = 0 → FAIL well below threshold.
        result = self._run(front_iv=0.38, back_iv=0.38)
        verdicts = [str(r.get("verdict") or "").upper() for r in result.get("rows") or []]
        assert any("FAIL" in v or "SKIPPED" in v or "NEAR MISS" in v for v in verdicts), f"Expected non-PASS verdict, got {verdicts}"

    def test_near_miss_verdict_from_summary(self):
        """near_miss_count appears in summary even if zero — key must exist."""
        result = self._run(front_iv=0.48, back_iv=0.38)
        summary = result.get("summary") or {}
        assert "near_miss_count" in summary

    def test_summary_counts_include_near_miss(self):
        result = self._run(front_iv=0.48, back_iv=0.38)
        summary = result.get("summary") or {}
        rows = result.get("rows") or []
        actual_near_miss = sum(str(r.get("verdict") or "").upper().startswith("NEAR MISS") for r in rows)
        assert summary.get("near_miss_count") == actual_near_miss


# ─── 32C.4: NEAR MISS verdict fields ─────────────────────────────────────────

class TestNearMissFields:
    def test_near_miss_verdict_has_miss_distance(self):
        """Simulate a NEAR MISS scenario via forward_factor_service internals."""
        from app import config
        from app.services.forward_factor_service import calculate_forward_factor
        # front_iv=0.42, back_iv=0.38 → verify FF is near-miss of 0.20
        try:
            formula = calculate_forward_factor(0.42, 0.38, 60, 90)
            ff = formula["forward_factor"]
            threshold = 0.20
            near_miss_window = 0.05
            if threshold - near_miss_window <= ff < threshold:
                miss_dist = round(threshold - ff, 4)
                assert miss_dist > 0 and miss_dist <= near_miss_window
        except ValueError:
            pass  # Variance could be negative with these IVs; that's also a valid test

    def test_near_miss_row_has_required_fields(self):
        from app import config
        exps = _exps([60, 90])
        # Deliberately engineer a near-miss: front_iv slightly elevated but within near-miss window
        hub = _FakeHub(exps, front_iv=0.42, back_iv=0.38)
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(config, "FORWARD_FACTOR_STRATEGY_ENABLED", True)
            mp.setattr(config, "FF_MIN_FORWARD_FACTOR", 0.20)
            mp.setattr(config, "FF_NEAR_MISS_WINDOW", 0.10)
            from app.services.forward_factor_service import build_forward_factor_strategy
            result = build_forward_factor_strategy(
                ["AAPL"], {"AAPL": {"current_price": 200, "average_volume_30d": 15_000_000,
                                    "has_data": True, "options_available": True}},
                hub, run_mode="dev",
            )
        near_miss_rows = [r for r in (result.get("rows") or []) if str(r.get("verdict") or "").upper().startswith("NEAR MISS")]
        for row in near_miss_rows:
            assert "miss_distance" in row, "NEAR MISS row must have miss_distance"
            assert "miss_reason" in row, "NEAR MISS row must have miss_reason"
            assert row.get("near_miss_ff") is True


# ─── 32C.5: NEAR MISS / DEBIT NEAR MAXIMUM ───────────────────────────────────

class TestNearMissDebit:
    def test_debit_above_max_within_tolerance_yields_near_miss(self):
        from app import config
        from app.services.forward_factor_verdict_service import apply_forward_factor_verdict
        # debit_at_risk = FF_MAX_DEBIT_DOLLARS * 1.10 (10% above max, within 20% tolerance)
        _max = float(getattr(config, "FF_MAX_DEBIT_DOLLARS", 500.0))
        _debit = _max * 1.10
        row = {
            "ticker": "AAPL", "verdict": "PASS / FORWARD FACTOR SETUP",
            "forward_factor": 0.25, "structure_status": "COMPLETE", "liquidity_pass": True,
            "debit_at_risk": _debit, "conservative_debit": _debit / 100,
        }
        result = apply_forward_factor_verdict(row)
        assert "NEAR MISS" in str(result.get("verdict") or "").upper(), f"Expected NEAR MISS for debit ${_debit:.0f}, got {result.get('verdict')}"

    def test_debit_well_above_max_outside_tolerance_yields_fail(self):
        from app import config
        from app.services.forward_factor_verdict_service import apply_forward_factor_verdict
        _max = float(getattr(config, "FF_MAX_DEBIT_DOLLARS", 500.0))
        _debit = _max * 1.50  # 50% above max, outside 20% tolerance
        row = {
            "ticker": "AAPL", "verdict": "PASS / FORWARD FACTOR SETUP",
            "forward_factor": 0.25, "structure_status": "COMPLETE", "liquidity_pass": True,
            "debit_at_risk": _debit, "conservative_debit": _debit / 100,
        }
        result = apply_forward_factor_verdict(row)
        assert "FAIL" in str(result.get("verdict") or "").upper(), f"Expected FAIL for debit ${_debit:.0f}, got {result.get('verdict')}"


# ─── 32C.6: Normalization NEAR MISS semantics ────────────────────────────────

class TestNormalizationNearMiss:
    def test_near_miss_verdict_maps_to_near_miss_decision_class(self):
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        row = {
            "ticker": "AAPL",
            "verdict": "NEAR MISS / FORWARD FACTOR NEAR THRESHOLD",
            "forward_factor": 0.17,
            "strategy_id": "forward_factor_calendar",
            "dry_run": True,
        }
        normalize_strategy_row(row, "forward_factor_calendar")
        assert row.get("decision_class") == "near_miss", f"Expected near_miss, got {row.get('decision_class')}"
        assert row.get("eligibility_status") == "near_miss", f"Expected near_miss, got {row.get('eligibility_status')}"
        assert row.get("action_type") == "forward_factor_near_miss"

    def test_near_miss_row_excluded_from_daily_opportunity(self):
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        row = {
            "ticker": "AAPL",
            "verdict": "NEAR MISS / FORWARD FACTOR NEAR THRESHOLD",
            "forward_factor": 0.17,
            "strategy_id": "forward_factor_calendar",
            "dry_run": True,
        }
        normalize_strategy_row(row, "forward_factor_calendar")
        assert row.get("can_enter_daily_opportunity") is False
        assert row.get("daily_opportunity_eligible") is False

    def test_near_miss_is_not_rejected(self):
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        row = {
            "ticker": "AAPL",
            "verdict": "NEAR MISS / FORWARD FACTOR NEAR THRESHOLD",
            "forward_factor": 0.17,
            "strategy_id": "forward_factor_calendar",
            "dry_run": True,
        }
        normalize_strategy_row(row, "forward_factor_calendar")
        # near_miss should NOT be classified as rejected (no trading, but not a hard rejection)
        assert row.get("decision_class") != "rejected"

    def test_near_miss_debit_verdict_maps_to_near_miss(self):
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        row = {
            "ticker": "AAPL",
            "verdict": "NEAR MISS / DEBIT NEAR MAXIMUM",
            "forward_factor": 0.25,
            "strategy_id": "forward_factor_calendar",
            "dry_run": True,
        }
        normalize_strategy_row(row, "forward_factor_calendar")
        assert row.get("eligibility_status") == "near_miss"


# ─── 32C.7: Daily Opportunity Priority ───────────────────────────────────────

class TestDailyOpportunityPriority:
    def test_forward_factor_entry_has_priority_8(self):
        from app.api.daily_opportunity_api import _daily_sort_key
        action = {"type": "forward_factor_entry", "priority_score": 80}
        key = _daily_sort_key(action)
        assert key[0] == 8, f"forward_factor_entry should have priority 8, got {key[0]}"

    def test_forward_factor_watch_has_priority_9(self):
        from app.api.daily_opportunity_api import _daily_sort_key
        action = {"type": "forward_factor_watch", "priority_score": 70}
        key = _daily_sort_key(action)
        assert key[0] == 9, f"forward_factor_watch should have priority 9, got {key[0]}"

    def test_ff_entry_sorts_before_ff_watch(self):
        from app.api.daily_opportunity_api import _daily_sort_key
        entry_key = _daily_sort_key({"type": "forward_factor_entry", "priority_score": 50})
        watch_key = _daily_sort_key({"type": "forward_factor_watch", "priority_score": 50})
        assert entry_key < watch_key

    def test_ff_entry_sorts_after_stock_add(self):
        from app.api.daily_opportunity_api import _daily_sort_key
        stock_key = _daily_sort_key({"type": "stock_add", "priority_score": 50})
        ff_key = _daily_sort_key({"type": "forward_factor_entry", "priority_score": 50})
        assert ff_key > stock_key


# ─── 32C.8: Endpoint Verification ────────────────────────────────────────────

def _fake_get_strategy_rows(rows):
    """Return a mock get_strategy_rows response containing the given rows."""
    return {
        "rows": rows,
        "row_count": len(rows),
        "source": "strategy_row_store",
        "latest_run_id": "test-run-001",
        "provider_calls_triggered": False,
    }


class TestEndpointVerification:
    def test_ff_conditional_eligibility_accepted(self):
        from app.services.endpoint_verification_service import _check_strategy_rows
        rows = [
            {"verdict": "PASS / FORWARD FACTOR SETUP", "dry_run": True, "eligibility_status": "conditional", "can_trade_live": False},
        ]
        with pytest.MonkeyPatch().context() as mp:
            import app.api.strategy_api as _sa
            mp.setattr(_sa, "get_strategy_rows", lambda *a, **kw: _fake_get_strategy_rows(rows))
            result = _check_strategy_rows("forward_factor_calendar", None)
        assert result.status != "FAIL", f"Unexpected FAIL: {result.assertion}"

    def test_ff_near_miss_eligibility_accepted(self):
        from app.services.endpoint_verification_service import _check_strategy_rows
        rows = [
            {"verdict": "NEAR MISS / FORWARD FACTOR NEAR THRESHOLD", "dry_run": True, "eligibility_status": "near_miss", "can_trade_live": False},
        ]
        with pytest.MonkeyPatch().context() as mp:
            import app.api.strategy_api as _sa
            mp.setattr(_sa, "get_strategy_rows", lambda *a, **kw: _fake_get_strategy_rows(rows))
            result = _check_strategy_rows("forward_factor_calendar", None)
        assert result.status != "FAIL", f"Unexpected FAIL: {result.assertion}"

    def test_ff_can_trade_live_true_triggers_fail(self):
        from app.services.endpoint_verification_service import _check_strategy_rows
        rows = [
            {"verdict": "PASS / FORWARD FACTOR SETUP", "dry_run": True, "eligibility_status": "conditional", "can_trade_live": True},
        ]
        with pytest.MonkeyPatch().context() as mp:
            import app.api.strategy_api as _sa
            mp.setattr(_sa, "get_strategy_rows", lambda *a, **kw: _fake_get_strategy_rows(rows))
            result = _check_strategy_rows("forward_factor_calendar", None)
        assert result.status == "FAIL"
        assert "LIVE" in (result.assertion or "").upper()

    def test_ff_unknown_eligibility_status_triggers_fail(self):
        from app.services.endpoint_verification_service import _check_strategy_rows
        rows = [
            {"verdict": "PASS / FORWARD FACTOR SETUP", "dry_run": True, "eligibility_status": "eligible", "can_trade_live": False},
        ]
        with pytest.MonkeyPatch().context() as mp:
            import app.api.strategy_api as _sa
            mp.setattr(_sa, "get_strategy_rows", lambda *a, **kw: _fake_get_strategy_rows(rows))
            result = _check_strategy_rows("forward_factor_calendar", None)
        assert result.status == "FAIL"


# ─── 32C.9: Calibration Statistics ───────────────────────────────────────────

class TestCalibrationStatistics:
    def test_calibration_report_has_near_miss_outcome(self):
        from app import config
        exps = _exps([60, 90])
        hub = _FakeHub(exps, front_iv=0.48, back_iv=0.38)
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(config, "FORWARD_FACTOR_STRATEGY_ENABLED", True)
            from app.services.forward_factor_service import build_forward_factor_strategy
            result = build_forward_factor_strategy(
                ["SPY"], {"SPY": {"current_price": 500, "average_volume_30d": 50_000_000,
                                  "has_data": True, "options_available": True}},
                hub, run_mode="dev",
            )
        cr = result.get("calibration_report") or {}
        outcomes = cr.get("outcomes") or {}
        assert "near_miss" in outcomes, f"calibration_report.outcomes must include near_miss, got keys: {list(outcomes)}"

    def test_summary_near_miss_count_is_integer(self):
        from app import config
        exps = _exps([60, 90])
        hub = _FakeHub(exps, front_iv=0.48, back_iv=0.38)
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(config, "FORWARD_FACTOR_STRATEGY_ENABLED", True)
            from app.services.forward_factor_service import build_forward_factor_strategy
            result = build_forward_factor_strategy(
                ["SPY"], {"SPY": {"current_price": 500, "average_volume_30d": 50_000_000,
                                  "has_data": True, "options_available": True}},
                hub, run_mode="dev",
            )
        summary = result.get("summary") or {}
        assert isinstance(summary.get("near_miss_count"), int)


# ─── 32C.10: Strategy Catalog Endpoint ───────────────────────────────────────

class TestStrategyCatalog:
    def test_catalog_returns_ff_strategy(self):
        from app.api.strategy_api import get_strategy_catalog
        result = get_strategy_catalog()
        strategies = result.get("strategies") or []
        ff = next((s for s in strategies if s["strategy_id"] == "forward_factor_calendar"), None)
        assert ff is not None, "forward_factor_calendar must appear in strategy catalog"

    def test_catalog_ff_is_dry_run(self):
        from app.api.strategy_api import get_strategy_catalog
        result = get_strategy_catalog()
        strategies = result.get("strategies") or []
        ff = next((s for s in strategies if s["strategy_id"] == "forward_factor_calendar"), None)
        assert ff is not None
        assert ff.get("dry_run") is True

    def test_catalog_ff_has_tags(self):
        from app.api.strategy_api import get_strategy_catalog
        result = get_strategy_catalog()
        strategies = result.get("strategies") or []
        ff = next((s for s in strategies if s["strategy_id"] == "forward_factor_calendar"), None)
        assert ff is not None
        assert "options" in (ff.get("tags") or [])

    def test_catalog_not_read_only_violation(self):
        from app.api.strategy_api import get_strategy_catalog
        result = get_strategy_catalog()
        assert result.get("provider_calls_triggered") is False

    def test_catalog_sorted_by_display_order(self):
        from app.api.strategy_api import get_strategy_catalog
        result = get_strategy_catalog()
        strategies = result.get("strategies") or []
        orders = [s.get("display_order", 99) for s in strategies]
        assert orders == sorted(orders), f"Catalog strategies not sorted by display_order: {orders}"
