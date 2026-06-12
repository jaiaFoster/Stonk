"""Small normalized records shared by strategies."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


COMPLETE = "COMPLETE"
PARTIAL = "PARTIAL"
MISSING_NOT_REQUESTED = "MISSING_NOT_REQUESTED"
MISSING_PROVIDER_FAILED = "MISSING_PROVIDER_FAILED"
MISSING_UNSUPPORTED = "MISSING_UNSUPPORTED"
SKIPPED_DEV_CAP = "SKIPPED_DEV_CAP"
SKIPPED_PROVIDER_BUDGET = "SKIPPED_PROVIDER_BUDGET"
STALE_CACHE_USED = "STALE_CACHE_USED"
STALE_CACHE_REJECTED = "STALE_CACHE_REJECTED"
LOW_CONFIDENCE = "LOW_CONFIDENCE"


@dataclass(slots=True)
class MarketDataRecord:
    ticker: str
    data_type: str
    payload: Any
    provider: str
    fetched_at: str
    expires_at: str
    state: str = COMPLETE
    confidence: str = "high"
    reason: str = ""
    fresh: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class StrategyDataRequirement:
    strategy_id: str
    tickers: list[str]
    needs_quote: bool = False
    needs_daily_candles: bool = False
    min_daily_bars: int = 240
    needs_options_chain: bool = False
    min_dte: int | None = None
    max_dte: int | None = None
    expirations_per_ticker: int | None = None
    needs_earnings_event: bool = False
    earnings_lookahead_days: int | None = None
    required_derived_metrics: list[str] = field(default_factory=list)
    optional_derived_metrics: list[str] = field(default_factory=list)
    priority: int = 50
    reason: str = ""


@dataclass(slots=True)
class StrategyDisplayMetadata:
    short_label: str
    section_label: str
    nav_label: str
    priority: int = 50
    show_top_summary: bool = True
    show_export: bool = True


@dataclass(slots=True)
class StrategyResult:
    strategy_id: str
    strategy_label: str
    version: str
    enabled: bool
    ran: bool
    rows: list[dict[str, Any]] = field(default_factory=list)
    active_rows: list[dict[str, Any]] = field(default_factory=list)
    pass_count: int = 0
    watch_count: int = 0
    fail_count: int = 0
    skipped_count: int = 0
    scanned_tickers: list[str] = field(default_factory=list)
    data_coverage: dict[str, Any] = field(default_factory=dict)
    provider_notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
