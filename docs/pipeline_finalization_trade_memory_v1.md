# Pipeline Finalization + Trade Memory v1

## Purpose

This patch fixes the report finalization order and adds persistent manual calendar-trade storage.

## Major changes

- Uses the structured pipeline orchestration from the maintenance refactor.
- Ensures the report payload is formatted after portfolio scoring, portfolio gap, stock momentum, unified calendar engine, open options, lifecycle checks, and trade memory have all run.
- Adds SQLite-backed Trade Memory v1.
- Adds `/trades`, `/trades/add`, `/trades/close`, and `/trades/delete` routes protected by `RUN_TOKEN`.
- Adds `TRADE_MEMORY_*` config values.
- Adds a Trade Memory section to the report.
- Updates lifecycle checks to use exact entry debit from trade memory when a stored trade matches a detected Tradier calendar.
- Updates README with Railway Volume setup.

## Railway volume setup

Recommended setup:

1. Add a Railway Volume to the app service.
2. Mount it at `/app/data`.
3. Set variables:

```text
TRADE_MEMORY_ENABLED=true
DATA_DIR=/app/data
TRADE_MEMORY_DB_PATH=/app/data/trade_memory.sqlite3
TRADE_MEMORY_DEFAULT_PROFIT_TARGET_PCT=50
TRADE_MEMORY_DEFAULT_MAX_LOSS_PCT=-35
```

Railway also exposes `RAILWAY_VOLUME_MOUNT_PATH` automatically. This app can use it, but explicit `DATA_DIR` and `TRADE_MEMORY_DB_PATH` make the path clear.

## How to add a trade

Browser form:

```text
/trades?token=YOUR_RUN_TOKEN
```

Direct URL example:

```text
/trades/add?token=YOUR_RUN_TOKEN&ticker=PDD&option_type=call&strike=120&short_expiration=2026-05-29&long_expiration=2026-06-26&quantity=1&entry_debit=2.10&entry_underlying_price=121.50&notes=earnings-calendar
```

## How to close a trade

```text
/trades/close?token=YOUR_RUN_TOKEN&id=1&close_value=3.15&notes=took-profit
```

## Notes

This is still advisory and read-only with respect to brokers. It does not place or close orders.
