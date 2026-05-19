"""
app/models/position.py — Normalized portfolio position model.

The current app still passes dictionaries around to preserve behavior, but this
model establishes the shape future services and strategies should use.
"""

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class Position:
    ticker: str
    quantity: float
    avg_buy_price: float
    current_price: float | None
    gain_loss: float | None
    gain_loss_pct: float | None
    market_value: float | None
    account: str
    asset_type: str = "stock"
    source: str = "robinhood"

    def to_dict(self) -> dict[str, Any]:
        """Return a dictionary shaped like the legacy position dictionaries."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Position":
        """Build a Position from the current legacy dictionary shape."""
        return cls(
            ticker=str(data.get("ticker", "UNKNOWN")),
            quantity=float(data.get("quantity", 0.0)),
            avg_buy_price=float(data.get("avg_buy_price", 0.0)),
            current_price=(
                float(data["current_price"])
                if data.get("current_price") is not None
                else None
            ),
            gain_loss=(
                float(data["gain_loss"])
                if data.get("gain_loss") is not None
                else None
            ),
            gain_loss_pct=(
                float(data["gain_loss_pct"])
                if data.get("gain_loss_pct") is not None
                else None
            ),
            market_value=(
                float(data["market_value"])
                if data.get("market_value") is not None
                else None
            ),
            account=str(data.get("account", "Unknown")),
            asset_type=str(data.get("asset_type", "stock")),
            source=str(data.get("source", "robinhood")),
        )
