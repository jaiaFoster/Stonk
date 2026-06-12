"""
app/services/portfolio_service.py — Portfolio data service.

For now this is a thin wrapper around the Robinhood provider. Later, this is
where multiple brokerage providers, account aggregation, and normalization
should live.
"""

from app.providers.robinhood_provider import get_positions, get_positions_with_status
from app.services.broker_position_snapshot_service import BrokerPositionSnapshotRepository, apply_broker_position_fallback


def get_portfolio_positions() -> list[dict]:
    """Fetch the user's current portfolio positions."""
    return get_positions()


def get_portfolio_positions_with_status() -> dict:
    """Fetch positions plus Robinhood provider status."""
    return apply_broker_position_fallback(
        get_positions_with_status(),
        BrokerPositionSnapshotRepository(log_print=lambda message: print(message, flush=True)),
    )
