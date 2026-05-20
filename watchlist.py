"""Root compatibility wrapper for watchlist candidate utilities."""

from app.services.watchlist_service import (  # noqa: F401
    get_watchlist_candidates,
    merge_watchlist_universe_positions,
    synthetic_positions_from_watchlist,
)
from app.services.watchlist_review_service import review_watchlist_candidates  # noqa: F401
