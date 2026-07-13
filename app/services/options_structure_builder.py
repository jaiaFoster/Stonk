"""Universal Options Structure Builder — ASA Patch 33A.

One reusable engine for transforming normalized option-chain data and
strategy structure specifications into candidate structures.

Strategies declare requirements via OptionsStructureSpec; this engine:
  1. Enumerates ALL valid expiration pairs.
  2. Records a disposition for EVERY pair (no silent discards).
  3. Matches legs according to spec (delta-target, ATM, same-strike).
  4. Computes conservative and mid debit.
  5. Checks liquidity.
  6. Returns a StructureBuildResult with structures + rejected pairs.

This engine does not call any provider.  It operates on chain data
that has already been fetched and normalized by the caller.

Patch history
-------------
33A  — Initial implementation: expiration enumeration, pair filtering,
       leg matching, calendar and vertical debit, liquidity check,
       full structured result with audit trail.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from app.models.options_structure_spec import (
    LiquidityRequirements,
    OptionsStructureSpec,
)

# ---------------------------------------------------------------------------
# Pair status constants
# ---------------------------------------------------------------------------


class PairStatus:
    """All possible dispositions for an expiration pair.

    Every pair that is considered by ``enumerate_expiration_pairs`` gets
    exactly one of these tokens as its ``pair_status``.  Pairs that survive
    all filters receive VALID (or VALID_BUT_*) and are handed to leg matching.
    All other pairs are rejected and carry one or more rejection codes.
    """

    VALID = "VALID"
    """Pair meets all DTE and gap requirements."""

    VALID_BUT_LOW_DTE = "VALID_BUT_LOW_DTE"
    """Front DTE is at or just above the hard minimum — usable but flagged."""

    VALID_BUT_WIDE_GAP = "VALID_BUT_WIDE_GAP"
    """Gap is at the outer edge of the allowed range — usable but flagged."""

    PRE_WINDOW = "PRE_WINDOW"
    """Front expiration is too far away (front_dte > front_dte_max)."""

    ENTRY_WINDOW = "ENTRY_WINDOW"
    """Pair is in the ideal entry window (informational, not a rejection)."""

    CLOSING_WINDOW = "CLOSING_WINDOW"
    """Front DTE has dropped below the minimum — too close to expiration."""

    EVENT_SPANNING = "EVENT_SPANNING"
    """Both legs expire before the event when event_must_be_between_legs=True."""

    NO_MATCHING_STRIKE = "NO_MATCHING_STRIKE"
    """A required strike could not be found in one of the chains."""

    MISSING_FRONT_CHAIN = "MISSING_FRONT_CHAIN"
    """No chain data was provided for the front expiration."""

    MISSING_BACK_CHAIN = "MISSING_BACK_CHAIN"
    """No chain data was provided for the back expiration."""

    MISSING_QUOTES = "MISSING_QUOTES"
    """Chain data exists but required bid/ask fields are absent."""

    UNSUPPORTED_EXPIRATION = "UNSUPPORTED_EXPIRATION"
    """Expiration date string could not be parsed."""

    PROVIDER_INCOMPLETE = "PROVIDER_INCOMPLETE"
    """Chain data arrived but appears truncated (e.g. no puts returned)."""

    REJECTED_BY_STRATEGY = "REJECTED_BY_STRATEGY"
    """Pair was excluded by a strategy-specific rule not covered above."""


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ExpirationPairRecord:
    """Audit record for one front/back expiration pair that was considered.

    Every pair that ``enumerate_expiration_pairs`` evaluates gets one of
    these records, regardless of whether it passed or was rejected.  This
    ensures the caller has a complete picture of why the builder did or did
    not attempt to build a structure for a given pair.
    """

    front_expiration: str
    """ISO-format date string for the front (near-term) expiration."""

    back_expiration: str
    """ISO-format date string for the back (far-term) expiration."""

    front_dte: Optional[int]
    """Calendar days from today to front expiration, or None if unparseable."""

    back_dte: Optional[int]
    """Calendar days from today to back expiration, or None if unparseable."""

    expiration_gap_days: Optional[int]
    """back_dte - front_dte, or None when either DTE is unavailable."""

    front_before_event: Optional[bool]
    """True if front expiration precedes the event date.  None if no event."""

    back_after_event: Optional[bool]
    """True if back expiration is after the event date.  None if no event."""

    event_between_legs: Optional[bool]
    """True if the event date falls strictly between the two expirations."""

    front_contracts_available: Optional[int]
    """Number of contracts returned in the front chain, or None if missing."""

    back_contracts_available: Optional[int]
    """Number of contracts returned in the back chain, or None if missing."""

    pair_status: str
    """One of the PairStatus constants — the primary disposition for this pair."""

    pair_rejection_codes: list[str]
    """Detailed rejection reasons when pair_status is not VALID."""

    data_sources: list[str]
    """Provider/cache source labels for the chain data used."""

    data_freshness: Optional[str]
    """ISO timestamp of the newest data point, or None if unknown."""


@dataclass
class StructureLeg:
    """One fully-resolved option contract leg within a BuiltStructure."""

    role: str
    """Semantic role: "front", "back", "long", "short", "near", "far", etc."""

    option_type: str
    """Contract type: "call" or "put"."""

    expiration: str
    """ISO-format expiration date."""

    strike: float
    """Strike price."""

    bid: Optional[float]
    ask: Optional[float]
    mid: Optional[float]

    iv: Optional[float]
    """Implied volatility as a decimal (e.g. 0.45 = 45 %)."""

    delta: Optional[float]
    gamma: Optional[float]
    theta: Optional[float]
    vega: Optional[float]

    open_interest: Optional[int]
    volume: Optional[int]

    spread_pct: Optional[float]
    """Bid-ask spread as a percentage of mid price."""


@dataclass
class BuiltStructure:
    """One fully-assembled (or partially-assembled) option structure.

    When structure_status == "COMPLETE" all legs have quotes and debits are
    positive.  When "INCOMPLETE" or "REJECTED", rejection_codes explain why.
    """

    structure_type: str
    """Mirrors OptionsStructureSpec.structure_type for easy downstream use."""

    legs: list[StructureLeg]

    conservative_debit: Optional[float]
    """Worst-case (ask-minus-bid) net debit to enter the structure."""

    mid_debit: Optional[float]
    """Mid-price net debit estimate."""

    max_leg_spread_pct: Optional[float]
    """Widest per-leg bid-ask spread percentage across all legs."""

    structure_status: str
    """"COMPLETE", "INCOMPLETE", or "REJECTED"."""

    rejection_codes: list[str]
    """Non-empty when structure_status != "COMPLETE"."""

    front_expiration: str
    back_expiration: str

    front_dte: Optional[int]
    back_dte: Optional[int]


@dataclass
class StructureBuildResult:
    """Complete output of one ``build_option_structures`` call.

    Includes both the successfully-built structures and a full audit trail
    of every expiration pair that was considered and why each was accepted
    or rejected.
    """

    ticker: str
    strategy_id: str
    structure_type: str

    structures: list[BuiltStructure]
    """Structures that were fully or partially assembled."""

    expiration_pairs_considered: list[ExpirationPairRecord]
    """One record per pair evaluated — including rejected pairs."""

    pairs_valid: int
    """Count of pairs that passed all filters and entered leg-matching."""

    pairs_rejected: int
    """Count of pairs rejected before leg-matching."""

    structures_built: int
    """Count of structures with structure_status == "COMPLETE"."""

    build_status: str
    """Top-level outcome.  One of:
    "SUCCESS"               — At least one COMPLETE structure was built.
    "NO_VALID_PAIRS"        — All pairs were rejected before leg-matching.
    "NO_MATCHING_STRIKES"   — Valid pairs found but no strike could be matched.
    "PROVIDER_INCOMPLETE"   — Chain data appears truncated.
    "MISSING_CHAIN"         — No chain data was provided at all.
    """

    build_summary: str
    """Human-readable summary of the build outcome."""

    data_completeness: str
    """"COMPLETE", "PARTIAL", or "MISSING" — overall chain coverage quality."""

    provider_completeness: dict
    """Chain coverage diagnostics.  Keys:
    "expirations_returned" (int), "expirations_requested" (int),
    "truncated" (bool).
    """


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _dte(expiration_str: str, today: Optional[date] = None) -> Optional[int]:
    """Return calendar days from *today* to *expiration_str* (ISO format).

    Returns None when the string cannot be parsed.
    """
    try:
        exp = date.fromisoformat(str(expiration_str)[:10])
        return (exp - (today or date.today())).days
    except (ValueError, TypeError):
        return None


def _mid(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    """Return the midpoint of bid/ask, or None if either value is missing."""
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2.0


def _spread_pct(
    bid: Optional[float],
    ask: Optional[float],
    mid: Optional[float],
) -> Optional[float]:
    """Return the bid-ask spread as a percentage of mid price.

    Returns None when any required value is absent or mid is zero.
    """
    if bid is None or ask is None or mid is None or mid == 0.0:
        return None
    return abs(ask - bid) / abs(mid) * 100.0


def _float_or_none(value: object) -> Optional[float]:
    """Cast *value* to float, returning None on any failure."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _int_or_none(value: object) -> Optional[int]:
    """Cast *value* to int, returning None on any failure."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _match_by_strike(
    contracts: list[dict],
    strike: float,
    option_type: Optional[str] = None,
) -> Optional[dict]:
    """Find the contract whose strike matches *strike* exactly (float equality).

    If *option_type* is provided only contracts of that type are considered.
    Returns the first match or None.
    """
    target = round(float(strike), 4)
    for c in contracts:
        if option_type and str(c.get("option_type") or "").lower() != option_type.lower():
            continue
        try:
            if round(float(c["strike"]), 4) == target:
                return c
        except (KeyError, TypeError, ValueError):
            continue
    return None


def _closest_delta(
    contracts: list[dict],
    target_delta: float,
    option_type: str,
) -> Optional[dict]:
    """Return the contract closest to *target_delta* for *option_type*.

    Uses the absolute distance |contract.delta - target_delta|.
    Contracts with a None delta are skipped.

    Args:
        contracts: Normalized contract list.
        target_delta: Signed delta target, e.g. 0.35 for calls, -0.35 for puts.
        option_type: "call" or "put" — used to pre-filter the list.

    Returns:
        Best-matching contract dict, or None if no eligible contracts found.
    """
    best: Optional[dict] = None
    best_dist = float("inf")
    for c in contracts:
        if str(c.get("option_type") or "").lower() != option_type.lower():
            continue
        delta = _float_or_none(c.get("delta"))
        if delta is None:
            continue
        dist = abs(delta - target_delta)
        if dist < best_dist:
            best_dist = dist
            best = c
    return best


def _nearest_atm(
    contracts: list[dict],
    underlying_price: float,
    option_type: Optional[str] = None,
) -> Optional[dict]:
    """Return the contract whose strike is closest to *underlying_price*.

    If *option_type* is provided only contracts of that type are considered.
    """
    best: Optional[dict] = None
    best_dist = float("inf")
    for c in contracts:
        if option_type and str(c.get("option_type") or "").lower() != option_type.lower():
            continue
        strike = _float_or_none(c.get("strike"))
        if strike is None:
            continue
        dist = abs(strike - underlying_price)
        if dist < best_dist:
            best_dist = dist
            best = c
    return best


def _compute_calendar_debit(
    front_contract: dict,
    back_contract: dict,
) -> tuple[Optional[float], Optional[float]]:
    """Return (conservative_debit, mid_debit) for a single-type calendar leg pair.

    For a calendar the strategy is long the back and short the front, so:
      conservative = back_ask - front_bid  (worst-case fill)
      mid          = back_mid - front_mid

    Returns (None, None) if required quotes are absent.
    """
    front_bid = _float_or_none(front_contract.get("bid"))
    back_ask = _float_or_none(back_contract.get("ask"))
    front_mid_val = _float_or_none(front_contract.get("mid")) or _mid(
        front_bid, _float_or_none(front_contract.get("ask"))
    )
    back_mid_val = _float_or_none(back_contract.get("mid")) or _mid(
        _float_or_none(back_contract.get("bid")), back_ask
    )

    conservative: Optional[float] = None
    if back_ask is not None and front_bid is not None:
        conservative = back_ask - front_bid

    mid_val: Optional[float] = None
    if back_mid_val is not None and front_mid_val is not None:
        mid_val = back_mid_val - front_mid_val

    return conservative, mid_val


def _compute_vertical_debit(
    long_contract: dict,
    short_contract: dict,
) -> tuple[Optional[float], Optional[float]]:
    """Return (conservative_debit, mid_debit) for a debit vertical.

    For a debit vertical the strategy is long one strike and short another:
      conservative = long_ask - short_bid  (worst-case fill)
      mid          = long_mid - short_mid

    Returns (None, None) if required quotes are absent.
    """
    long_ask = _float_or_none(long_contract.get("ask"))
    short_bid = _float_or_none(short_contract.get("bid"))
    long_mid_val = _float_or_none(long_contract.get("mid")) or _mid(
        _float_or_none(long_contract.get("bid")), long_ask
    )
    short_mid_val = _float_or_none(short_contract.get("mid")) or _mid(
        short_bid, _float_or_none(short_contract.get("ask"))
    )

    conservative: Optional[float] = None
    if long_ask is not None and short_bid is not None:
        conservative = long_ask - short_bid

    mid_val: Optional[float] = None
    if long_mid_val is not None and short_mid_val is not None:
        mid_val = long_mid_val - short_mid_val

    return conservative, mid_val


def _check_liquidity(
    contracts: list[dict],
    requirements: LiquidityRequirements,
) -> tuple[bool, list[str]]:
    """Check every contract against the liquidity requirements.

    Args:
        contracts: List of contract dicts to check (all legs of the structure).
        requirements: LiquidityRequirements spec from the caller.

    Returns:
        (passed, failures) where *passed* is True only when all checks pass
        and *failures* is a list of human-readable failure descriptions.
    """
    failures: list[str] = []

    for c in contracts:
        label = f"{c.get('option_type','?')} {c.get('strike','?')} {c.get('expiration','?')}"

        if requirements.require_nonzero_bid:
            bid = _float_or_none(c.get("bid"))
            if bid is None or bid <= 0.0:
                failures.append(f"{label}: bid is zero or missing")

        if requirements.min_bid is not None:
            bid = _float_or_none(c.get("bid"))
            if bid is None or bid < requirements.min_bid:
                failures.append(
                    f"{label}: bid {bid} < min_bid {requirements.min_bid}"
                )

        if requirements.min_open_interest is not None:
            oi = _int_or_none(c.get("open_interest"))
            if oi is None or oi < requirements.min_open_interest:
                failures.append(
                    f"{label}: OI {oi} < min_oi {requirements.min_open_interest}"
                )

        if requirements.min_volume is not None:
            vol = _int_or_none(c.get("volume"))
            if vol is None or vol < requirements.min_volume:
                failures.append(
                    f"{label}: volume {vol} < min_volume {requirements.min_volume}"
                )

        if requirements.max_bid_ask_spread_pct is not None:
            bid = _float_or_none(c.get("bid"))
            ask = _float_or_none(c.get("ask"))
            mid_val = _float_or_none(c.get("mid")) or _mid(bid, ask)
            sp = _spread_pct(bid, ask, mid_val)
            if sp is None or sp > requirements.max_bid_ask_spread_pct:
                failures.append(
                    f"{label}: spread {sp}% > max {requirements.max_bid_ask_spread_pct}%"
                )

    return len(failures) == 0, failures


# ---------------------------------------------------------------------------
# Leg extraction helper
# ---------------------------------------------------------------------------


def _extract_leg(
    contract: dict,
    role: str,
    option_type: str,
    expiration: str,
) -> StructureLeg:
    """Convert a normalized contract dict into a StructureLeg."""
    bid = _float_or_none(contract.get("bid"))
    ask = _float_or_none(contract.get("ask"))
    mid_val = _float_or_none(contract.get("mid")) or _mid(bid, ask)
    sp = _spread_pct(bid, ask, mid_val)

    return StructureLeg(
        role=role,
        option_type=option_type.lower(),
        expiration=expiration,
        strike=float(contract.get("strike") or 0),
        bid=bid,
        ask=ask,
        mid=mid_val,
        iv=_float_or_none(contract.get("iv")),
        delta=_float_or_none(contract.get("delta")),
        gamma=_float_or_none(contract.get("gamma")),
        theta=_float_or_none(contract.get("theta")),
        vega=_float_or_none(contract.get("vega")),
        open_interest=_int_or_none(contract.get("open_interest")),
        volume=_int_or_none(contract.get("volume")),
        spread_pct=sp,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def enumerate_expiration_pairs(
    expirations: list[str],
    spec: OptionsStructureSpec,
    today: Optional[date] = None,
    event_date: Optional[str] = None,
) -> list[ExpirationPairRecord]:
    """Enumerate ALL possible front/back expiration pairs and record a disposition.

    Every pair (front, back) where front < back is evaluated.  The result
    list contains one ``ExpirationPairRecord`` per pair considered, whether
    it was accepted or rejected.  No pair is silently discarded.

    The ordering is front ascending, back ascending within each front.

    Args:
        expirations: List of ISO-format expiration date strings available in
                     the chain.  Duplicates are ignored.
        spec: OptionsStructureSpec that declares DTE and gap requirements.
        today: Reference date for DTE calculations.  Defaults to date.today().
        event_date: ISO-format date of the earnings/event.  When provided,
                    EventRelationshipRule fields in spec are evaluated.

    Returns:
        List of ExpirationPairRecord, one per evaluated pair.
    """
    today = today or date.today()
    records: list[ExpirationPairRecord] = []

    # Parse event date once
    event_dt: Optional[date] = None
    if event_date:
        try:
            event_dt = date.fromisoformat(str(event_date)[:10])
        except (ValueError, TypeError):
            event_dt = None

    # Deduplicate and sort expirations by date
    seen: set[str] = set()
    sorted_exps: list[tuple[str, int]] = []
    for exp_str in expirations:
        key = str(exp_str)[:10]
        if key in seen:
            continue
        seen.add(key)
        dte = _dte(key, today)
        if dte is None:
            # Record as unparseable and skip from pair enumeration
            records.append(
                ExpirationPairRecord(
                    front_expiration=key,
                    back_expiration=key,
                    front_dte=None,
                    back_dte=None,
                    expiration_gap_days=None,
                    front_before_event=None,
                    back_after_event=None,
                    event_between_legs=None,
                    front_contracts_available=None,
                    back_contracts_available=None,
                    pair_status=PairStatus.UNSUPPORTED_EXPIRATION,
                    pair_rejection_codes=["Could not parse expiration date"],
                    data_sources=[],
                    data_freshness=None,
                )
            )
            continue
        sorted_exps.append((key, dte))

    sorted_exps.sort(key=lambda x: x[1])

    # Enumerate all (front, back) pairs where front comes before back
    for i, (front_exp, front_dte) in enumerate(sorted_exps):
        for j in range(i + 1, len(sorted_exps)):
            back_exp, back_dte = sorted_exps[j]
            gap = back_dte - front_dte

            rejection_codes: list[str] = []
            status = PairStatus.VALID  # optimistic; overridden below

            # --- DTE range checks ---
            if spec.front_dte_min is not None and front_dte < spec.front_dte_min:
                rejection_codes.append(
                    f"front_dte {front_dte} < front_dte_min {spec.front_dte_min}"
                )
            if spec.front_dte_max is not None and front_dte > spec.front_dte_max:
                rejection_codes.append(
                    f"front_dte {front_dte} > front_dte_max {spec.front_dte_max}"
                )
            if spec.back_dte_min is not None and back_dte < spec.back_dte_min:
                rejection_codes.append(
                    f"back_dte {back_dte} < back_dte_min {spec.back_dte_min}"
                )
            if spec.back_dte_max is not None and back_dte > spec.back_dte_max:
                rejection_codes.append(
                    f"back_dte {back_dte} > back_dte_max {spec.back_dte_max}"
                )

            # --- Gap checks ---
            if spec.min_expiration_gap_days is not None and gap < spec.min_expiration_gap_days:
                rejection_codes.append(
                    f"gap {gap}d < min_gap {spec.min_expiration_gap_days}d"
                )
            if spec.max_expiration_gap_days is not None and gap > spec.max_expiration_gap_days:
                rejection_codes.append(
                    f"gap {gap}d > max_gap {spec.max_expiration_gap_days}d"
                )

            # --- Event relationship checks ---
            front_before_event: Optional[bool] = None
            back_after_event: Optional[bool] = None
            event_between: Optional[bool] = None

            if event_dt is not None:
                front_exp_dt = date.fromisoformat(front_exp)
                back_exp_dt = date.fromisoformat(back_exp)
                front_before_event = front_exp_dt < event_dt
                back_after_event = back_exp_dt >= event_dt
                event_between = front_before_event and back_after_event

                er = spec.event_relationship
                if er is not None:
                    if er.front_must_expire_before_event and not front_before_event:
                        rejection_codes.append(
                            f"front {front_exp} does not expire before event {event_date}"
                        )
                    if er.back_must_expire_after_event and not back_after_event:
                        rejection_codes.append(
                            f"back {back_exp} does not expire after event {event_date}"
                        )
                    if er.event_must_be_between_legs and not event_between:
                        rejection_codes.append(
                            f"event {event_date} is not between front {front_exp} and back {back_exp}"
                        )
                    if er.event_within_dte_of_front is not None:
                        days_to_event = (event_dt - date.today()).days
                        if days_to_event > er.event_within_dte_of_front:
                            rejection_codes.append(
                                f"event is {days_to_event}d away, beyond event_within_dte_of_front {er.event_within_dte_of_front}"
                            )

            # --- Assign final status ---
            if rejection_codes:
                # Map the first/most-important code to a status token
                first = rejection_codes[0]
                if "front_dte" in first and "front_dte_min" in first:
                    status = PairStatus.CLOSING_WINDOW
                elif "front_dte" in first and "front_dte_max" in first:
                    status = PairStatus.PRE_WINDOW
                elif "event" in first:
                    status = PairStatus.EVENT_SPANNING
                else:
                    status = PairStatus.REJECTED_BY_STRATEGY
            else:
                # Apply VALID_BUT_* qualifiers even for passing pairs
                if spec.front_dte_min is not None and front_dte <= spec.front_dte_min + 3:
                    status = PairStatus.VALID_BUT_LOW_DTE
                elif (
                    spec.max_expiration_gap_days is not None
                    and gap >= spec.max_expiration_gap_days - 7
                ):
                    status = PairStatus.VALID_BUT_WIDE_GAP
                else:
                    status = PairStatus.VALID

            records.append(
                ExpirationPairRecord(
                    front_expiration=front_exp,
                    back_expiration=back_exp,
                    front_dte=front_dte,
                    back_dte=back_dte,
                    expiration_gap_days=gap,
                    front_before_event=front_before_event,
                    back_after_event=back_after_event,
                    event_between_legs=event_between,
                    front_contracts_available=None,  # filled in by build_option_structures
                    back_contracts_available=None,
                    pair_status=status,
                    pair_rejection_codes=rejection_codes,
                    data_sources=[],
                    data_freshness=None,
                )
            )

    return records


# ---------------------------------------------------------------------------
# Structure assembly helpers
# ---------------------------------------------------------------------------


def _select_strike(
    contracts: list[dict],
    option_type: str,
    spec: OptionsStructureSpec,
    underlying_price: Optional[float],
) -> Optional[dict]:
    """Select a single contract from *contracts* per the spec's strike method.

    Args:
        contracts: All contracts available for one expiration.
        option_type: "call" or "put".
        spec: The caller's OptionsStructureSpec.
        underlying_price: Current underlying price (required for ATM method).

    Returns:
        Best-matching contract dict or None.
    """
    typed = [c for c in contracts if str(c.get("option_type") or "").lower() == option_type.lower()]
    if not typed:
        return None

    method = spec.strike_selection_method

    if method == "delta_target" and spec.delta_targets:
        target = spec.delta_targets.get(option_type)
        if target is not None:
            return _closest_delta(typed, target, option_type)

    if method == "nearest_atm" and underlying_price:
        return _nearest_atm(typed, underlying_price, option_type)

    # Fallback — nearest ATM if possible, else first contract
    if underlying_price:
        return _nearest_atm(typed, underlying_price, option_type)
    return typed[0] if typed else None


def _build_calendar_structure(
    front_exp: str,
    back_exp: str,
    front_dte: int,
    back_dte: int,
    front_chain: list[dict],
    back_chain: list[dict],
    option_type: str,
    spec: OptionsStructureSpec,
    underlying_price: Optional[float],
) -> BuiltStructure:
    """Build one single-type calendar (call or put) from already-fetched chains.

    Returns a BuiltStructure with status COMPLETE, INCOMPLETE, or REJECTED.
    """
    rejection_codes: list[str] = []

    # Select front leg
    front_contract = _select_strike(front_chain, option_type, spec, underlying_price)
    if front_contract is None:
        return BuiltStructure(
            structure_type=f"{option_type}_calendar",
            legs=[],
            conservative_debit=None,
            mid_debit=None,
            max_leg_spread_pct=None,
            structure_status="REJECTED",
            rejection_codes=[f"No {option_type} contracts found in front chain {front_exp}"],
            front_expiration=front_exp,
            back_expiration=back_exp,
            front_dte=front_dte,
            back_dte=back_dte,
        )

    front_strike = float(front_contract.get("strike") or 0)

    # Match back leg to front strike when same_strike_required
    if spec.same_strike_required:
        back_contract = _match_by_strike(back_chain, front_strike, option_type)
    else:
        back_contract = _select_strike(back_chain, option_type, spec, underlying_price)

    if back_contract is None:
        return BuiltStructure(
            structure_type=f"{option_type}_calendar",
            legs=[],
            conservative_debit=None,
            mid_debit=None,
            max_leg_spread_pct=None,
            structure_status="REJECTED",
            rejection_codes=[
                f"No matching {option_type} strike {front_strike} in back chain {back_exp}"
            ],
            front_expiration=front_exp,
            back_expiration=back_exp,
            front_dte=front_dte,
            back_dte=back_dte,
        )

    # Build legs
    front_leg = _extract_leg(front_contract, "front", option_type, front_exp)
    back_leg = _extract_leg(back_contract, "back", option_type, back_exp)
    legs = [front_leg, back_leg]

    # Debit
    conservative, mid_val = _compute_calendar_debit(front_contract, back_contract)

    if conservative is None or conservative <= 0:
        rejection_codes.append(
            f"Invalid conservative debit: {conservative} (front_bid={front_leg.bid}, back_ask={back_leg.ask})"
        )
    if mid_val is None or mid_val <= 0:
        rejection_codes.append(f"Invalid mid debit: {mid_val}")

    # Spread pct
    spreads = [leg.spread_pct for leg in legs if leg.spread_pct is not None]
    max_spread = max(spreads) if spreads else None

    # Liquidity
    if spec.liquidity_requirements is not None:
        liq_pass, liq_failures = _check_liquidity(
            [front_contract, back_contract], spec.liquidity_requirements
        )
        if not liq_pass:
            rejection_codes.extend(liq_failures)

    status = "COMPLETE" if not rejection_codes else "REJECTED"

    return BuiltStructure(
        structure_type=f"{option_type}_calendar",
        legs=legs,
        conservative_debit=round(conservative, 4) if conservative is not None else None,
        mid_debit=round(mid_val, 4) if mid_val is not None else None,
        max_leg_spread_pct=round(max_spread, 2) if max_spread is not None else None,
        structure_status=status,
        rejection_codes=rejection_codes,
        front_expiration=front_exp,
        back_expiration=back_exp,
        front_dte=front_dte,
        back_dte=back_dte,
    )


def _build_vertical_structure(
    expiration: str,
    dte: int,
    chain: list[dict],
    option_type: str,
    spec: OptionsStructureSpec,
    underlying_price: Optional[float],
) -> Optional[BuiltStructure]:
    """Build one debit vertical from a single-expiration chain.

    For a call debit vertical: long lower-strike call, short higher-strike call.
    For a put debit vertical:  long higher-strike put, short lower-strike put.

    Returns None when no usable long/short pair can be constructed.
    Currently selects the long leg by spec (delta/ATM) and a short leg
    one standard width above (calls) or below (puts).
    """
    rejection_codes: list[str] = []
    typed = [
        c for c in chain
        if str(c.get("option_type") or "").lower() == option_type.lower()
    ]
    if not typed:
        return None

    typed.sort(key=lambda c: float(c.get("strike") or 0))

    long_contract = _select_strike(typed, option_type, spec, underlying_price)
    if long_contract is None:
        return None

    long_strike = float(long_contract.get("strike") or 0)

    # For calls: short strike is the next strike above long.
    # For puts: short strike is the next strike below long.
    short_contract: Optional[dict] = None
    if option_type == "call":
        candidates = [c for c in typed if float(c.get("strike") or 0) > long_strike]
        short_contract = candidates[0] if candidates else None
    else:  # put
        candidates = [c for c in typed if float(c.get("strike") or 0) < long_strike]
        short_contract = candidates[-1] if candidates else None

    if short_contract is None:
        return None

    long_leg = _extract_leg(long_contract, "long", option_type, expiration)
    short_leg = _extract_leg(short_contract, "short", option_type, expiration)
    legs = [long_leg, short_leg]

    conservative, mid_val = _compute_vertical_debit(long_contract, short_contract)

    if conservative is None or conservative <= 0:
        rejection_codes.append(f"Invalid conservative debit: {conservative}")
    if mid_val is None or mid_val <= 0:
        rejection_codes.append(f"Invalid mid debit: {mid_val}")

    spreads = [leg.spread_pct for leg in legs if leg.spread_pct is not None]
    max_spread = max(spreads) if spreads else None

    if spec.liquidity_requirements is not None:
        liq_pass, liq_failures = _check_liquidity(
            [long_contract, short_contract], spec.liquidity_requirements
        )
        if not liq_pass:
            rejection_codes.extend(liq_failures)

    status = "COMPLETE" if not rejection_codes else "REJECTED"

    return BuiltStructure(
        structure_type=f"{option_type}_debit_vertical",
        legs=legs,
        conservative_debit=round(conservative, 4) if conservative is not None else None,
        mid_debit=round(mid_val, 4) if mid_val is not None else None,
        max_leg_spread_pct=round(max_spread, 2) if max_spread is not None else None,
        structure_status=status,
        rejection_codes=rejection_codes,
        front_expiration=expiration,  # verticals have one expiration
        back_expiration=expiration,
        front_dte=dte,
        back_dte=dte,
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def build_option_structures(
    *,
    ticker: str,
    underlying_quote: Optional[dict],
    normalized_chain_set: dict,
    spec: OptionsStructureSpec,
    event_context: Optional[dict] = None,
    market_context: Optional[dict] = None,
    build_context: Optional[dict] = None,
) -> StructureBuildResult:
    """Main entry point.  Builds all valid structures per the spec.

    Args:
        ticker: Underlying ticker symbol, e.g. "NVDA".
        underlying_quote: Current price quote dict.  Should contain a "price"
                          (or "last") field.  May be None when unavailable.
        normalized_chain_set: Dict mapping expiration date string to list of
                              normalized contract dicts.
                              Example: {"2026-08-15": [{"strike": 130, ...}, ...]}
        spec: OptionsStructureSpec declaring all structure requirements.
        event_context: Optional earnings/event timing context.
                       Expected keys: "earnings_date" (ISO str), "session" (str).
        market_context: Optional market data (e.g. {"vix": 18.5}).
        build_context: Optional build-time metadata (run_id, scan_id, etc.).

    Returns:
        StructureBuildResult containing all built structures and a complete
        audit trail of every expiration pair considered.
    """
    # --- Resolve underlying price ---
    underlying_price: Optional[float] = None
    if underlying_quote:
        for field_name in ("price", "last", "close", "mark"):
            val = _float_or_none(underlying_quote.get(field_name))
            if val and val > 0:
                underlying_price = val
                break

    # --- Resolve event date ---
    event_date: Optional[str] = None
    if event_context:
        event_date = (
            event_context.get("earnings_date")
            or event_context.get("event_date")
        )

    # --- Quick guard: no chain data at all ---
    expirations = sorted(normalized_chain_set.keys()) if normalized_chain_set else []
    if not expirations:
        return StructureBuildResult(
            ticker=ticker,
            strategy_id=spec.strategy_id,
            structure_type=spec.structure_type,
            structures=[],
            expiration_pairs_considered=[],
            pairs_valid=0,
            pairs_rejected=0,
            structures_built=0,
            build_status="MISSING_CHAIN",
            build_summary="No option chain data was provided.",
            data_completeness="MISSING",
            provider_completeness={
                "expirations_returned": 0,
                "expirations_requested": 0,
                "truncated": False,
            },
        )

    # --- Enumerate pairs ---
    all_pair_records = enumerate_expiration_pairs(
        expirations=expirations,
        spec=spec,
        event_date=event_date,
    )

    # --- Annotate pair records with chain-level contract counts ---
    for rec in all_pair_records:
        if rec.pair_status == PairStatus.UNSUPPORTED_EXPIRATION:
            continue
        front_chain = normalized_chain_set.get(rec.front_expiration, [])
        back_chain = normalized_chain_set.get(rec.back_expiration, [])
        rec.front_contracts_available = len(front_chain)
        rec.back_contracts_available = len(back_chain)
        if not front_chain and rec.pair_status in (PairStatus.VALID, PairStatus.VALID_BUT_LOW_DTE, PairStatus.VALID_BUT_WIDE_GAP):
            rec.pair_status = PairStatus.MISSING_FRONT_CHAIN
            rec.pair_rejection_codes.append(f"No chain data for front {rec.front_expiration}")
        if not back_chain and rec.pair_status in (PairStatus.VALID, PairStatus.VALID_BUT_LOW_DTE, PairStatus.VALID_BUT_WIDE_GAP):
            rec.pair_status = PairStatus.MISSING_BACK_CHAIN
            rec.pair_rejection_codes.append(f"No chain data for back {rec.back_expiration}")

    valid_statuses = {PairStatus.VALID, PairStatus.VALID_BUT_LOW_DTE, PairStatus.VALID_BUT_WIDE_GAP}
    valid_pairs = [r for r in all_pair_records if r.pair_status in valid_statuses]
    rejected_pairs = [r for r in all_pair_records if r.pair_status not in valid_statuses]

    if not valid_pairs:
        return StructureBuildResult(
            ticker=ticker,
            strategy_id=spec.strategy_id,
            structure_type=spec.structure_type,
            structures=[],
            expiration_pairs_considered=all_pair_records,
            pairs_valid=0,
            pairs_rejected=len(all_pair_records),
            structures_built=0,
            build_status="NO_VALID_PAIRS",
            build_summary=(
                f"All {len(all_pair_records)} expiration pair(s) were rejected. "
                f"First rejection: {rejected_pairs[0].pair_rejection_codes[0] if rejected_pairs and rejected_pairs[0].pair_rejection_codes else 'unknown'}."
            ),
            data_completeness="PARTIAL" if expirations else "MISSING",
            provider_completeness={
                "expirations_returned": len(expirations),
                "expirations_requested": len(expirations),
                "truncated": False,
            },
        )

    # --- Build structures from valid pairs ---
    structures: list[BuiltStructure] = []
    structure_type = spec.structure_type

    for pair in valid_pairs:
        if len(structures) >= spec.maximum_structures:
            break

        front_chain = normalized_chain_set.get(pair.front_expiration, [])
        back_chain = normalized_chain_set.get(pair.back_expiration, [])

        if structure_type in ("call_calendar", "put_calendar"):
            opt_type = structure_type.split("_")[0]  # "call" or "put"
            s = _build_calendar_structure(
                front_exp=pair.front_expiration,
                back_exp=pair.back_expiration,
                front_dte=pair.front_dte or 0,
                back_dte=pair.back_dte or 0,
                front_chain=front_chain,
                back_chain=back_chain,
                option_type=opt_type,
                spec=spec,
                underlying_price=underlying_price,
            )
            structures.append(s)

        elif structure_type == "double_calendar":
            # Build call and put calendars, then combine into one structure
            call_struct = _build_calendar_structure(
                front_exp=pair.front_expiration,
                back_exp=pair.back_expiration,
                front_dte=pair.front_dte or 0,
                back_dte=pair.back_dte or 0,
                front_chain=front_chain,
                back_chain=back_chain,
                option_type="call",
                spec=spec,
                underlying_price=underlying_price,
            )
            put_struct = _build_calendar_structure(
                front_exp=pair.front_expiration,
                back_exp=pair.back_expiration,
                front_dte=pair.front_dte or 0,
                back_dte=pair.back_dte or 0,
                front_chain=front_chain,
                back_chain=back_chain,
                option_type="put",
                spec=spec,
                underlying_price=underlying_price,
            )
            # Combine legs and debits from both calendars
            combined_legs = call_struct.legs + put_struct.legs
            combined_rejection = call_struct.rejection_codes + put_struct.rejection_codes

            conservative: Optional[float] = None
            mid_val: Optional[float] = None
            if (
                call_struct.conservative_debit is not None
                and put_struct.conservative_debit is not None
            ):
                conservative = call_struct.conservative_debit + put_struct.conservative_debit
            if call_struct.mid_debit is not None and put_struct.mid_debit is not None:
                mid_val = call_struct.mid_debit + put_struct.mid_debit

            all_spreads = [
                leg.spread_pct
                for leg in combined_legs
                if leg.spread_pct is not None
            ]
            max_spread = max(all_spreads) if all_spreads else None

            combined_status = (
                "COMPLETE"
                if call_struct.structure_status == "COMPLETE"
                and put_struct.structure_status == "COMPLETE"
                else "REJECTED"
            )

            structures.append(
                BuiltStructure(
                    structure_type="double_calendar",
                    legs=combined_legs,
                    conservative_debit=round(conservative, 4) if conservative is not None else None,
                    mid_debit=round(mid_val, 4) if mid_val is not None else None,
                    max_leg_spread_pct=round(max_spread, 2) if max_spread is not None else None,
                    structure_status=combined_status,
                    rejection_codes=combined_rejection,
                    front_expiration=pair.front_expiration,
                    back_expiration=pair.back_expiration,
                    front_dte=pair.front_dte,
                    back_dte=pair.back_dte,
                )
            )

        elif structure_type in ("call_debit_vertical", "put_debit_vertical"):
            opt_type = structure_type.split("_")[0]  # "call" or "put"
            # Verticals use only one expiration; treat front as the target
            s = _build_vertical_structure(
                expiration=pair.front_expiration,
                dte=pair.front_dte or 0,
                chain=front_chain,
                option_type=opt_type,
                spec=spec,
                underlying_price=underlying_price,
            )
            if s is not None:
                structures.append(s)
        else:
            # Unknown structure type — record and skip
            structures.append(
                BuiltStructure(
                    structure_type=structure_type,
                    legs=[],
                    conservative_debit=None,
                    mid_debit=None,
                    max_leg_spread_pct=None,
                    structure_status="REJECTED",
                    rejection_codes=[
                        f"Unsupported structure_type '{structure_type}'"
                    ],
                    front_expiration=pair.front_expiration,
                    back_expiration=pair.back_expiration,
                    front_dte=pair.front_dte,
                    back_dte=pair.back_dte,
                )
            )

    # --- Rank complete structures ---
    def _ranking_key(s: BuiltStructure) -> tuple:
        """Lower is better for debit_mid ranking."""
        prefs = spec.ranking_preferences or ["debit_mid"]
        keys = []
        for pref in prefs:
            if pref == "debit_mid":
                keys.append(s.mid_debit if s.mid_debit is not None else float("inf"))
        return tuple(keys)

    complete = [s for s in structures if s.structure_status == "COMPLETE"]
    complete.sort(key=_ranking_key)
    incomplete = [s for s in structures if s.structure_status != "COMPLETE"]
    ranked_structures = complete + incomplete

    # --- Determine overall build status ---
    structures_built = len(complete)

    if structures_built > 0:
        build_status = "SUCCESS"
        build_summary = (
            f"Built {structures_built} complete {structure_type} structure(s) "
            f"from {len(valid_pairs)} valid pair(s)."
        )
    elif structures:
        build_status = "NO_MATCHING_STRIKES"
        first_rejection = structures[0].rejection_codes[0] if structures[0].rejection_codes else "unknown"
        build_summary = (
            f"No complete structures built from {len(valid_pairs)} valid pair(s). "
            f"First rejection: {first_rejection}."
        )
    else:
        build_status = "NO_MATCHING_STRIKES"
        build_summary = (
            f"No structures could be assembled from {len(valid_pairs)} valid pair(s)."
        )

    # --- Data completeness ---
    if not expirations:
        data_completeness = "MISSING"
    elif len(valid_pairs) < len(all_pair_records) // 2:
        data_completeness = "PARTIAL"
    else:
        data_completeness = "COMPLETE"

    return StructureBuildResult(
        ticker=ticker,
        strategy_id=spec.strategy_id,
        structure_type=spec.structure_type,
        structures=ranked_structures,
        expiration_pairs_considered=all_pair_records,
        pairs_valid=len(valid_pairs),
        pairs_rejected=len(rejected_pairs),
        structures_built=structures_built,
        build_status=build_status,
        build_summary=build_summary,
        data_completeness=data_completeness,
        provider_completeness={
            "expirations_returned": len(expirations),
            "expirations_requested": len(expirations),
            "truncated": False,
        },
    )
