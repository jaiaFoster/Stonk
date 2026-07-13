"""Declarative specification model for option structure construction requests.

Strategies declare WHAT they want; the universal builder decides HOW to build it.

ASA Patch 33A — Universal Options Structure Builder foundation.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LegDefinition:
    """Describes one leg of an option structure.

    Attributes:
        role: Semantic role within the structure.
              Calendars: "front" (short) or "back" (long).
              Verticals: "long" or "short".
              Diagonals/condors: "near", "far", "long_put", "short_put", etc.
        option_type: "call", "put", or "either".
        expiration_slot: Which expiration bucket this leg occupies.
                         "front" (near-term) or "back" (far-term).
        position: Direction from strategy perspective — "long" or "short".
        delta_target: Signed delta the builder should target when selecting a
                      strike, e.g. 0.35 for a call or -0.35 for a put.
        moneyness_target: Alternative strike-selection hint when delta is
                          unavailable — "atm", "otm_1_strike", "itm_1_strike".
        strike_match: Constrain strike relative to another leg.
                      "same_as_front" means match the front-leg strike exactly.
                      "same_as_back" means match the back-leg strike exactly.
                      None means select independently.
    """

    role: str               # "front", "back", "long", "short", "near", "far"
    option_type: str        # "call", "put", "either"
    expiration_slot: str    # "front" or "back"
    position: str           # "long" or "short" (from strategy perspective)
    delta_target: Optional[float] = None       # e.g. 0.35 for FF call leg
    moneyness_target: Optional[str] = None     # "atm", "otm_1_strike"
    strike_match: Optional[str] = None         # "same_as_front", "same_as_back"


@dataclass
class LiquidityRequirements:
    """Liquidity thresholds applied per leg during structure construction.

    Any field left as None is not checked by the builder.
    """

    max_bid_ask_spread_pct: Optional[float] = None  # e.g. 35.0 means ≤35 %
    min_open_interest: Optional[int] = None
    min_volume: Optional[int] = None
    min_bid: Optional[float] = None
    require_nonzero_bid: bool = True


@dataclass
class EventRelationshipRule:
    """Constraints describing how earnings/events relate to the two expirations.

    For a classic earnings-IV-crush calendar:
        front_must_expire_before_event = True
        back_must_expire_after_event   = True
        event_must_be_between_legs     = True

    For a pre-earnings vol-buying calendar:
        front_must_expire_before_event = False
        back_must_expire_after_event   = True
        event_within_dte_of_front      = 14
    """

    front_must_expire_before_event: bool = False
    back_must_expire_after_event: bool = False
    event_must_be_between_legs: bool = False
    event_within_dte_of_front: Optional[int] = None  # e.g. 14 days


@dataclass
class OptionsStructureSpec:
    """Full specification for one option structure construction request.

    Strategies instantiate this dataclass to express their requirements.
    The universal builder interprets these fields to enumerate expiration
    pairs, match strikes, check liquidity, and assemble BuiltStructure results.

    Usage example — Forward Factor double calendar::

        spec = OptionsStructureSpec(
            strategy_id="forward_factor",
            structure_type="double_calendar",
            option_types=["call", "put"],
            front_dte_min=35,
            front_dte_max=90,
            min_expiration_gap_days=14,
            max_expiration_gap_days=49,
            strike_selection_method="delta_target",
            delta_targets={"call": 0.35, "put": -0.35},
            same_strike_required=True,
            liquidity_requirements=LiquidityRequirements(
                max_bid_ask_spread_pct=35.0,
                min_open_interest=10,
                require_nonzero_bid=True,
            ),
            maximum_structures=5,
        )
    """

    # --- Identity ---
    strategy_id: str
    """Unique identifier for the calling strategy, e.g. "forward_factor"."""

    structure_type: str
    """Structure type token.  Supported values (builder may reject unknown types):
    "call_calendar", "put_calendar", "double_calendar",
    "call_debit_vertical", "put_debit_vertical".
    Extensible for "diagonal", "iron_condor", "straddle", "strangle", etc.
    """

    option_types: list[str]
    """Option types required by this structure.
    Single-type: ["call"] or ["put"].
    Double calendar: ["call", "put"].
    """

    # --- Leg definitions (optional — builder uses defaults when empty) ---
    leg_definitions: list[LegDefinition] = field(default_factory=list)
    """Explicit per-leg instructions. When provided the builder respects them.
    When empty the builder derives legs from structure_type + option_types.
    """

    # --- Expiration requirements ---
    front_dte_min: Optional[int] = None
    """Minimum DTE for the front (near-term / short) expiration."""

    front_dte_max: Optional[int] = None
    """Maximum DTE for the front expiration."""

    back_dte_min: Optional[int] = None
    """Minimum DTE for the back (far-term / long) expiration.
    Leave None when using a derived back-DTE rule (front + gap range).
    """

    back_dte_max: Optional[int] = None
    """Maximum DTE for the back expiration."""

    min_expiration_gap_days: Optional[int] = None
    """Minimum calendar days between front and back expirations."""

    max_expiration_gap_days: Optional[int] = None
    """Maximum calendar days between front and back expirations."""

    same_strike_required: bool = False
    """When True, all legs sharing an option_type must use the same strike
    (e.g. front-call strike == back-call strike in a double calendar).
    """

    # --- Strike selection ---
    strike_selection_method: str = "nearest_atm"
    """Primary strike selection algorithm.  One of:
    "nearest_atm"   — select the strike closest to the underlying price.
    "delta_target"  — select the strike closest to delta_targets[option_type].
    "moneyness"     — use moneyness_target on the relevant LegDefinition.
    """

    delta_targets: Optional[dict[str, float]] = None
    """Target delta per option type when strike_selection_method="delta_target".
    Example: {"call": 0.35, "put": -0.35}
    """

    # --- Event context rules ---
    event_relationship: Optional[EventRelationshipRule] = None
    """Earnings/event timing constraints for expiration pair filtering.
    None means no event-based filtering is applied.
    """

    # --- Liquidity ---
    liquidity_requirements: Optional[LiquidityRequirements] = None
    """Per-leg liquidity thresholds. None skips all liquidity checks."""

    # --- Output constraints ---
    maximum_structures: int = 1
    """Maximum number of candidate structures to return."""

    ranking_preferences: list[str] = field(
        default_factory=lambda: ["debit_mid"]
    )
    """Ordered list of ranking keys when multiple structures are found.
    Supported keys: "debit_mid" (prefer lower mid debit).
    Future keys: "delta_accuracy", "iv_edge", "liquidity_score".
    """
