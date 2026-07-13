"""
Sprint 28 — Epic D: Chain Accuracy Audit Service

Validates options chain data through the full pipeline:
Provider → Normalization → Cache → Strategy → API → Frontend

Audits detect:
- Zero-bid legs (no market for this strike/expiry)
- Bid > Ask inversions (stale or bad data)
- Implausible IV values (< 0.01 or > 20.0)
- Missing Greeks from a provider that normally supplies them
- Extreme bid-ask spread percentages (> 50% of mid)
- Open interest / volume anomalies (zero OI on all strikes)

Design: purely read-only; does not call providers or mutate data.
"""

from __future__ import annotations

from typing import Any

from app.models.data_provenance import (
    PROVENANCE_SCHEMA_VERSION,
    ChainDataProvenance,
)

# Audit thresholds
MAX_BID_ASK_SPREAD_PCT = 0.50   # 50% of mid — flag as anomalous
MIN_IV = 0.01
MAX_IV = 20.0
MIN_OPEN_INTEREST_FOR_PASS = 1


class ChainAuditResult:
    """Result of auditing one options chain snapshot."""

    __slots__ = (
        "ticker",
        "provider",
        "retrieved_at",
        "total_legs",
        "zero_bid_count",
        "bid_ask_inversion_count",
        "iv_anomaly_count",
        "spread_anomaly_count",
        "zero_oi_legs",
        "missing_greek_legs",
        "audit_passed",
        "audit_warnings",
        "audit_errors",
        "chain_provenance",
        "schema_version",
    )

    def __init__(self, ticker: str, provider: str, retrieved_at: str | None = None):
        self.ticker = ticker
        self.provider = provider
        self.retrieved_at = retrieved_at
        self.total_legs = 0
        self.zero_bid_count = 0
        self.bid_ask_inversion_count = 0
        self.iv_anomaly_count = 0
        self.spread_anomaly_count = 0
        self.zero_oi_legs = 0
        self.missing_greek_legs = 0
        self.audit_passed = True
        self.audit_warnings: list[str] = []
        self.audit_errors: list[str] = []
        self.chain_provenance: ChainDataProvenance | None = None
        self.schema_version = PROVENANCE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "provider": self.provider,
            "retrieved_at": self.retrieved_at,
            "total_legs": self.total_legs,
            "zero_bid_count": self.zero_bid_count,
            "bid_ask_inversion_count": self.bid_ask_inversion_count,
            "iv_anomaly_count": self.iv_anomaly_count,
            "spread_anomaly_count": self.spread_anomaly_count,
            "zero_oi_legs": self.zero_oi_legs,
            "missing_greek_legs": self.missing_greek_legs,
            "audit_passed": self.audit_passed,
            "audit_warnings": self.audit_warnings,
            "audit_errors": self.audit_errors,
            "chain_provenance": self.chain_provenance.to_dict() if self.chain_provenance else None,
            "schema_version": self.schema_version,
        }

    def add_warning(self, msg: str) -> None:
        self.audit_warnings.append(msg)

    def add_error(self, msg: str) -> None:
        self.audit_errors.append(msg)
        self.audit_passed = False


def audit_chain_legs(
    legs: list[dict[str, Any]],
    ticker: str,
    provider: str,
    retrieved_at: str | None = None,
    expect_greeks: bool = True,
) -> ChainAuditResult:
    """Audit a flat list of options legs for data quality issues.

    Parameters
    ----------
    legs : list[dict]
        Each dict is one option leg with bid, ask, iv, delta, etc.
    ticker : str
        Underlying symbol for context in error messages.
    provider : str
        Data provider slug.
    retrieved_at : str | None
        ISO timestamp of chain fetch.
    expect_greeks : bool
        If True, flag legs with no delta as missing_greek_legs.
    """
    result = ChainAuditResult(ticker, provider, retrieved_at)
    result.total_legs = len(legs)

    if not legs:
        result.add_error(f"Empty chain — no legs returned by {provider}.")
        _attach_provenance(result, retrieved_at, provider, "empty")
        return result

    for i, leg in enumerate(legs):
        bid = _f(leg.get("bid"))
        ask = _f(leg.get("ask"))
        iv = _f(leg.get("iv") or leg.get("implied_volatility"))
        oi = _f(leg.get("open_interest"))
        delta = leg.get("delta")

        if bid is not None and bid == 0.0:
            result.zero_bid_count += 1

        if bid is not None and ask is not None and bid > ask + 0.001:
            result.bid_ask_inversion_count += 1

        if iv is not None:
            if iv < MIN_IV or iv > MAX_IV:
                result.iv_anomaly_count += 1

        if bid is not None and ask is not None and bid > 0 and ask > 0:
            mid = (bid + ask) / 2
            spread = ask - bid
            if mid > 0 and spread / mid > MAX_BID_ASK_SPREAD_PCT:
                result.spread_anomaly_count += 1

        if oi is not None and oi < MIN_OPEN_INTEREST_FOR_PASS:
            result.zero_oi_legs += 1

        if expect_greeks and delta is None:
            result.missing_greek_legs += 1

    # Warn thresholds
    zero_bid_pct = result.zero_bid_count / result.total_legs if result.total_legs else 0
    if zero_bid_pct > 0.5:
        result.add_warning(f"{result.zero_bid_count}/{result.total_legs} legs have zero bid — chain may be illiquid.")

    if result.bid_ask_inversion_count > 0:
        result.add_error(
            f"{result.bid_ask_inversion_count} bid>ask inversions detected — chain data may be stale."
        )

    iv_anom_pct = result.iv_anomaly_count / result.total_legs if result.total_legs else 0
    if iv_anom_pct > 0.1:
        result.add_warning(
            f"{result.iv_anomaly_count} legs have implausible IV (outside {MIN_IV}–{MAX_IV}). "
            f"Check normalization pipeline."
        )

    spread_pct = result.spread_anomaly_count / result.total_legs if result.total_legs else 0
    if spread_pct > 0.5:
        result.add_warning(
            f"{result.spread_anomaly_count}/{result.total_legs} legs have wide bid-ask spread (>50% of mid)."
        )

    oi_pct = result.zero_oi_legs / result.total_legs if result.total_legs else 0
    if oi_pct > 0.8:
        result.add_warning(
            f"{result.zero_oi_legs}/{result.total_legs} legs have zero open interest — consider skipping."
        )

    completeness = "complete" if not result.audit_errors else "partial"
    _attach_provenance(result, retrieved_at, provider, completeness)
    return result


def audit_two_leg_spread(
    front_leg: dict[str, Any] | None,
    back_leg: dict[str, Any] | None,
    ticker: str,
    provider: str,
    retrieved_at: str | None = None,
) -> ChainAuditResult:
    """Audit a two-leg calendar spread for ASA's specific use case."""
    legs = [l for l in [front_leg, back_leg] if l is not None]
    result = audit_chain_legs(legs, ticker, provider, retrieved_at, expect_greeks=True)

    # Calendar-specific: check that expirations differ
    if front_leg and back_leg:
        front_exp = front_leg.get("expiration_date") or front_leg.get("expiration")
        back_exp = back_leg.get("expiration_date") or back_leg.get("expiration")
        if front_exp and back_exp and front_exp == back_exp:
            result.add_error("Front and back legs have the same expiration — not a valid calendar spread.")

        # IV ordering: front should be higher for a positive forward factor
        front_iv = _f(front_leg.get("iv") or front_leg.get("implied_volatility"))
        back_iv = _f(back_leg.get("iv") or back_leg.get("implied_volatility"))
        if front_iv is not None and back_iv is not None and front_iv < back_iv:
            result.add_warning(
                f"Front IV ({front_iv:.3f}) < Back IV ({back_iv:.3f}): forward factor will be negative."
            )

    return result


def build_chain_audit_summary(audit_results: list[ChainAuditResult]) -> dict[str, Any]:
    """Aggregate multiple chain audit results into a pipeline health summary."""
    total = len(audit_results)
    passed = sum(1 for r in audit_results if r.audit_passed)
    failed = total - passed
    all_warnings = [w for r in audit_results for w in r.audit_warnings]
    all_errors = [e for r in audit_results for e in r.audit_errors]
    return {
        "total_chains_audited": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": passed / total if total else 0.0,
        "warning_count": len(all_warnings),
        "error_count": len(all_errors),
        "sample_warnings": all_warnings[:5],
        "sample_errors": all_errors[:5],
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "provider_calls_triggered": False,
        "read_only": True,
    }


def _attach_provenance(
    result: ChainAuditResult,
    retrieved_at: str | None,
    provider: str,
    completeness: str,
) -> None:
    result.chain_provenance = ChainDataProvenance(
        provider=provider,
        retrieved_at=retrieved_at,
        completeness=completeness,
        bid_ask_spread_anomalies=result.spread_anomaly_count,
        zero_bid_legs=result.zero_bid_count,
        total_legs_checked=result.total_legs,
    )


def _f(val: Any) -> float | None:
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None
