"""
app/services/options_chain_validation_service.py — Chain validation.

Patch 33B: Validates a NormalizedOptionsChain against OptionsDataRequirements.
Called by the gateway after a provider succeeds. Returns (ok, errors, warnings).
"""

from __future__ import annotations

from app.models.market_data_contracts import (
    FreshnessState,
    NormalizedOptionsChain,
    OptionsDataRequirements,
)


def validate_chain(
    chain: NormalizedOptionsChain,
    requirements: OptionsDataRequirements,
) -> tuple[bool, list[str], list[str]]:
    """
    Validate chain against declared requirements.

    Returns (ok, errors, warnings).
    errors = hard failures that block use of this chain.
    warnings = soft notices that callers may log or surface.
    """
    errors: list[str] = list(chain.validation_errors)
    warnings: list[str] = list(chain.validation_warnings)

    if not chain.contracts:
        errors.append("Chain has no contracts")

    if requirements.minimum_contract_count is not None:
        if len(chain.contracts) < requirements.minimum_contract_count:
            errors.append(
                f"Chain has {len(chain.contracts)} contracts; minimum is {requirements.minimum_contract_count}"
            )

    if requirements.live_required and not FreshnessState.is_live(chain.freshness_state):
        errors.append(
            f"Live data required but chain freshness_state is {chain.freshness_state}"
        )

    if requirements.greeks_required:
        contracts_with_delta = [c for c in chain.contracts if c.delta is not None]
        if not contracts_with_delta:
            errors.append("greeks_required but no contract has delta populated")
        elif len(contracts_with_delta) < len(chain.contracts) * 0.5:
            warnings.append(
                f"greeks_required: only {len(contracts_with_delta)}/{len(chain.contracts)} contracts have delta"
            )

    if requirements.implied_volatility_required:
        contracts_with_iv = [c for c in chain.contracts if c.implied_volatility is not None]
        if not contracts_with_iv:
            errors.append("implied_volatility_required but no contract has IV populated")

    if requirements.bid_ask_required:
        contracts_with_ba = [c for c in chain.contracts if c.bid is not None and c.ask is not None]
        if not contracts_with_ba:
            errors.append("bid_ask_required but no contract has bid/ask populated")
        elif len(contracts_with_ba) < len(chain.contracts) * 0.5:
            warnings.append(
                f"bid_ask_required: only {len(contracts_with_ba)}/{len(chain.contracts)} contracts have bid/ask"
            )

    if requirements.open_interest_required:
        contracts_with_oi = [c for c in chain.contracts if c.open_interest is not None]
        if not contracts_with_oi:
            errors.append("open_interest_required but no contract has open_interest populated")

    if requirements.volume_required:
        contracts_with_vol = [c for c in chain.contracts if c.volume is not None]
        if not contracts_with_vol:
            errors.append("volume_required but no contract has volume populated")

    # Freshness completeness check
    if chain.freshness_state == FreshnessState.INCOMPLETE:
        warnings.append("Chain freshness_state is INCOMPLETE — some required fields missing")

    ok = len(errors) == 0
    return ok, errors, warnings
