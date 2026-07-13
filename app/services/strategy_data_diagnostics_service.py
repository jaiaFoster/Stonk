"""
Sprint 28 — Epic H: Strategy Data Diagnostics Service

Every strategy evaluation records what data it required, what was present,
what was missing, what confidence it had, and why a row was accepted or
rejected. This information is stored in a `_data_diagnostics` key on each
strategy row and is never used to alter strategy behavior — it is purely
for transparency and debugging.

Usage
-----
diagnostics = StrategyDataDiagnostics(strategy_id="earnings_calendar")
diagnostics.require("earnings_date", ...)
diagnostics.mark_present("earnings_date", source="finnhub", confidence="confirmed")
diagnostics.mark_missing("options_chain", reason="no_tradier_data")
row["_data_diagnostics"] = diagnostics.to_dict()
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models.data_provenance import (
    CONFIDENCE_NO_DATA,
    PROVENANCE_SCHEMA_VERSION,
)

_STATUS_PRESENT = "present"
_STATUS_MISSING = "missing"
_STATUS_PARTIAL = "partial"
_STATUS_STALE = "stale"


class StrategyDataDiagnostics:
    """Accumulates per-evaluation data diagnostics for a single strategy row."""

    def __init__(self, strategy_id: str, ticker: str = ""):
        self.strategy_id = strategy_id
        self.ticker = ticker
        self._fields: dict[str, dict[str, Any]] = {}
        self._rejection_reason: str | None = None
        self._acceptance_reason: str | None = None
        self._overall_confidence: str = CONFIDENCE_NO_DATA
        self._evaluated_at: str = _utcnow()
        self._notes: list[str] = []

    def require(
        self,
        field: str,
        required: bool = True,
        description: str = "",
    ) -> "StrategyDataDiagnostics":
        """Declare that a field is required for evaluation."""
        if field not in self._fields:
            self._fields[field] = {
                "status": _STATUS_MISSING,
                "required": required,
                "description": description,
                "source": None,
                "confidence": CONFIDENCE_NO_DATA,
                "reason": None,
                "stale": False,
            }
        else:
            self._fields[field]["required"] = required
            if description:
                self._fields[field]["description"] = description
        return self

    def mark_present(
        self,
        field: str,
        source: str = "unknown",
        confidence: str = "single_source",
        stale: bool = False,
        note: str = "",
    ) -> "StrategyDataDiagnostics":
        """Mark a field as present in the evaluation data."""
        self._fields.setdefault(field, {})
        self._fields[field].update({
            "status": _STATUS_STALE if stale else _STATUS_PRESENT,
            "source": source,
            "confidence": confidence,
            "stale": stale,
            "reason": note or None,
        })
        return self

    def mark_missing(
        self,
        field: str,
        reason: str = "",
        required: bool = True,
    ) -> "StrategyDataDiagnostics":
        """Mark a field as missing from the evaluation data."""
        self._fields.setdefault(field, {})
        self._fields[field].update({
            "status": _STATUS_MISSING,
            "required": required,
            "reason": reason or "Not available.",
            "source": None,
            "confidence": CONFIDENCE_NO_DATA,
            "stale": False,
        })
        return self

    def mark_partial(
        self,
        field: str,
        available_fraction: float,
        source: str = "unknown",
        reason: str = "",
    ) -> "StrategyDataDiagnostics":
        """Mark a field as partially available (e.g., some expirations missing)."""
        self._fields.setdefault(field, {})
        self._fields[field].update({
            "status": _STATUS_PARTIAL,
            "available_fraction": available_fraction,
            "source": source,
            "reason": reason,
            "confidence": "single_source" if available_fraction > 0.5 else CONFIDENCE_NO_DATA,
            "stale": False,
        })
        return self

    def set_rejection_reason(self, reason: str) -> "StrategyDataDiagnostics":
        self._rejection_reason = reason
        return self

    def set_acceptance_reason(self, reason: str) -> "StrategyDataDiagnostics":
        self._acceptance_reason = reason
        return self

    def set_overall_confidence(self, confidence: str) -> "StrategyDataDiagnostics":
        self._overall_confidence = confidence
        return self

    def add_note(self, note: str) -> "StrategyDataDiagnostics":
        if note:
            self._notes.append(note)
        return self

    def to_dict(self) -> dict[str, Any]:
        required_fields = [f for f, d in self._fields.items() if d.get("required", True)]
        missing_required = [f for f in required_fields if self._fields[f].get("status") in (_STATUS_MISSING,)]
        present_fields = [f for f, d in self._fields.items() if d.get("status") in (_STATUS_PRESENT, _STATUS_STALE, _STATUS_PARTIAL)]
        stale_fields = [f for f, d in self._fields.items() if d.get("stale")]
        return {
            "strategy_id": self.strategy_id,
            "ticker": self.ticker,
            "evaluated_at": self._evaluated_at,
            "overall_confidence": self._overall_confidence,
            "rejection_reason": self._rejection_reason,
            "acceptance_reason": self._acceptance_reason,
            "required_field_count": len(required_fields),
            "present_field_count": len(present_fields),
            "missing_required_fields": missing_required,
            "stale_fields": stale_fields,
            "data_complete": len(missing_required) == 0,
            "fields": self._fields,
            "notes": self._notes,
            "schema_version": PROVENANCE_SCHEMA_VERSION,
        }

    @property
    def data_complete(self) -> bool:
        required = [f for f, d in self._fields.items() if d.get("required", True)]
        return all(self._fields[f].get("status") != _STATUS_MISSING for f in required)

    @property
    def missing_required(self) -> list[str]:
        return [
            f for f, d in self._fields.items()
            if d.get("required", True) and d.get("status") == _STATUS_MISSING
        ]


def attach_data_diagnostics(
    row: dict[str, Any],
    diagnostics: StrategyDataDiagnostics,
) -> None:
    """Attach a diagnostics record to a strategy row in-place."""
    row["_data_diagnostics"] = diagnostics.to_dict()


def get_data_diagnostics(row: dict[str, Any]) -> dict[str, Any] | None:
    return (row or {}).get("_data_diagnostics")


def diagnostics_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate diagnostics across a list of strategy rows."""
    total = len(rows)
    with_diagnostics = [r for r in rows if r.get("_data_diagnostics")]
    data_complete = [r for r in with_diagnostics if (r["_data_diagnostics"] or {}).get("data_complete")]
    missing_any = [r for r in with_diagnostics if not (r["_data_diagnostics"] or {}).get("data_complete")]

    field_miss_counts: dict[str, int] = {}
    for r in with_diagnostics:
        for f in ((r["_data_diagnostics"] or {}).get("missing_required_fields") or []):
            field_miss_counts[f] = field_miss_counts.get(f, 0) + 1

    return {
        "total_rows": total,
        "rows_with_diagnostics": len(with_diagnostics),
        "data_complete_count": len(data_complete),
        "missing_data_count": len(missing_any),
        "most_common_missing_fields": sorted(field_miss_counts.items(), key=lambda x: -x[1])[:10],
        "schema_version": PROVENANCE_SCHEMA_VERSION,
    }


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
