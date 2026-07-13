"""
ASA Patch 32A — Canonical Provenance Model

Every data field in ASA can carry a FieldProvenanceRecord that describes:
  - which provider's value was selected and why
  - what every other provider returned (including MISSING / ERROR)
  - a normalised confidence level (HIGH / MEDIUM / LOW / CONFLICT / UNKNOWN)
  - freshness / staleness metadata

Design rules
------------
- All fields are optional with safe defaults; partial provenance > no provenance.
- All fields are serialisable primitives (str, float, bool, list, None).
- No raw provider payloads embedded — only structured metadata.
- Backward-compatible with Sprint 28 (28.A.v1) DataProvenanceRecord; the new
  model lives alongside the old one and is referenced via provenance_refs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

PATCH32A_SCHEMA_VERSION = "32A.v1"

# ─── Confidence levels ─────────────────────────────────────────────────────────
CONFIDENCE_HIGH = "HIGH"       # ≥2 providers agree on date AND session
CONFIDENCE_MEDIUM = "MEDIUM"   # ≥2 agree on date; session differs or one unknown
CONFIDENCE_LOW = "LOW"         # Only 1 provider has data
CONFIDENCE_CONFLICT = "CONFLICT"  # 2+ providers report different dates
CONFIDENCE_UNKNOWN = "UNKNOWN"   # No provider has data

CONFIDENCE_LEVELS = (CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW, CONFIDENCE_CONFLICT, CONFIDENCE_UNKNOWN)

# UI colour map for templates / API clients
CONFIDENCE_COLOR = {
    CONFIDENCE_HIGH: "green",
    CONFIDENCE_MEDIUM: "yellow-green",
    CONFIDENCE_LOW: "orange",
    CONFIDENCE_CONFLICT: "red",
    CONFIDENCE_UNKNOWN: "gray",
}

CONFIDENCE_LABEL = {
    CONFIDENCE_HIGH: "High confidence — multiple providers agree",
    CONFIDENCE_MEDIUM: "Medium confidence — date agrees, session uncertain",
    CONFIDENCE_LOW: "Low confidence — single source only",
    CONFIDENCE_CONFLICT: "Conflict — providers disagree",
    CONFIDENCE_UNKNOWN: "Unknown — no provider returned data",
}

# ─── Provider value status ─────────────────────────────────────────────────────
STATUS_AVAILABLE = "AVAILABLE"           # Provider returned a value
STATUS_MISSING = "MISSING"               # Provider returned nothing / null
STATUS_UNSUPPORTED = "UNSUPPORTED"       # Field not offered by this provider
STATUS_STALE = "STALE"                   # Data exceeds freshness threshold
STATUS_ERROR = "ERROR"                   # Provider returned an error
STATUS_NOT_REQUESTED = "NOT_REQUESTED"   # Provider was not queried this run

PROVIDER_STATUSES = (
    STATUS_AVAILABLE, STATUS_MISSING, STATUS_UNSUPPORTED,
    STATUS_STALE, STATUS_ERROR, STATUS_NOT_REQUESTED,
)

# ─── Source types ──────────────────────────────────────────────────────────────
SOURCE_TYPE_PROVIDER = "PROVIDER"
SOURCE_TYPE_CALCULATED = "CALCULATED"
SOURCE_TYPE_APPROXIMATED = "APPROXIMATED"
SOURCE_TYPE_MISSING = "MISSING"

# ─── Earnings provider priority order ─────────────────────────────────────────
EARNINGS_PROVIDER_PRIORITY = ("robinhood", "finnhub", "alpha_vantage")


@dataclass(slots=True)
class ProviderValueRecord:
    """One provider's result for a single field."""

    provider: str = "unknown"
    value: Any = None
    status: str = STATUS_NOT_REQUESTED
    observed_at: str | None = None
    freshness_timestamp: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    is_selected: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def available(
        cls,
        provider: str,
        value: Any,
        observed_at: str | None = None,
        is_selected: bool = False,
    ) -> "ProviderValueRecord":
        ts = observed_at or _utcnow()
        return cls(
            provider=provider,
            value=value,
            status=STATUS_AVAILABLE,
            observed_at=ts,
            freshness_timestamp=ts,
            is_selected=is_selected,
        )

    @classmethod
    def missing(cls, provider: str, observed_at: str | None = None) -> "ProviderValueRecord":
        return cls(
            provider=provider,
            value=None,
            status=STATUS_MISSING,
            observed_at=observed_at or _utcnow(),
        )

    @classmethod
    def error(
        cls,
        provider: str,
        error_code: str = "",
        error_message: str = "",
        observed_at: str | None = None,
    ) -> "ProviderValueRecord":
        return cls(
            provider=provider,
            value=None,
            status=STATUS_ERROR,
            observed_at=observed_at or _utcnow(),
            error_code=error_code,
            error_message=error_message,
        )

    @classmethod
    def not_requested(cls, provider: str) -> "ProviderValueRecord":
        return cls(provider=provider, status=STATUS_NOT_REQUESTED)

    @classmethod
    def unsupported(cls, provider: str) -> "ProviderValueRecord":
        return cls(provider=provider, status=STATUS_UNSUPPORTED)


@dataclass(slots=True)
class FieldProvenanceRecord:
    """Patch 32A canonical provenance record for one field across all providers.

    This is the primary provenance shape for Patch 32A. It extends the Sprint 28
    DataProvenanceRecord by tracking per-provider responses and using the
    HIGH/MEDIUM/LOW/CONFLICT/UNKNOWN confidence vocabulary.
    """

    field_id: str = ""
    selected_value: Any = None
    selected_provider: str = "unknown"
    selected_source_type: str = SOURCE_TYPE_MISSING
    selected_at: str | None = None
    observed_at: str | None = None
    freshness_timestamp: str | None = None
    confidence_level: str = CONFIDENCE_UNKNOWN
    confidence_reason: str = ""
    selection_reason: str = ""
    is_calculated: bool = False
    is_approximation: bool = False
    calculation_method: str = ""
    provider_values: list[ProviderValueRecord] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    schema_version: str = PATCH32A_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FieldProvenanceRecord":
        if not isinstance(data, dict):
            return cls()
        pv_raw = data.get("provider_values") or []
        provider_values = []
        for pv in pv_raw:
            if isinstance(pv, dict):
                safe = {k: v for k, v in pv.items() if k in ProviderValueRecord.__dataclass_fields__}
                provider_values.append(ProviderValueRecord(**safe))
        safe_main = {
            k: v for k, v in data.items()
            if k in cls.__dataclass_fields__ and k != "provider_values"
        }
        return cls(provider_values=provider_values, **safe_main)

    @property
    def confidence_color(self) -> str:
        return CONFIDENCE_COLOR.get(self.confidence_level, "gray")

    @property
    def confidence_label(self) -> str:
        return CONFIDENCE_LABEL.get(self.confidence_level, self.confidence_level)

    @property
    def has_conflict(self) -> bool:
        return self.confidence_level == CONFIDENCE_CONFLICT or bool(self.conflicts)

    @property
    def provider_count(self) -> int:
        return sum(
            1 for pv in (self.provider_values or [])
            if pv.status == STATUS_AVAILABLE
        )

    def compact(self) -> dict[str, Any]:
        """Minimal representation for API responses and row annotations."""
        out: dict[str, Any] = {
            "field_id": self.field_id,
            "confidence_level": self.confidence_level,
            "confidence_color": self.confidence_color,
            "selected_provider": self.selected_provider,
            "selected_source_type": self.selected_source_type,
            "provider_count": self.provider_count,
            "has_conflict": self.has_conflict,
            "schema_version": self.schema_version,
        }
        if self.selected_value is not None:
            out["selected_value"] = self.selected_value
        if self.observed_at:
            out["observed_at"] = self.observed_at
        if self.confidence_reason:
            out["confidence_reason"] = self.confidence_reason
        if self.is_approximation:
            out["is_approximation"] = True
        return out


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
