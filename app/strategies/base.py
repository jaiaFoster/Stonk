"""
app/strategies/base.py — Shared strategy result types.

Strategy modules should return transparent, report-friendly outputs. The app is
not trying to hide a black-box model; every score should expose the reasons,
risks, and known data limitations behind it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class StrategyResult:
    name: str
    ticker: str
    action: str
    score: float | None = None
    confidence: str | None = None
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    next_check: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
