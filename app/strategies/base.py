"""
app/strategies/base.py — Shared strategy result types.

This is a placeholder foundation for later strategy modules. It is not used by
/run yet, so the current app behavior remains unchanged.
"""

from dataclasses import dataclass, field


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
