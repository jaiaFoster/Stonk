"""
app/models/provider_capabilities.py — Provider capability declarations.

Patch 33B: Strategies declare what they need; the gateway selects the right provider.
No strategy code should reference a provider by name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ProviderCapabilities:
    """Declares what a specific data provider can and cannot supply."""

    provider_id: str

    # Chain data
    can_supply_live_chains: bool = False
    can_supply_delayed_chains: bool = False
    can_supply_greeks: bool = False
    can_supply_implied_volatility: bool = False
    can_supply_open_interest: bool = False
    can_supply_bid_ask: bool = False
    can_supply_volume: bool = False

    # Underlying quote
    can_supply_underlying_quote: bool = False

    # Expiration access
    can_supply_multiple_expirations: bool = False
    max_expirations_per_request: int | None = None

    # Data quality
    typical_delay_seconds: int = 0
    supports_greeks_on_chain: bool = False

    # Additional metadata
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def satisfies(self, requirements: "OptionsDataRequirements") -> tuple[bool, list[str]]:  # noqa: F821
        """
        Returns (ok, reasons_failed). reasons_failed is empty when ok is True.
        Deferred import to avoid circular dependency with market_data_contracts.
        """
        from app.models.market_data_contracts import OptionsDataRequirements

        failures: list[str] = []

        if requirements.live_required and not self.can_supply_live_chains:
            failures.append(f"{self.provider_id}: live data required but not available")

        if (
            requirements.maximum_delay_seconds is not None
            and self.typical_delay_seconds > requirements.maximum_delay_seconds
        ):
            failures.append(
                f"{self.provider_id}: delay {self.typical_delay_seconds}s exceeds max {requirements.maximum_delay_seconds}s"
            )

        if requirements.greeks_required and not self.can_supply_greeks:
            failures.append(f"{self.provider_id}: greeks required but not available")

        if requirements.implied_volatility_required and not self.can_supply_implied_volatility:
            failures.append(f"{self.provider_id}: implied_volatility required but not available")

        if requirements.bid_ask_required and not self.can_supply_bid_ask:
            failures.append(f"{self.provider_id}: bid/ask required but not available")

        if requirements.open_interest_required and not self.can_supply_open_interest:
            failures.append(f"{self.provider_id}: open_interest required but not available")

        if requirements.volume_required and not self.can_supply_volume:
            failures.append(f"{self.provider_id}: volume required but not available")

        return (len(failures) == 0, failures)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "can_supply_live_chains": self.can_supply_live_chains,
            "can_supply_delayed_chains": self.can_supply_delayed_chains,
            "can_supply_greeks": self.can_supply_greeks,
            "can_supply_implied_volatility": self.can_supply_implied_volatility,
            "can_supply_open_interest": self.can_supply_open_interest,
            "can_supply_bid_ask": self.can_supply_bid_ask,
            "can_supply_volume": self.can_supply_volume,
            "can_supply_underlying_quote": self.can_supply_underlying_quote,
            "can_supply_multiple_expirations": self.can_supply_multiple_expirations,
            "max_expirations_per_request": self.max_expirations_per_request,
            "typical_delay_seconds": self.typical_delay_seconds,
            "supports_greeks_on_chain": self.supports_greeks_on_chain,
            "notes": self.notes,
        }


# ─── Canonical capability declarations ────────────────────────────────────────

TRADIER_CAPABILITIES = ProviderCapabilities(
    provider_id="tradier",
    can_supply_live_chains=True,
    can_supply_delayed_chains=False,
    can_supply_greeks=True,
    can_supply_implied_volatility=True,
    can_supply_open_interest=True,
    can_supply_bid_ask=True,
    can_supply_volume=True,
    can_supply_underlying_quote=True,
    can_supply_multiple_expirations=True,
    max_expirations_per_request=None,
    typical_delay_seconds=0,
    supports_greeks_on_chain=True,
    notes="Live data via Tradier brokerage API; requires TRADIER_ACCESS_TOKEN",
)

MARKETDATA_CAPABILITIES = ProviderCapabilities(
    provider_id="marketdata",
    can_supply_live_chains=True,
    can_supply_delayed_chains=True,
    can_supply_greeks=True,
    can_supply_implied_volatility=True,
    can_supply_open_interest=True,
    can_supply_bid_ask=True,
    can_supply_volume=True,
    can_supply_underlying_quote=True,
    can_supply_multiple_expirations=True,
    max_expirations_per_request=None,
    typical_delay_seconds=0,
    supports_greeks_on_chain=True,
    notes="Live data via MarketData.app API; requires MARKETDATA_KEY",
)

LAST_KNOWN_GOOD_CAPABILITIES = ProviderCapabilities(
    provider_id="last_known_good_cache",
    can_supply_live_chains=False,
    can_supply_delayed_chains=False,
    can_supply_greeks=True,
    can_supply_implied_volatility=True,
    can_supply_open_interest=True,
    can_supply_bid_ask=True,
    can_supply_volume=True,
    can_supply_underlying_quote=False,
    can_supply_multiple_expirations=True,
    max_expirations_per_request=None,
    typical_delay_seconds=0,
    supports_greeks_on_chain=True,
    notes="Stale cache; reference only — permits analysis but never new entries",
)

PROVIDER_CAPABILITIES_REGISTRY: dict[str, ProviderCapabilities] = {
    "tradier": TRADIER_CAPABILITIES,
    "marketdata": MARKETDATA_CAPABILITIES,
    "last_known_good_cache": LAST_KNOWN_GOOD_CAPABILITIES,
}
