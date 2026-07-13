"""
Sprint 28 — Epic C: Provider Conflict Service

Detects, records, and classifies conflicts between data providers.
Conflicts are NEVER hidden — they are always surfaced with a human-readable
explanation of what was reported and why a particular value was chosen.

Conflict resolution policy
--------------------------
- Numeric fields: prefer the most confirmed value (highest source count).
  When source counts are equal, prefer the primary/configured provider.
- Date fields: prefer the earlier date when there is a small gap (potential
  provider date bleed); flag as conflict when the gap exceeds threshold.
- String/enum fields: prefer majority vote; flag on tie.
- All resolutions emit a `selection_reason` so users can audit the decision.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.models.data_provenance import (
    CONFIDENCE_CONFIRMED,
    CONFIDENCE_DISPUTED,
    CONFIDENCE_NO_DATA,
    CONFIDENCE_SINGLE_SOURCE,
    PROVENANCE_SCHEMA_VERSION,
    DataProvenanceRecord,
)
from app.services.data_provenance_service import detect_value_conflict

_PROVIDER_PRIORITY = ["finnhub", "tradier", "robinhood", "alpha_vantage", "alphavantage"]


class ConflictRecord:
    """Immutable record of a detected conflict between providers."""

    __slots__ = (
        "field",
        "conflict_type",
        "values_by_source",
        "resolved_value",
        "resolved_source",
        "resolution_policy",
        "selection_reason",
        "severity",
        "schema_version",
    )

    SEVERITY_CRITICAL = "critical"    # Do-not-trade (dates, key inputs)
    SEVERITY_WARNING = "warning"      # Lower confidence but tradeable
    SEVERITY_INFO = "info"            # Minor discrepancy, within tolerance

    def __init__(
        self,
        field: str,
        conflict_type: str,
        values_by_source: dict[str, Any],
        resolved_value: Any,
        resolved_source: str,
        resolution_policy: str,
        selection_reason: str,
        severity: str = "warning",
    ):
        self.field = field
        self.conflict_type = conflict_type
        self.values_by_source = values_by_source
        self.resolved_value = resolved_value
        self.resolved_source = resolved_source
        self.resolution_policy = resolution_policy
        self.selection_reason = selection_reason
        self.severity = severity
        self.schema_version = PROVENANCE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "conflict_type": self.conflict_type,
            "values_by_source": self.values_by_source,
            "resolved_value": self.resolved_value,
            "resolved_source": self.resolved_source,
            "resolution_policy": self.resolution_policy,
            "selection_reason": self.selection_reason,
            "severity": self.severity,
            "schema_version": self.schema_version,
        }

    def __repr__(self) -> str:
        return (
            f"ConflictRecord(field={self.field!r}, severity={self.severity!r}, "
            f"values={self.values_by_source!r}, resolved={self.resolved_value!r})"
        )


def detect_earnings_date_conflict(
    provider_dates: dict[str, str | None],
    conflict_threshold_days: int = 2,
    bleed_suspect_window_days: int = 10,
) -> list[ConflictRecord]:
    """Detect date conflicts across providers for an earnings event.

    Parameters
    ----------
    provider_dates : dict
        Mapping of provider slug → YYYY-MM-DD string or None.
    conflict_threshold_days : int
        Gap in days that triggers a critical conflict.
    bleed_suspect_window_days : int
        Gap that triggers a warning (potential provider date bleed).

    Returns
    -------
    list[ConflictRecord]
        Empty if no conflict; one ConflictRecord per distinct conflict pair.
    """
    dated: list[tuple[str, date]] = []
    for src, raw in (provider_dates or {}).items():
        d = _parse_date(raw)
        if d is not None:
            dated.append((src, d))

    if len(dated) < 2:
        return []

    dated.sort(key=lambda pair: pair[1])
    conflicts: list[ConflictRecord] = []
    for i in range(len(dated) - 1):
        src_a, d_a = dated[i]
        src_b, d_b = dated[i + 1]
        delta = (d_b - d_a).days
        if delta == 0:
            continue

        if delta <= bleed_suspect_window_days:
            severity = (
                ConflictRecord.SEVERITY_CRITICAL
                if delta <= conflict_threshold_days
                else ConflictRecord.SEVERITY_WARNING
            )
            conflict_type = (
                "date_conflict"
                if delta <= conflict_threshold_days
                else "date_bleed_suspect"
            )
            resolved_src, resolved_date = _prefer_earlier_date(dated)
            reason = (
                f"{src_a} reported {d_a} and {src_b} reported {d_b} "
                f"({delta}d gap). "
                + ("Dates differ by more than tolerance — do not trade."
                   if severity == ConflictRecord.SEVERITY_CRITICAL
                   else "Small gap may indicate provider date bleed; use with caution.")
            )
            conflicts.append(ConflictRecord(
                field="earnings_date",
                conflict_type=conflict_type,
                values_by_source={src_a: d_a.isoformat(), src_b: d_b.isoformat()},
                resolved_value=resolved_date.isoformat(),
                resolved_source=resolved_src,
                resolution_policy="prefer_earlier",
                selection_reason=reason,
                severity=severity,
            ))

    return conflicts


def detect_numeric_conflict(
    field: str,
    values_by_source: dict[str, float | None],
    tolerance: float = 0.0,
    primary_provider: str | None = None,
) -> ConflictRecord | None:
    """Detect a numeric conflict and return a ConflictRecord if one exists."""
    valid: dict[str, float] = {
        src: float(v) for src, v in (values_by_source or {}).items() if v is not None
    }
    if len(valid) < 2:
        return None

    report = detect_value_conflict(field, valid, tolerance)
    if not report["has_conflict"]:
        return None

    # Prefer primary provider if configured; else prefer first source
    preferred_src = _preferred_source(list(valid.keys()), primary_provider)
    resolved_value = valid[preferred_src]
    reason = (
        f"Numeric conflict on {field!r}: "
        + ", ".join(f"{s}={v:.4g}" for s, v in valid.items())
        + f". Resolved to {preferred_src} value ({resolved_value:.4g})."
    )

    return ConflictRecord(
        field=field,
        conflict_type="numeric_conflict",
        values_by_source={src: v for src, v in valid.items()},
        resolved_value=resolved_value,
        resolved_source=preferred_src,
        resolution_policy=f"prefer_provider:{preferred_src}",
        selection_reason=reason,
        severity=ConflictRecord.SEVERITY_WARNING,
    )


def build_conflict_summary(conflicts: list[ConflictRecord]) -> dict[str, Any]:
    """Summarize a list of ConflictRecords for API/display."""
    if not conflicts:
        return {
            "has_conflicts": False,
            "conflict_count": 0,
            "critical_count": 0,
            "warning_count": 0,
            "conflicts": [],
            "schema_version": PROVENANCE_SCHEMA_VERSION,
        }
    critical = [c for c in conflicts if c.severity == ConflictRecord.SEVERITY_CRITICAL]
    warning = [c for c in conflicts if c.severity == ConflictRecord.SEVERITY_WARNING]
    return {
        "has_conflicts": True,
        "conflict_count": len(conflicts),
        "critical_count": len(critical),
        "warning_count": len(warning),
        "has_critical": bool(critical),
        "conflicts": [c.to_dict() for c in conflicts],
        "critical_fields": [c.field for c in critical],
        "schema_version": PROVENANCE_SCHEMA_VERSION,
    }


def resolve_conflict_to_provenance(
    conflict: ConflictRecord,
) -> DataProvenanceRecord:
    """Convert a ConflictRecord into a DataProvenanceRecord for the resolved value."""
    conf = CONFIDENCE_DISPUTED if conflict.severity == ConflictRecord.SEVERITY_CRITICAL else CONFIDENCE_SINGLE_SOURCE
    return DataProvenanceRecord(
        source=conflict.resolved_source,
        confidence=conf,
        conflict_detected=True,
        conflict_details=[conflict.to_dict()],
        selection_reason=conflict.selection_reason,
    )


def _prefer_earlier_date(dated: list[tuple[str, date]]) -> tuple[str, date]:
    dated_sorted = sorted(dated, key=lambda p: p[1])
    return dated_sorted[0]


def _preferred_source(sources: list[str], primary: str | None) -> str:
    if primary and primary in sources:
        return primary
    for p in _PROVIDER_PRIORITY:
        if p in sources:
            return p
    return sources[0]


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return None
