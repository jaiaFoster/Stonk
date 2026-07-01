"""Canonical strategy output schema for ASA."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class StrategyLeg:
    """One leg of a multi-leg options structure."""

    leg_id: str
    position: str
    option_type: str
    strike: float | None
    expiration: str | None
    dte: int | None
    bid: float | None
    ask: float | None
    mid: float | None
    iv: float | None
    delta: float | None
    open_interest: int | None
    volume: int | None
    current_price: float | None
    average_price: float | None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> StrategyLeg:
        return cls(**{name: value.get(name) for name in cls.__dataclass_fields__})


@dataclass
class StrategyPricing:
    """Provider-independent package pricing."""

    mid_debit: float | None = None
    conservative_debit: float | None = None
    net_credit: float | None = None
    slippage_pct: float | None = None
    pricing_status: str = "unknown"
    diagnostics: dict[str, Any] | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StrategyRisk:
    """Provider-independent structure risk facts."""

    max_risk: float | None = None
    max_reward: float | None = None
    account_risk_pct: float | None = None
    risk_status: str = "unknown"
    diagnostics: dict[str, Any] | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StrategyStructure:
    """Canonical option structure built from normalized chain data."""

    structure_type: str
    ticker: str
    legs: list[StrategyLeg]
    status: str = "BUILT"
    reason_code: str | None = None
    reason_label: str | None = None
    pricing: StrategyPricing | None = None
    risk: StrategyRisk | None = None
    diagnostics: dict[str, Any] | None = None

    def to_dict(self) -> dict:
        return {
            "structure_type": self.structure_type,
            "ticker": self.ticker,
            "legs": [leg.to_dict() for leg in self.legs],
            "status": self.status,
            "reason_code": self.reason_code,
            "reason_label": self.reason_label,
            "pricing": self.pricing.to_dict() if self.pricing else None,
            "risk": self.risk.to_dict() if self.risk else None,
            "diagnostics": self.diagnostics,
        }


@dataclass
class StrategyGate:
    """One gate or requirement in the strategy evaluation pipeline."""

    name: str
    status: str
    detail: str
    is_hard_block: bool = False
    value: Any = None
    gate_id: str | None = None
    reason_code: str | None = None
    reason_label: str | None = None
    blockers: list[str] | None = None
    warnings: list[str] | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StrategyDataLineage:
    """Sources and confidence supporting an opportunity."""

    earnings_date: str | None
    earnings_date_confidence: str
    earnings_date_sources: list[str]
    earnings_date_conflict: bool
    conflicting_dates: list[str]
    source_call_log: dict[str, dict]
    iv_source: str | None
    price_source: str | None
    volume_source: str | None
    data_as_of: str | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StrategyPipelineTrace:
    """Stage-by-stage trace for a single candidate."""

    stages: dict[str, str]
    stage_details: dict[str, str]
    removed_at_stage: str | None
    removal_reason: str | None
    prescreen_stats: dict | None

    def record(self, stage: str, result: str, detail: str = "") -> None:
        self.stages[stage] = result
        if detail:
            self.stage_details[stage] = detail
        if result in ("REMOVED", "FAIL_TERMINAL") and not self.removed_at_stage:
            self.removed_at_stage = stage

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExpirationPair:
    """Canonical expiration pair for a calendar spread."""

    front_expiration: str
    back_expiration: str
    front_dte: int
    back_dte: int
    earnings_date: str | None
    days_to_earnings: int | None
    front_before_earnings: bool
    gap_days: int | None
    is_near_miss: bool
    selection_method: str

    @property
    def is_valid(self) -> bool:
        return self.front_before_earnings and (self.gap_days or 0) >= 1

    def to_dict(self) -> dict:
        return {**asdict(self), "is_valid": self.is_valid}

    @classmethod
    def from_dict(cls, value: dict) -> ExpirationPair:
        return cls(
            front_expiration=value.get("front_expiration", ""),
            back_expiration=value.get("back_expiration", ""),
            front_dte=value.get("front_dte", 0),
            back_dte=value.get("back_dte", 0),
            earnings_date=value.get("earnings_date"),
            days_to_earnings=value.get("days_to_earnings"),
            front_before_earnings=bool(value.get("front_before_earnings")),
            gap_days=value.get("gap_days"),
            is_near_miss=bool(value.get("is_near_miss")),
            selection_method=value.get("selection_method", "unknown"),
        )


@dataclass
class StrategyOpportunity:
    """Canonical, fully serializable output object for ASA strategy signals."""

    strategy_id: str
    strategy_version: str
    ticker: str
    run_id: str | None
    verdict: str
    verdict_tier: int
    score: float | None
    actionability_score: float | None
    reason_code: str | None
    reason_label: str | None
    blockers: list[str]
    warnings: list[str]
    structure_type: str | None
    legs: list[StrategyLeg]
    expiration_pair: ExpirationPair | None
    debit: float | None
    credit: float | None
    max_risk: float | None
    max_reward: float | None
    slippage_pct: float | None
    edge_on_margin: float | None
    iv_percentile: float | None
    iv_edge: float | None
    liquidity_status: str | None
    bid_ask_spread_pct: float | None
    open_interest: int | None
    source_mode: str
    can_trade_live: bool
    can_enter_daily_opportunity: bool
    stale_structure: bool | None
    stale_structure_note: str | None
    data_lineage: StrategyDataLineage | None
    pipeline_trace: StrategyPipelineTrace | None
    gates: list[StrategyGate]
    raw: dict
    generated_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "ticker": self.ticker,
            "run_id": self.run_id,
            "verdict": self.verdict,
            "verdict_tier": self.verdict_tier,
            "score": self.score,
            "actionability_score": self.actionability_score,
            "reason_code": self.reason_code,
            "reason_label": self.reason_label,
            "blockers": self.blockers,
            "warnings": self.warnings,
            "structure_type": self.structure_type,
            "legs": [leg.to_dict() for leg in self.legs],
            "expiration_pair": self.expiration_pair.to_dict() if self.expiration_pair else None,
            "debit": self.debit,
            "credit": self.credit,
            "max_risk": self.max_risk,
            "max_reward": self.max_reward,
            "slippage_pct": self.slippage_pct,
            "edge_on_margin": self.edge_on_margin,
            "iv_percentile": self.iv_percentile,
            "iv_edge": self.iv_edge,
            "liquidity_status": self.liquidity_status,
            "bid_ask_spread_pct": self.bid_ask_spread_pct,
            "open_interest": self.open_interest,
            "source_mode": self.source_mode,
            "can_trade_live": self.can_trade_live,
            "can_enter_daily_opportunity": self.can_enter_daily_opportunity,
            "stale_structure": self.stale_structure,
            "stale_structure_note": self.stale_structure_note,
            "data_lineage": self.data_lineage.to_dict() if self.data_lineage else None,
            "pipeline_trace": self.pipeline_trace.to_dict() if self.pipeline_trace else None,
            "gates": [gate.to_dict() for gate in self.gates],
            "raw": self.raw,
            "generated_at": self.generated_at,
        }

    @property
    def is_actionable(self) -> bool:
        return self.verdict_tier >= 80 and self.can_enter_daily_opportunity

    @property
    def is_signal(self) -> bool:
        return self.verdict_tier >= 60
