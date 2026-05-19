"""
app/models/news_item.py — Normalized news article model.

News used to be represented as plain headline strings. This model gives each
article the shape the advisor will need later for scoring, filtering, storage,
and recommendation explanations.
"""

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class NewsItem:
    ticker: str
    title: str
    source: str
    url: str
    published_at: str
    relevance_score: float

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dictionary for reports and future storage."""
        data = asdict(self)
        data["relevance_score"] = round(float(data["relevance_score"]), 2)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NewsItem":
        """Build a NewsItem from a dictionary-like object."""
        return cls(
            ticker=str(data.get("ticker", "UNKNOWN")),
            title=str(data.get("title", "")),
            source=str(data.get("source", "Unknown source")),
            url=str(data.get("url", "")),
            published_at=str(data.get("published_at", "")),
            relevance_score=float(data.get("relevance_score", 0.0) or 0.0),
        )
