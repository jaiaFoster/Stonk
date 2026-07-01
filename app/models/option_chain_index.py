"""Provider-free index over normalized option-chain rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class IndexedOption:
    ticker: str
    expiration: str
    option_type: str
    strike: float
    dte: int | None
    bid: float | None
    ask: float | None
    mid: float | None
    iv: float | None
    delta: float | None
    open_interest: int | None
    volume: int | None
    raw: dict[str, Any]

    @property
    def spread_pct(self) -> float | None:
        if self.bid is None or self.ask is None or self.mid in (None, 0):
            return None
        return (self.ask - self.bid) / self.mid * 100


class OptionChainIndex:
    """Read-only lookup structure. Never calls providers."""

    def __init__(self, ticker: str, options: list[IndexedOption], as_of_date: date):
        self.ticker = ticker.upper()
        self.options = options
        self.as_of_date = as_of_date
        self._index: dict[str, dict[str, dict[float, IndexedOption]]] = {}
        for option in options:
            self._index.setdefault(option.expiration, {}).setdefault(option.option_type, {})[option.strike] = option

    @classmethod
    def from_chain_payload(cls, ticker: str, payload: Any, as_of_date: date | str) -> OptionChainIndex:
        as_of = _date(as_of_date) or date.today()
        rows = cls._rows(payload)
        options: list[IndexedOption] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            expiration = str(row.get("expiration") or row.get("expiration_date") or row.get("expiry") or "")[:10]
            option_type = str(row.get("option_type") or row.get("type") or "").lower()
            strike = _float(row.get("strike"))
            if not expiration or option_type not in {"call", "put"} or strike is None:
                continue
            expiration_date = _date(expiration)
            dte = (expiration_date - as_of).days if expiration_date else None
            bid, ask = _float(row.get("bid")), _float(row.get("ask"))
            mid = _float(row.get("mid") or row.get("mark"))
            if mid is None and bid is not None and ask is not None:
                mid = (bid + ask) / 2
            greeks = row.get("greeks") if isinstance(row.get("greeks"), dict) else {}
            options.append(IndexedOption(
                ticker=ticker.upper(), expiration=expiration, option_type=option_type, strike=strike, dte=dte,
                bid=bid, ask=ask, mid=mid,
                iv=_float(row.get("iv") or row.get("implied_volatility") or greeks.get("mid_iv")),
                delta=_float(row.get("delta") if row.get("delta") is not None else greeks.get("delta")),
                open_interest=_int(row.get("open_interest")), volume=_int(row.get("volume")), raw=dict(row),
            ))
        return cls(ticker, options, as_of)

    @staticmethod
    def _rows(payload: Any) -> list[Any]:
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            return []
        for key in ("options", "option", "items", "rows", "chain"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                nested = value.get("option")
                if isinstance(nested, list):
                    return nested
        return []

    @property
    def expirations(self) -> list[str]:
        return sorted(self._index, key=lambda exp: (_date(exp) or date.max))

    def expirations_between(self, min_dte: int, max_dte: int) -> list[str]:
        return [exp for exp in self.expirations if (dte := self.expiration_dte(exp)) is not None and min_dte <= dte <= max_dte]

    def expiration_dte(self, expiration: str) -> int | None:
        parsed = _date(expiration)
        return (parsed - self.as_of_date).days if parsed else None

    def available_strikes(self, expiration: str, option_type: str) -> list[float]:
        return sorted(self._index.get(expiration, {}).get(option_type.lower(), {}))

    def option(self, expiration: str, option_type: str, strike: float) -> IndexedOption | None:
        return self._index.get(expiration, {}).get(option_type.lower(), {}).get(float(strike))

    def nearest_strike(self, expiration: str, option_type: str, target_strike: float) -> IndexedOption | None:
        strikes = self.available_strikes(expiration, option_type)
        return self.option(expiration, option_type, min(strikes, key=lambda value: abs(value - target_strike))) if strikes else None

    def nearest_delta(self, expiration: str, option_type: str, target_delta: float) -> IndexedOption | None:
        choices = [item for item in self._index.get(expiration, {}).get(option_type.lower(), {}).values() if item.delta is not None]
        return min(choices, key=lambda item: abs(abs(item.delta or 0) - abs(target_delta))) if choices else None

    def same_strike_pair(self, front_exp: str, back_exp: str, option_type: str, strike: float) -> tuple[IndexedOption, IndexedOption] | None:
        front = self.option(front_exp, option_type, strike)
        back = self.option(back_exp, option_type, strike)
        return (front, back) if front and back else None

    def front_back_pair_candidates(self, min_front_dte: int, max_front_dte: int, min_back_dte: int, max_back_dte: int) -> list[tuple[str, str]]:
        fronts = self.expirations_between(min_front_dte, max_front_dte)
        backs = self.expirations_between(min_back_dte, max_back_dte)
        return [(front, back) for front in fronts for back in backs if back > front]

    def chain_quality_summary(self) -> dict[str, Any]:
        count = len(self.options)
        return {
            "ticker": self.ticker,
            "option_count": count,
            "expiration_count": len(self.expirations),
            "has_quotes": any(item.bid is not None and item.ask is not None for item in self.options),
            "has_iv": any(item.iv is not None for item in self.options),
            "has_greeks": any(item.delta is not None for item in self.options),
            "has_open_interest": any(item.open_interest is not None for item in self.options),
            "empty": count == 0,
        }


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
