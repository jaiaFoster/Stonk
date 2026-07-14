"""
app/models/calendar_evolution_policy.py — Immutable CalendarEvolutionPolicy.

Patch 33A.1: Declarative policy object for earnings-calendar lifecycle timing.
All thresholds come from config (Railway env vars) or explicit user-approved defaults.
The policy validates its own ordering invariants at construction time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

POLICY_VERSION = "33A.1.calendar.v1"


@dataclass(frozen=True)
class CalendarEvolutionPolicy:
    """
    Immutable policy defining all DTE thresholds for earnings-calendar lifecycle.

    Fields are event DTE (days until earnings date), not option DTE.
    Higher DTE = earlier in time (further from the event).

    Policy ordering invariants (validated at construction):
      discovery_end >= build_start >= surface_start >= ideal_entry_max >= ideal_entry_min >= late_entry >= discovery_start >= 0

    Example: discovery_end=35 > build_start=24 > surface_start=14 > ideal_entry_max=12 > ideal_entry_min=6 > late_entry=4 > discovery_start=0
    """
    discovery_start_event_dte: int      # Lower bound: 0 (same-day earnings)
    discovery_end_event_dte: int        # Upper bound: 35 (early-stage discovery)
    build_start_event_dte: int          # Structure building begins: 24
    surface_start_event_dte: int        # API-visible / surfaced: 14
    ideal_entry_min_event_dte: int      # Ideal entry window minimum: 6
    ideal_entry_max_event_dte: int      # Ideal entry window maximum: 12
    late_entry_event_dte: int           # Late entry cutoff: 4
    policy_version: str = POLICY_VERSION
    source_by_field: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        # Ordering: discovery_end >= build_start >= surface_start >= ideal_max >= ideal_min >= late_entry >= discovery_start >= 0
        errors: list[str] = []
        if self.discovery_start_event_dte < 0:
            errors.append(f"discovery_start_event_dte must be >= 0, got {self.discovery_start_event_dte}")
        if self.discovery_end_event_dte < self.build_start_event_dte:
            errors.append(
                f"discovery_end ({self.discovery_end_event_dte}) must be >= build_start ({self.build_start_event_dte})"
            )
        if self.build_start_event_dte < self.surface_start_event_dte:
            errors.append(
                f"build_start ({self.build_start_event_dte}) must be >= surface_start ({self.surface_start_event_dte})"
            )
        if self.surface_start_event_dte < self.ideal_entry_max_event_dte:
            errors.append(
                f"surface_start ({self.surface_start_event_dte}) must be >= ideal_entry_max ({self.ideal_entry_max_event_dte})"
            )
        if self.ideal_entry_max_event_dte < self.ideal_entry_min_event_dte:
            errors.append(
                f"ideal_entry_max ({self.ideal_entry_max_event_dte}) must be >= ideal_entry_min ({self.ideal_entry_min_event_dte})"
            )
        if self.ideal_entry_min_event_dte < self.late_entry_event_dte:
            errors.append(
                f"ideal_entry_min ({self.ideal_entry_min_event_dte}) must be >= late_entry ({self.late_entry_event_dte})"
            )
        if self.late_entry_event_dte < self.discovery_start_event_dte:
            errors.append(
                f"late_entry ({self.late_entry_event_dte}) must be >= discovery_start ({self.discovery_start_event_dte})"
            )
        if self.late_entry_event_dte < 0:
            errors.append(f"late_entry_event_dte must be >= 0, got {self.late_entry_event_dte}")
        if errors:
            raise ValueError(f"CalendarEvolutionPolicy invariant violations: {'; '.join(errors)}")

    def is_in_discovery_window(self, days_until_event: int) -> bool:
        return self.discovery_start_event_dte <= days_until_event <= self.discovery_end_event_dte

    def is_build_eligible(self, days_until_event: int) -> bool:
        return self.discovery_start_event_dte <= days_until_event <= self.build_start_event_dte

    def is_surface_eligible(self, days_until_event: int) -> bool:
        return self.discovery_start_event_dte <= days_until_event <= self.surface_start_event_dte

    def is_ideal_entry(self, days_until_event: int) -> bool:
        return self.ideal_entry_min_event_dte <= days_until_event <= self.ideal_entry_max_event_dte

    def is_late_entry(self, days_until_event: int) -> bool:
        return self.late_entry_event_dte <= days_until_event < self.ideal_entry_min_event_dte

    def is_entry_allowed(self, days_until_event: int) -> bool:
        """True when within the actual entry window: late_entry <= DTE <= ideal_entry_max."""
        return self.late_entry_event_dte <= days_until_event <= self.ideal_entry_max_event_dte

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "discovery_start_event_dte": self.discovery_start_event_dte,
            "discovery_end_event_dte": self.discovery_end_event_dte,
            "build_start_event_dte": self.build_start_event_dte,
            "surface_start_event_dte": self.surface_start_event_dte,
            "ideal_entry_min_event_dte": self.ideal_entry_min_event_dte,
            "ideal_entry_max_event_dte": self.ideal_entry_max_event_dte,
            "late_entry_event_dte": self.late_entry_event_dte,
            "source_by_field": dict(self.source_by_field),
        }


def load_calendar_evolution_policy() -> CalendarEvolutionPolicy:
    """
    Build the CalendarEvolutionPolicy from config (Railway env vars → approved defaults).

    Policy source hierarchy: Railway env vars > approved code defaults.
    Threshold changes require explicit user approval (see AGENTS.md governance rule).
    """
    from app import config

    def _src(key: str, default_val: int) -> tuple[int, str]:
        env_val = getattr(config, key, None)
        if key in os.environ:
            return int(env_val), f"railway_env:{key}"
        return int(env_val) if env_val is not None else default_val, f"approved_default:{key}"

    discovery_start, src_ds = _src("EARNINGS_DISCOVERY_START_DAYS", 0)
    discovery_end, src_de = _src("EARNINGS_DISCOVERY_END_DAYS", 35)
    build_start, src_bs = _src("CALENDAR_STRUCTURE_BUILD_START_EVENT_DTE", 24)
    surface_start, src_ss = _src("CALENDAR_SURFACE_START_EVENT_DTE", 14)
    ideal_min, src_im = _src("EARNINGS_CALENDAR_IDEAL_ENTRY_MIN_DTE", 6)
    ideal_max, src_ix = _src("EARNINGS_CALENDAR_IDEAL_ENTRY_MAX_DTE", 12)
    late_entry, src_le = _src("EARNINGS_CALENDAR_LATE_ENTRY_DTE", 4)

    return CalendarEvolutionPolicy(
        discovery_start_event_dte=discovery_start,
        discovery_end_event_dte=discovery_end,
        build_start_event_dte=build_start,
        surface_start_event_dte=surface_start,
        ideal_entry_min_event_dte=ideal_min,
        ideal_entry_max_event_dte=ideal_max,
        late_entry_event_dte=late_entry,
        source_by_field={
            "discovery_start_event_dte": src_ds,
            "discovery_end_event_dte": src_de,
            "build_start_event_dte": src_bs,
            "surface_start_event_dte": src_ss,
            "ideal_entry_min_event_dte": src_im,
            "ideal_entry_max_event_dte": src_ix,
            "late_entry_event_dte": src_le,
        },
    )
