"""
ASA Patch 30A — Strategy Registry Tests

Covers STRATEGY_SPEC_REGISTRY (dict-based, 5 entries) in app/strategies/registry.py.
The existing list-based STRATEGY_REGISTRY is not modified; these tests verify
the new dict registry alongside it.
"""
from __future__ import annotations

import py_compile


class TestCompile:
    def test_registry_compiles(self):
        py_compile.compile("app/strategies/registry.py", doraise=True)


class TestStrategySpecRegistry:
    def _registry(self):
        from app.strategies.registry import STRATEGY_SPEC_REGISTRY
        return STRATEGY_SPEC_REGISTRY

    def test_registry_is_dict(self):
        assert isinstance(self._registry(), dict)

    def test_registry_has_five_entries(self):
        assert len(self._registry()) == 5, f"Expected 5 entries, got {len(self._registry())}: {list(self._registry())}"

    def test_registry_contains_four_production_strategies(self):
        reg = self._registry()
        for sid in ("earnings_calendar", "skew_momentum_vertical", "forward_factor_calendar", "stock_momentum"):
            assert sid in reg, f"Missing production strategy {sid!r}"

    def test_registry_contains_test_clone(self):
        assert "stock_momentum_unified_test" in self._registry()

    def test_each_spec_has_required_keys(self):
        required = ("strategy_id", "strategy_name", "status", "dry_run", "daily_opportunity_allowed")
        for sid, spec in self._registry().items():
            for key in required:
                assert key in spec, f"{sid}: missing key {key!r}"

    def test_strategy_ids_match_keys(self):
        for key, spec in self._registry().items():
            assert spec["strategy_id"] == key, f"Key {key!r} != strategy_id {spec['strategy_id']!r}"

    def test_ff_is_dry_run_in_spec_registry(self):
        spec = self._registry().get("forward_factor_calendar", {})
        assert spec.get("dry_run") is True

    def test_ff_daily_opportunity_not_allowed(self):
        # 32C: FF promoted to daily_opportunity_allowed=True (research signals only; dry_run enforces read-only).
        spec = self._registry().get("forward_factor_calendar", {})
        assert spec.get("daily_opportunity_allowed") is True

    def test_test_clone_is_dry_run(self):
        spec = self._registry().get("stock_momentum_unified_test", {})
        assert spec.get("dry_run") is True

    def test_test_clone_daily_opportunity_not_allowed(self):
        spec = self._registry().get("stock_momentum_unified_test", {})
        assert spec.get("daily_opportunity_allowed") is False

    def test_test_clone_status_is_test(self):
        spec = self._registry().get("stock_momentum_unified_test", {})
        assert spec.get("status") == "test"

    def test_production_strategies_not_broken(self):
        # The original list-based STRATEGY_REGISTRY must still exist and have 4 entries.
        from app.strategies.registry import STRATEGY_REGISTRY
        assert isinstance(STRATEGY_REGISTRY, list)
        assert len(STRATEGY_REGISTRY) == 4

    def test_enabled_strategies_unchanged(self):
        # enabled_strategies() must not include the test clone (it's never enabled).
        from app.strategies.registry import enabled_strategies
        ids = [s.strategy_id for s in enabled_strategies()]
        assert "stock_momentum_unified_test" not in ids

    def test_spec_registry_has_schema_version(self):
        from app.strategies.schema import SCHEMA_VERSION
        spec = self._registry().get("stock_momentum_unified_test", {})
        assert spec.get("schema_version") == SCHEMA_VERSION
