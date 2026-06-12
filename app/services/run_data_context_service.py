"""One in-memory shared facts cache per pipeline run."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass
class RunDataContext:
    run_id: str
    mode: str
    created_at: str
    quotes: dict[str, Any] = field(default_factory=dict)
    candles: dict[str, Any] = field(default_factory=dict)
    options_chains: dict[str, Any] = field(default_factory=dict)
    earnings_events: dict[str, Any] = field(default_factory=dict)
    broker_positions: dict[str, Any] = field(default_factory=dict)
    derived_metrics: dict[str, Any] = field(default_factory=dict)
    provider_status: dict[str, Any] = field(default_factory=dict)
    requirements: dict[str, Any] = field(default_factory=dict)
    coverage: dict[str, Any] = field(default_factory=dict)
    fetch_audit: list[dict[str, Any]] = field(default_factory=list)
    strategy_results: dict[str, Any] = field(default_factory=dict)

    def audit(self, ticker: str, data_type: str, source: str, **details: Any) -> None:
        self.fetch_audit.append({"ticker": ticker, "data_type": data_type, "source": source, **details})

    def to_summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "created_at": self.created_at,
            "quotes": len(self.quotes),
            "candles": len(self.candles),
            "options_chains": len(self.options_chains),
            "earnings_events": len(self.earnings_events),
            "derived_metrics": len(self.derived_metrics),
            "requirements": self.requirements,
            "coverage": self.coverage,
            "fetch_audit": list(self.fetch_audit),
        }


def create_run_data_context(mode: str = "prod", run_id: str | None = None) -> RunDataContext:
    return RunDataContext(
        run_id=run_id or uuid4().hex,
        mode="dev" if str(mode).lower() == "dev" else "prod",
        created_at=datetime.now(timezone.utc).isoformat(),
    )
