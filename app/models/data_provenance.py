"""
Sprint 28 — Epic A: Universal Provenance Model

Every data value in ASA can carry a DataProvenanceRecord describing where it came
from, when it was fetched, how confident we are, what alternatives were seen, and
whether an approximation or conflict was involved.  The model is intentionally
lightweight: a plain dict in wire format, a dataclass for in-process building.

Design principles
-----------------
- Every field is optional with a safe default so partial provenance is better
  than no provenance.
- All fields are serializable primitives (str, float, bool, list, None).
- The model does NOT embed raw payloads — only structured metadata.
- Source names use the canonical provider slug (finnhub, tradier, robinhood,
  alpha_vantage, calculated, user_override, cache, unknown).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

# Canonical source slugs
SOURCE_FINNHUB = "finnhub"
SOURCE_TRADIER = "tradier"
SOURCE_ROBINHOOD = "robinhood"
SOURCE_ALPHA_VANTAGE = "alpha_vantage"
SOURCE_CALCULATED = "calculated"
SOURCE_USER_OVERRIDE = "user_override"
SOURCE_CACHE = "cache"
SOURCE_UNKNOWN = "unknown"

# Confidence tiers
CONFIDENCE_CONFIRMED = "confirmed"       # ≥2 independent sources agree
CONFIDENCE_SINGLE_SOURCE = "single_source"  # Only one source, unverified
CONFIDENCE_ESTIMATED = "estimated"      # Calculated/interpolated
CONFIDENCE_DISPUTED = "disputed"        # Sources disagree
CONFIDENCE_NO_DATA = "no_data"         # No data available

# Calculation method slugs
CALC_DIRECT = "direct"                  # Taken verbatim from provider
CALC_INTERPOLATED = "interpolated"     # Interpolated between data points
CALC_DERIVED = "derived"               # Derived from other fields
CALC_BLACK_SCHOLES = "black_scholes"
CALC_BINOMIAL = "binomial"
CALC_PROVIDER_NATIVE = "provider_native"  # Provider calculated it, method unknown
CALC_UNKNOWN = "unknown"

PROVENANCE_SCHEMA_VERSION = "28.A.v1"


@dataclass(slots=True)
class DataProvenanceRecord:
    """Provenance annotation for a single field or data object.

    Fields
    ------
    source : str
        Canonical provider slug for the primary value.
    retrieved_at : str | None
        ISO-8601 UTC timestamp when data was fetched from the source.
    confidence : str
        One of CONFIDENCE_* constants.
    calculation_method : str
        One of CALC_* constants.
    approximation : bool
        True when the value is an approximation rather than exact.
    conflict_detected : bool
        True when multiple sources returned different values.
    conflict_details : list[dict]
        One entry per conflicting source: {"source": ..., "value": ..., "date": ...}
    alternatives : list[dict]
        Other values seen: {"source": ..., "value": ..., "confidence": ...}
    selection_reason : str
        Why this value was chosen over alternatives.
    schema_version : str
        Provenance schema version for forward compatibility.
    """

    source: str = SOURCE_UNKNOWN
    retrieved_at: str | None = None
    confidence: str = CONFIDENCE_NO_DATA
    calculation_method: str = CALC_DIRECT
    approximation: bool = False
    conflict_detected: bool = False
    conflict_details: list[dict[str, Any]] = field(default_factory=list)
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    selection_reason: str = ""
    schema_version: str = PROVENANCE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DataProvenanceRecord":
        safe = {k: v for k, v in (data or {}).items() if k in cls.__dataclass_fields__}
        return cls(**safe)

    @classmethod
    def single_source(
        cls,
        source: str,
        retrieved_at: str | None = None,
        calculation_method: str = CALC_DIRECT,
        approximation: bool = False,
        selection_reason: str = "",
    ) -> "DataProvenanceRecord":
        return cls(
            source=source,
            retrieved_at=retrieved_at or _utcnow(),
            confidence=CONFIDENCE_SINGLE_SOURCE,
            calculation_method=calculation_method,
            approximation=approximation,
            selection_reason=selection_reason or f"Single source: {source}",
        )

    @classmethod
    def multi_source_confirmed(
        cls,
        primary_source: str,
        all_sources: list[str],
        retrieved_at: str | None = None,
        selection_reason: str = "",
    ) -> "DataProvenanceRecord":
        others = [s for s in all_sources if s != primary_source]
        alternatives = [{"source": s, "value": None, "confidence": CONFIDENCE_SINGLE_SOURCE} for s in others]
        return cls(
            source=primary_source,
            retrieved_at=retrieved_at or _utcnow(),
            confidence=CONFIDENCE_CONFIRMED,
            calculation_method=CALC_DIRECT,
            alternatives=alternatives,
            selection_reason=selection_reason or f"Confirmed by {len(all_sources)} sources: {', '.join(all_sources)}",
        )

    @classmethod
    def disputed(
        cls,
        conflict_details: list[dict[str, Any]],
        retrieved_at: str | None = None,
    ) -> "DataProvenanceRecord":
        return cls(
            source=SOURCE_UNKNOWN,
            retrieved_at=retrieved_at or _utcnow(),
            confidence=CONFIDENCE_DISPUTED,
            conflict_detected=True,
            conflict_details=conflict_details,
            selection_reason="Sources disagree; value not trusted.",
        )

    @classmethod
    def calculated(
        cls,
        method: str = CALC_DERIVED,
        source_fields: list[str] | None = None,
        approximation: bool = False,
    ) -> "DataProvenanceRecord":
        reason = f"Calculated via {method}"
        if source_fields:
            reason += f" from {', '.join(source_fields)}"
        return cls(
            source=SOURCE_CALCULATED,
            retrieved_at=_utcnow(),
            confidence=CONFIDENCE_SINGLE_SOURCE,
            calculation_method=method,
            approximation=approximation,
            selection_reason=reason,
        )

    @classmethod
    def unavailable(cls, reason: str = "") -> "DataProvenanceRecord":
        return cls(
            source=SOURCE_UNKNOWN,
            confidence=CONFIDENCE_NO_DATA,
            selection_reason=reason or "Data not available.",
        )


@dataclass(slots=True)
class EarningsProvenance:
    """Provenance record specialized for earnings date/session data.

    Tracks per-provider data seen, the merge outcome, and conflict details
    so the UI can show users exactly what each provider reported.
    """

    date_provenance: DataProvenanceRecord = field(default_factory=DataProvenanceRecord)
    session_provenance: DataProvenanceRecord = field(default_factory=DataProvenanceRecord)
    # Per-provider raw data: {"finnhub": {"date": ..., "session": ..., "confirmed": ...}, ...}
    provider_detail: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Conflict summary for display
    conflict_summary: str = ""
    date_agreement: bool = False
    session_agreement: bool = False
    sources_checked: list[str] = field(default_factory=list)
    sources_returned_data: list[str] = field(default_factory=list)
    sources_failed: list[str] = field(default_factory=list)
    schema_version: str = PROVENANCE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


@dataclass(slots=True)
class GreekProvenance:
    """Provenance record for options Greeks."""

    delta_source: str = SOURCE_UNKNOWN
    gamma_source: str = SOURCE_UNKNOWN
    theta_source: str = SOURCE_UNKNOWN
    vega_source: str = SOURCE_UNKNOWN
    rho_source: str = SOURCE_UNKNOWN
    iv_source: str = SOURCE_UNKNOWN
    calculation_method: str = CALC_UNKNOWN
    retrieved_at: str | None = None
    approximation: bool = False
    model_notes: str = ""
    schema_version: str = PROVENANCE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ChainDataProvenance:
    """Provenance record for an options chain snapshot."""

    provider: str = SOURCE_UNKNOWN
    retrieved_at: str | None = None
    cache_hit: bool = False
    cache_age_seconds: int | None = None
    completeness: str = "unknown"  # complete, partial, empty
    missing_expirations: list[str] = field(default_factory=list)
    bid_ask_spread_anomalies: int = 0
    zero_bid_legs: int = 0
    total_legs_checked: int = 0
    schema_version: str = PROVENANCE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
