"""
app/services/market_data_provider_registry.py — Provider registry and factory.

Patch 33B: Factory-based registry so credentials/HTTP clients are not
created at import time. Providers are instantiated on first access per request.
"""

from __future__ import annotations

from typing import Any

from app import config
from app.models.market_data_contracts import OptionsDataRequirements
from app.models.provider_capabilities import ProviderCapabilities
from app.providers.options_data_provider import OptionsDataProvider


class ProviderRegistry:
    """
    Returns configured OptionsDataProvider instances by ID.
    Factories are registered at startup; instances are built on demand.
    Nothing is imported from provider modules at class-definition time.
    """

    def __init__(self) -> None:
        self._factories: dict[str, Any] = {}

    def register(self, provider_id: str, factory: Any) -> None:
        """Register a zero-argument callable that returns an OptionsDataProvider."""
        self._factories[provider_id] = factory

    def get(self, provider_id: str) -> OptionsDataProvider | None:
        factory = self._factories.get(provider_id)
        if factory is None:
            return None
        return factory()

    def available_ids(self) -> list[str]:
        return list(self._factories.keys())

    def configured_ids(self) -> list[str]:
        result = []
        for pid in self._factories:
            try:
                p = self.get(pid)
                if p is not None and p.is_configured:
                    result.append(pid)
            except Exception:
                pass
        return result

    def capabilities_summary(self) -> list[dict[str, Any]]:
        """Returns capability dicts for all registered providers. No network calls."""
        result = []
        for pid in self._factories:
            try:
                p = self.get(pid)
                if p is not None:
                    cap = p.capabilities.to_dict()
                    cap["configured"] = p.is_configured
                    result.append(cap)
            except Exception:
                result.append({"provider_id": pid, "error": "failed to instantiate"})
        return result

    def select_providers(
        self,
        requirements: OptionsDataRequirements,
        order: list[str] | None = None,
    ) -> list[OptionsDataProvider]:
        """
        Return a list of providers that satisfy requirements, in preference order.
        If order is None, uses config.OPTIONS_PROVIDER_ORDER.
        """
        provider_order = order or config.OPTIONS_PROVIDER_ORDER
        selected: list[OptionsDataProvider] = []

        for pid in provider_order:
            provider = self.get(pid)
            if provider is None or not provider.is_configured:
                continue
            ok, _ = provider.capabilities.satisfies(requirements)
            if ok:
                selected.append(provider)

        return selected


# ─── Default registry (module-level singleton) ─────────────────────────────

def _build_default_registry() -> ProviderRegistry:
    registry = ProviderRegistry()

    def _tradier_factory() -> OptionsDataProvider:
        from app.providers.tradier_options_adapter import TradierOptionsAdapter
        return TradierOptionsAdapter()

    def _marketdata_factory() -> OptionsDataProvider:
        from app.providers.marketdata_provider import MarketDataProvider
        return MarketDataProvider()

    def _last_known_good_factory() -> OptionsDataProvider:
        from app.services.options_chain_cache import LastKnownGoodCacheProvider
        return LastKnownGoodCacheProvider()

    registry.register("tradier", _tradier_factory)
    registry.register("marketdata", _marketdata_factory)
    registry.register("last_known_good_cache", _last_known_good_factory)

    return registry


_default_registry: ProviderRegistry | None = None


def get_default_registry() -> ProviderRegistry:
    global _default_registry
    if _default_registry is None:
        _default_registry = _build_default_registry()
    return _default_registry
