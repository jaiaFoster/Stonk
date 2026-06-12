"""Generic stage counters for expensive strategy scans."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StagedScan:
    strategy_id: str
    stages: dict[str, dict[str, Any]] = field(default_factory=dict)

    def record(self, stage: str, input_count: int, output_count: int, rejection_reasons: dict[str, int] | None = None) -> None:
        self.stages[stage] = {
            "input_count": input_count,
            "output_count": output_count,
            "rejected_count": max(0, input_count - output_count),
            "rejection_reasons": dict(rejection_reasons or {}),
        }

    def summary(self) -> dict[str, Any]:
        return {"strategy_id": self.strategy_id, "stages": self.stages}
