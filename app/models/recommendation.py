"""
app/models/recommendation.py — Normalized advisor recommendation model.

The recommendation model is strategy-agnostic. It can carry current position
info, score breakdowns, reasons/risks, and optional market metrics used by the
strategy.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class Recommendation:
    ticker: str
    account: str
    strategy: str
    action: str
    score: float
    confidence: str
    allocation_pct: float | None = None
    position_value: float | None = None
    gain_loss_pct: float | None = None
    score_breakdown: dict[str, float] = field(default_factory=dict)
    market_metrics: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    next_check: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON/report friendly dictionary."""
        data = asdict(self)
        data["score"] = round(float(self.score), 1)
        if self.allocation_pct is not None:
            data["allocation_pct"] = round(float(self.allocation_pct), 2)
        if self.position_value is not None:
            data["position_value"] = round(float(self.position_value), 2)
        if self.gain_loss_pct is not None:
            data["gain_loss_pct"] = round(float(self.gain_loss_pct), 2)
        data["score_breakdown"] = {
            key: round(float(value), 1) for key, value in self.score_breakdown.items()
        }
        data["market_metrics"] = dict(self.market_metrics or {})
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Recommendation":
        """Build a Recommendation from a dictionary-like object."""
        return cls(
            ticker=str(data.get("ticker", "UNKNOWN")),
            account=str(data.get("account", "Unknown")),
            strategy=str(data.get("strategy", "Unknown Strategy")),
            action=str(data.get("action", "WATCH")),
            score=float(data.get("score", 0.0) or 0.0),
            confidence=str(data.get("confidence", "Low")),
            allocation_pct=(
                float(data["allocation_pct"])
                if data.get("allocation_pct") is not None
                else None
            ),
            position_value=(
                float(data["position_value"])
                if data.get("position_value") is not None
                else None
            ),
            gain_loss_pct=(
                float(data["gain_loss_pct"])
                if data.get("gain_loss_pct") is not None
                else None
            ),
            score_breakdown=dict(data.get("score_breakdown", {}) or {}),
            market_metrics=dict(data.get("market_metrics", {}) or {}),
            reasons=list(data.get("reasons", []) or []),
            risks=list(data.get("risks", []) or []),
            next_check=(str(data["next_check"]) if data.get("next_check") else None),
        )
