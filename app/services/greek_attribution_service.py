"""
Sprint 28 — Epic E: Greek Source Attribution Service

Every options Greek exposed in ASA must carry a provenance annotation
describing the source (provider slug), calculation method, and whether
the value is native (from the provider) or derived (calculated by ASA).

Greek attribution policy
------------------------
- If the provider returns Greeks natively, they are flagged as
  `calculation_method="provider_native"`.
- If ASA calculates them from the option price using Black-Scholes
  approximation, they are flagged `calculation_method="black_scholes"`.
- If they are estimated from chain mid-prices, they are `calculation_method="interpolated"`.
- Any Greek value not provided is None with `source="unknown"`.
"""

from __future__ import annotations

from typing import Any

from app.models.data_provenance import (
    CALC_BLACK_SCHOLES,
    CALC_DERIVED,
    CALC_INTERPOLATED,
    CALC_PROVIDER_NATIVE,
    CALC_UNKNOWN,
    CONFIDENCE_SINGLE_SOURCE,
    CONFIDENCE_NO_DATA,
    PROVENANCE_SCHEMA_VERSION,
    SOURCE_CALCULATED,
    SOURCE_UNKNOWN,
    GreekProvenance,
)

# Canonical Greek field names
GREEK_DELTA = "delta"
GREEK_GAMMA = "gamma"
GREEK_THETA = "theta"
GREEK_VEGA = "vega"
GREEK_RHO = "rho"
GREEK_IV = "iv"

ALL_GREEKS = (GREEK_DELTA, GREEK_GAMMA, GREEK_THETA, GREEK_VEGA, GREEK_RHO, GREEK_IV)

# Provider-specific Greek availability (empirical from integration)
PROVIDER_GREEK_AVAILABILITY = {
    "tradier": {
        "delta": CALC_PROVIDER_NATIVE,
        "gamma": CALC_PROVIDER_NATIVE,
        "theta": CALC_PROVIDER_NATIVE,
        "vega": CALC_PROVIDER_NATIVE,
        "rho": CALC_PROVIDER_NATIVE,
        "iv": CALC_PROVIDER_NATIVE,
    },
    "robinhood": {
        "delta": CALC_PROVIDER_NATIVE,
        "gamma": CALC_PROVIDER_NATIVE,
        "theta": CALC_PROVIDER_NATIVE,
        "vega": CALC_PROVIDER_NATIVE,
        "rho": CALC_PROVIDER_NATIVE,
        "iv": CALC_PROVIDER_NATIVE,
    },
    "alpha_vantage": {
        "iv": CALC_PROVIDER_NATIVE,
    },
    "finnhub": {},
}


def build_greek_provenance(
    option_row: dict[str, Any],
    provider: str,
    retrieved_at: str | None = None,
) -> GreekProvenance:
    """Build a GreekProvenance record from an options chain row.

    Inspects which Greeks are present in *option_row* and assigns the
    appropriate source and calculation method based on the provider.
    """
    avail = PROVIDER_GREEK_AVAILABILITY.get(provider, {})

    def _src_for(greek: str) -> str:
        val = option_row.get(greek) or option_row.get(f"greeks_{greek}")
        if val is not None:
            return provider if greek in avail else SOURCE_CALCULATED
        return SOURCE_UNKNOWN

    def _method_for(greek: str) -> str:
        val = option_row.get(greek) or option_row.get(f"greeks_{greek}")
        if val is not None:
            return avail.get(greek, CALC_DERIVED)
        return CALC_UNKNOWN

    return GreekProvenance(
        delta_source=_src_for("delta"),
        gamma_source=_src_for("gamma"),
        theta_source=_src_for("theta"),
        vega_source=_src_for("vega"),
        rho_source=_src_for("rho"),
        iv_source=_src_for("iv"),
        calculation_method=avail.get("delta", CALC_UNKNOWN),
        retrieved_at=retrieved_at,
        approximation=provider not in PROVIDER_GREEK_AVAILABILITY,
        model_notes=f"Greeks from {provider}" if provider in PROVIDER_GREEK_AVAILABILITY else "Greeks source unverified.",
    )


def build_greek_attribution_report(
    option_row: dict[str, Any],
    provider: str,
    retrieved_at: str | None = None,
) -> dict[str, Any]:
    """Return a structured attribution report for all Greeks in one option row."""
    prov = build_greek_provenance(option_row, provider, retrieved_at)
    fields: dict[str, dict[str, Any]] = {}
    for greek in ALL_GREEKS:
        val = option_row.get(greek) or option_row.get(f"greeks_{greek}")
        src_field = f"{greek}_source"
        src = getattr(prov, src_field, SOURCE_UNKNOWN)
        method = prov.calculation_method if src != SOURCE_UNKNOWN else CALC_UNKNOWN
        fields[greek] = {
            "value": val,
            "source": src,
            "calculation_method": method,
            "confidence": CONFIDENCE_SINGLE_SOURCE if val is not None else CONFIDENCE_NO_DATA,
            "available": val is not None,
        }
    return {
        "provider": provider,
        "retrieved_at": retrieved_at,
        "greeks": fields,
        "model_notes": prov.model_notes,
        "approximation": prov.approximation,
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "provider_calls_triggered": False,
        "read_only": True,
    }


def compact_greek_sources(option_row: dict[str, Any], provider: str) -> dict[str, str]:
    """Return a minimal {greek: source} mapping for inline API annotation."""
    avail = PROVIDER_GREEK_AVAILABILITY.get(provider, {})
    out: dict[str, str] = {}
    for greek in ALL_GREEKS:
        val = option_row.get(greek) or option_row.get(f"greeks_{greek}")
        if val is not None:
            out[greek] = provider if greek in avail else SOURCE_CALCULATED
    return out


def enrich_option_row_with_greek_attribution(
    option_row: dict[str, Any],
    provider: str,
    retrieved_at: str | None = None,
) -> dict[str, Any]:
    """Return a copy of *option_row* with `_greek_provenance` attached."""
    row = dict(option_row)
    row["_greek_provenance"] = build_greek_attribution_report(row, provider, retrieved_at)
    return row
