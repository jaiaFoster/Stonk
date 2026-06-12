"""Strategy-facing shared market data interface."""

from __future__ import annotations

from dataclasses import asdict
import json
from typing import Any, Callable

from app import config
from app.models.market_data_models import COMPLETE, MISSING_PROVIDER_FAILED, SKIPPED_PROVIDER_BUDGET
from app.providers.tradier_provider import TradierProvider
from app.services.candle_service import get_candle_history
from app.services.derived_market_metrics_service import compute_derived_metrics
from app.services.market_data_repository import MarketDataRepository
from app.services.provider_budget_service import ProviderBudget
from app.services.run_data_context_service import RunDataContext


class MarketDataHub:
    def __init__(
        self, context: RunDataContext, repository: MarketDataRepository | None = None,
        provider: TradierProvider | None = None, candle_fetcher: Callable[..., dict[str, Any]] | None = None,
        log_print: Callable[[str], None] | None = None,
    ):
        self.context = context
        self.repository = repository or MarketDataRepository()
        self.provider = provider or TradierProvider()
        self.candle_fetcher = candle_fetcher or get_candle_history
        self.log = log_print or (lambda message: None)
        self.budget = ProviderBudget(config.MARKET_DATA_MAX_PROVIDER_FETCHES_PER_RUN)
        self.log(f"MarketDataHub: initialized; sqlite_cache={'enabled' if self.repository.enabled else 'disabled'}; db={self.repository.db_path}")

    def get_quote(self, ticker: str, *, required: bool = False, strategy_id: str = "", force_refresh: bool = False) -> dict[str, Any] | None:
        return self._get(ticker, "quote", self.context.quotes, config.MARKET_DATA_QUOTE_TTL_SECONDS, lambda: (self.provider.get_quotes([ticker]) or {}).get(ticker), "tradier", required, strategy_id, force_refresh=force_refresh)

    def get_daily_candles(self, ticker: str, *, min_bars: int = 240, interval: str = "daily", required: bool = False, strategy_id: str = "", force_refresh: bool = False) -> dict[str, Any] | None:
        signature = self._signature("candles", interval=str(interval or "daily").lower(), min_bars=int(min_bars or 240))
        return self._get(ticker, "candles", self.context.candles, config.MARKET_DATA_CANDLES_TTL_SECONDS, lambda: self.candle_fetcher(ticker, log_print=self.log), "multi_provider", required, strategy_id, signature, force_refresh)

    def get_options_chain(self, ticker: str, *, min_dte: int | None = None, max_dte: int | None = None, expirations: int | None = None, required: bool = False, strategy_id: str = "", force_refresh: bool = False) -> dict[str, Any] | None:
        signature = self._signature("options_chain", min_dte=min_dte, max_dte=max_dte, expirations=int(expirations or 1))
        def fetch() -> dict[str, Any]:
            dates = self.provider.get_expirations(ticker)[: max(1, int(expirations or 1))]
            return {"expirations": dates, "chains": {date: self.provider.get_option_chain(ticker, date, greeks=True) for date in dates}}
        return self._get(ticker, "options_chain", self.context.options_chains, config.MARKET_DATA_OPTIONS_CHAIN_TTL_SECONDS, fetch, "tradier", required, strategy_id, signature, force_refresh)

    def get_earnings_event(self, ticker: str, *, lookahead_days: int = 45, required: bool = False, strategy_id: str = "", force_refresh: bool = False) -> dict[str, Any] | None:
        key = self._key(ticker, self._signature("earnings_event", lookahead_days=int(lookahead_days or 45)))
        if key in self.context.earnings_events:
            self._audit(ticker, "earnings_event", "run_cache", COMPLETE, strategy_id)
            return self.context.earnings_events[key]
        legacy = self.context.earnings_events.get(ticker.upper())
        if legacy is not None:
            self.context.earnings_events[key] = legacy
            self._audit(ticker, "earnings_event", "run_cache", COMPLETE, strategy_id)
            return legacy
        self._audit(ticker, "earnings_event", "missing", "MISSING_NOT_REQUESTED", strategy_id)
        return None

    def get_derived_metrics(self, ticker: str, *, metrics: list[str], required: bool = False, strategy_id: str = "", force_refresh: bool = False) -> dict[str, Any]:
        symbol = ticker.upper()
        key = self._key(symbol, self._signature("derived_metrics", metrics="shared_daily_v1"))
        if force_refresh or key not in self.context.derived_metrics:
            candles = self.get_daily_candles(symbol, required=required, strategy_id=strategy_id, force_refresh=force_refresh)
            benchmark = self.get_daily_candles(config.MARKET_BENCHMARK_TICKER, required=False, strategy_id="shared_benchmark", force_refresh=force_refresh)
            bars = ((candles or {}).get("payload") or candles or {}).get("bars", []) if isinstance(candles, dict) else []
            benchmark_bars = ((benchmark or {}).get("payload") or benchmark or {}).get("bars", []) if isinstance(benchmark, dict) else []
            computed = compute_derived_metrics(bars, benchmark_bars)
            self.context.derived_metrics[key] = computed
            self.repository.put(symbol, "derived_metrics", computed, "shared_candles", config.MARKET_DATA_DERIVED_METRICS_TTL_SECONDS)
        available = self.context.derived_metrics[key]
        values = available.get("metrics", available)
        return {name: values.get(name) for name in metrics} | {"reason": available.get("reason", "")}

    def ensure_requirements(self, requirement: Any, *, force_refresh: bool = False) -> dict[str, Any]:
        self.context.requirements[requirement.strategy_id] = asdict(requirement)
        for ticker in requirement.tickers:
            if requirement.needs_quote:
                self.get_quote(ticker, required=True, strategy_id=requirement.strategy_id, force_refresh=force_refresh)
            if requirement.needs_daily_candles:
                self.get_daily_candles(ticker, min_bars=requirement.min_daily_bars, required=True, strategy_id=requirement.strategy_id, force_refresh=force_refresh)
            if requirement.needs_options_chain:
                self.get_options_chain(ticker, min_dte=requirement.min_dte, max_dte=requirement.max_dte, expirations=requirement.expirations_per_ticker, required=True, strategy_id=requirement.strategy_id, force_refresh=force_refresh)
            if requirement.needs_earnings_event:
                self.get_earnings_event(ticker, lookahead_days=requirement.earnings_lookahead_days or 45, strategy_id=requirement.strategy_id, force_refresh=force_refresh)
            if requirement.required_derived_metrics:
                self.get_derived_metrics(ticker, metrics=requirement.required_derived_metrics, required=True, strategy_id=requirement.strategy_id, force_refresh=force_refresh)
        return {"strategy_id": requirement.strategy_id, "tickers": requirement.tickers}

    def seed(self, data_type: str, ticker: str, payload: Any, provider: str = "legacy_pipeline", signature: str = "default") -> None:
        if signature == "default":
            signature = {
                "candles": self._signature("candles", interval="daily", min_bars=240),
                "derived_metrics": self._signature("derived_metrics", metrics="shared_daily_v1"),
                "earnings_event": self._signature("earnings_event", lookahead_days=45),
            }.get(data_type, "default")
        target = getattr(self.context, {"quote": "quotes", "candles": "candles", "options_chain": "options_chains", "earnings_event": "earnings_events", "derived_metrics": "derived_metrics"}[data_type])
        target[self._key(ticker, signature)] = payload
        self._audit(ticker, data_type, "pipeline_seed", COMPLETE, "shared")

    def mark_skipped(self, ticker: str, strategy_id: str, state: str) -> None:
        self._audit(ticker, "requirements", "skipped", state, strategy_id)

    def _get(self, ticker: str, data_type: str, target: dict[str, Any], ttl: int, fetcher: Callable[[], Any], provider: str, required: bool, strategy_id: str, signature: str = "default", force_refresh: bool = False) -> Any:
        symbol = ticker.upper()
        key = self._key(symbol, signature)
        if not force_refresh and key in target:
            self._audit(symbol, data_type, "run_cache", COMPLETE, strategy_id)
            self.log(f"MarketDataHub: {data_type} {symbol} run_context_hit")
            return target[key]
        cached = None if force_refresh else self.repository.get(symbol, data_type, cache_key=signature)
        if cached:
            target[key] = cached.to_dict()
            self._audit(symbol, data_type, "sqlite_cache", COMPLETE, strategy_id, provider=cached.provider)
            self.log(f"MarketDataHub: {data_type} {symbol} sqlite_cache_hit")
            return target[key]
        if self.repository.provider_error_suppressed(symbol, data_type, provider):
            self._audit(symbol, data_type, "skipped", "MISSING_PROVIDER_FAILED", strategy_id, provider=provider, reason="Recent provider failure temporarily suppressed.")
            stale = self.repository.get(symbol, data_type, cache_key=signature, allow_stale=True)
            if stale:
                target[key] = stale.to_dict()
                self._audit(symbol, data_type, "sqlite_cache", "STALE_CACHE_USED", strategy_id, provider=stale.provider)
                self.log(f"MarketDataHub: {data_type} {symbol} stale_cache_fallback")
                return target[key]
            return None
        if not self.budget.consume(data_type):
            self._audit(symbol, data_type, "skipped", SKIPPED_PROVIDER_BUDGET, strategy_id)
            return None
        try:
            payload = fetcher()
            if not payload:
                raise RuntimeError("provider returned no data")
            record = self.repository.put(symbol, data_type, payload, provider, ttl, cache_key=signature)
            target[key] = record.to_dict()
            self.repository.log_fetch(self.context.run_id, symbol, data_type, provider, "ok", "provider")
            self._audit(symbol, data_type, "provider", COMPLETE, strategy_id, provider=provider)
            detail = f" bars={len(payload.get('bars', []))}" if isinstance(payload, dict) and payload.get("bars") is not None else ""
            self.log(f"MarketDataHub: {data_type} {symbol} provider_fetch {provider}{detail}")
            return target[key]
        except Exception as exc:
            self.repository.record_provider_error(symbol, data_type, provider, str(exc), config.MARKET_DATA_PROVIDER_ERROR_TTL_SECONDS)
            self.repository.log_fetch(self.context.run_id, symbol, data_type, provider, "failed", "provider", str(exc))
            self._audit(symbol, data_type, "failed", MISSING_PROVIDER_FAILED, strategy_id, provider=provider, reason=str(exc))
            stale = self.repository.get(symbol, data_type, cache_key=signature, allow_stale=True)
            if stale:
                target[key] = stale.to_dict()
                self._audit(symbol, data_type, "sqlite_cache", "STALE_CACHE_USED", strategy_id, provider=stale.provider)
                self.log(f"MarketDataHub: {data_type} {symbol} stale_cache_fallback")
                return target[key]
            return None

    def _audit(self, ticker: str, data_type: str, source: str, state: str, strategy_id: str, **details: Any) -> None:
        self.context.audit(ticker.upper(), data_type, source, state=state, strategy_id=strategy_id, **details)

    @staticmethod
    def _signature(data_type: str, **params: Any) -> str:
        clean = {key: value for key, value in params.items() if value is not None}
        return data_type + ":" + json.dumps(clean, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _key(ticker: str, signature: str) -> str:
        return f"{str(ticker).upper()}|{signature}"
