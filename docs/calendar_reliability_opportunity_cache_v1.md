# Calendar Reliability + Opportunity Cache v1

This patch makes candle/history retrieval resilient before Strategy 2.

## Reliability

- Candle requests fall back per ticker using `MARKET_DATA_PROVIDER_ORDER`.
- Supported providers are Finnhub, Tradier, and Alpha Vantage.
- `yfinance` is intentionally not installed in v1 to avoid adding a large unofficial scraping dependency; it can be added later through the configurable provider order.
- Outputs use normalized daily OHLCV bars and include candle-quality metadata.
- Calendar candidates receive selected-provider and quality fields before ranking.
- Mini-backtest eligibility requires high or medium candle quality.
- Missing candles and strategy ineligibility remain distinct statuses.

## Opportunity Cache

`calendar_opportunity_cache_service.py` stores automatically generated scanner snapshots in SQLite. Repeated natural keys update `last_seen_at` and increment `seen_count`.

The cache is an audit/history feature only. It does not accept manual entries, track active trades, or place orders.

## Configuration

```text
MARKET_DATA_PROVIDER_ORDER=finnhub,tradier,alphavantage
MARKET_DATA_CANDLE_REQUIRED_BARS=240
MARKET_DATA_CANDLE_RECENT_DAYS=7
CALENDAR_OPPORTUNITY_CACHE_ENABLED=true
CALENDAR_OPPORTUNITY_DB_PATH=/app/data/calendar_opportunities.sqlite3
CALENDAR_OPPORTUNITY_CACHE_RECENT_LIMIT=20
EARNINGS_DISCOVERY_START_DAYS=4
EARNINGS_DISCOVERY_END_DAYS=21
```

The dashboard adds a compact Calendar Reliability section with discovery counts, candle success, cache writes, backtest history count, and recent cached opportunities.
