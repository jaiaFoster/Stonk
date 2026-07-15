"""
app/services/options_chain_cache.py — Last-known-good options chain cache.

Patch 33B: Stale cache provider implementing OptionsDataProvider.
Returns cached chains only — no network calls, no live data.
Permits analysis but never new entry (FreshnessState.STALE_CACHE).
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Any, Generator

from app import config
from app.models.market_data_contracts import (
    FreshnessState,
    NormalizedOptionContract,
    NormalizedOptionsChain,
    OptionsDataRequirements,
    ProviderAttempt,
    ProviderOutcome,
)
from app.models.provider_capabilities import LAST_KNOWN_GOOD_CAPABILITIES, ProviderCapabilities
from app.providers.options_data_provider import (
    ProviderEmptyChainError,
    ProviderNotConfiguredError,
)

PROVIDER_ID = "last_known_good_cache"


class LastKnownGoodCacheProvider:
    """
    Stale-cache fallback implementing OptionsDataProvider.
    Reads from the market_data SQLite cache populated by prior successful fetches.
    freshness_state is always STALE_CACHE — never LIVE_*.
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or config.MARKET_DATA_DB_PATH

    @property
    def provider_id(self) -> str:
        return PROVIDER_ID

    @property
    def capabilities(self) -> ProviderCapabilities:
        return LAST_KNOWN_GOOD_CAPABILITIES

    @property
    def is_configured(self) -> bool:
        import os
        return bool(self._db_path and os.path.exists(self._db_path))

    def get_options_chain(
        self,
        symbol: str,
        requirements: OptionsDataRequirements,
        expirations: list[str] | None = None,
    ) -> NormalizedOptionsChain:
        symbol = str(symbol or "").upper().strip()
        if not symbol:
            raise ProviderEmptyChainError("symbol is required", provider_id=PROVIDER_ID)

        if not self.is_configured:
            raise ProviderNotConfiguredError(
                f"Cache DB not found at {self._db_path}", provider_id=PROVIDER_ID
            )

        start_ms = int(time.time() * 1000)
        retrieved_at = datetime.now(timezone.utc)

        cached = self._load_from_cache(symbol, expirations)
        duration_ms = int(time.time() * 1000) - start_ms

        if not cached:
            raise ProviderEmptyChainError(
                f"No cached chain for {symbol}", provider_id=PROVIDER_ID
            )

        contracts, cache_ts = cached
        exp_dates = sorted({c.expiration for c in contracts})

        attempt = ProviderAttempt(
            provider_id=PROVIDER_ID,
            outcome=ProviderOutcome.SUCCESS,
            duration_ms=duration_ms,
            freshness_state=FreshnessState.STALE_CACHE,
            contract_count=len(contracts),
        )

        age_warning = []
        if cache_ts:
            age_seconds = (retrieved_at - cache_ts).total_seconds()
            if age_seconds > config.OPTIONS_CHAIN_STALE_TTL_SECONDS:
                age_warning.append(
                    f"Cache is {int(age_seconds / 3600)}h old — treat as reference only"
                )

        return NormalizedOptionsChain(
            underlying=symbol,
            expirations=exp_dates,
            contracts=contracts,
            provider_id=PROVIDER_ID,
            provider_attempts=[attempt],
            retrieved_at=retrieved_at,
            quote_timestamp=cache_ts,
            freshness_state=FreshnessState.STALE_CACHE,
            is_live=False,
            is_complete=True,
            validation_errors=[],
            validation_warnings=age_warning,
        )

    def store_chain(self, chain: NormalizedOptionsChain) -> None:
        """Persist a live chain for future stale-cache retrieval."""
        if not self._db_path:
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS options_chain_cache (
                        underlying TEXT NOT NULL,
                        cached_at TEXT NOT NULL,
                        freshness_state TEXT NOT NULL,
                        payload TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    "DELETE FROM options_chain_cache WHERE underlying = ?",
                    (chain.underlying,),
                )
                payload = json.dumps(chain.to_legacy_chain_set())
                conn.execute(
                    "INSERT INTO options_chain_cache (underlying, cached_at, freshness_state, payload) VALUES (?,?,?,?)",
                    (
                        chain.underlying,
                        chain.retrieved_at.isoformat(),
                        chain.freshness_state,
                        payload,
                    ),
                )
        except Exception:
            pass  # cache write failure is non-fatal

    def health_check(self) -> dict[str, Any]:
        import os
        exists = bool(self._db_path and os.path.exists(self._db_path))
        result: dict[str, Any] = {
            "provider_id": PROVIDER_ID,
            "configured": exists,
        }
        if not exists:
            result["status"] = "NOT_CONFIGURED"
            result["message"] = f"DB not found: {self._db_path}"
            return result
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT COUNT(*) FROM options_chain_cache"
                ).fetchone()
            result["status"] = "OK"
            result["cached_symbols"] = rows[0] if rows else 0
        except Exception as e:
            result["status"] = "ERROR"
            result["message"] = str(e)[:200]
        return result

    # ── Internal ───────────────────────────────────────────────────────────

    def _load_from_cache(
        self,
        symbol: str,
        expirations: list[str] | None,
    ) -> tuple[list[NormalizedOptionContract], datetime | None] | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT payload, cached_at FROM options_chain_cache WHERE underlying = ?",
                    (symbol,),
                ).fetchone()
        except Exception:
            return None

        if not row:
            return None

        payload_json, cached_at_str = row
        try:
            payload = json.loads(payload_json)
        except (json.JSONDecodeError, TypeError):
            return None

        cache_ts: datetime | None = None
        try:
            cache_ts = datetime.fromisoformat(cached_at_str).replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pass

        contracts = _contracts_from_legacy_payload(payload, symbol)
        if not contracts:
            return None

        if expirations:
            target_set = set(expirations)
            contracts = [c for c in contracts if c.expiration.isoformat() in target_set]

        return contracts, cache_ts

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self._db_path, timeout=5)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def _contracts_from_legacy_payload(
    payload: dict[str, Any], underlying: str
) -> list[NormalizedOptionContract]:
    contracts: list[NormalizedOptionContract] = []
    chains_by_exp: dict[str, list[dict]] = payload.get("chains_by_expiration") or payload.get("chains") or {}

    for exp_str, rows in chains_by_exp.items():
        try:
            exp_date = date.fromisoformat(exp_str[:10])
        except ValueError:
            continue

        for row in (rows or []):
            if not isinstance(row, dict):
                continue
            option_type = str(row.get("option_type") or "").lower()
            if option_type not in {"call", "put"}:
                continue
            strike = _float_or_none(row.get("strike"))
            if strike is None:
                continue
            bid = _float_or_none(row.get("bid"))
            ask = _float_or_none(row.get("ask"))
            mid = _float_or_none(row.get("mid"))
            if mid is None and bid is not None and ask is not None:
                mid = (bid + ask) / 2.0

            contracts.append(
                NormalizedOptionContract(
                    symbol=str(row.get("symbol") or ""),
                    underlying=underlying,
                    expiration=exp_date,
                    strike=strike,
                    option_type=option_type,  # type: ignore[arg-type]
                    bid=bid,
                    ask=ask,
                    mid=mid,
                    last=_float_or_none(row.get("last")),
                    volume=_int_or_none(row.get("volume")),
                    open_interest=_int_or_none(row.get("open_interest")),
                    implied_volatility=_float_or_none(row.get("iv")),
                    delta=_float_or_none(row.get("delta")),
                    gamma=_float_or_none(row.get("gamma")),
                    theta=_float_or_none(row.get("theta")),
                    vega=_float_or_none(row.get("vega")),
                    rho=_float_or_none(row.get("rho")),
                    underlying_price=_float_or_none(row.get("underlying_price")),
                    quote_timestamp=None,
                    provider_id=PROVIDER_ID,
                    data_delay_seconds=None,
                    freshness_state=FreshnessState.STALE_CACHE,
                    raw=row,
                )
            )

    return contracts


def _float_or_none(v: Any) -> float | None:
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _int_or_none(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
