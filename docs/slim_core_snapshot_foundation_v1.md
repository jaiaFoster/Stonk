# Slim Core and Pull-On-Demand Snapshot Foundation v1

Patch 27A separates small hot server state from future cold local-vault data.

## Hot server state

- Latest successful report snapshot for dashboard fallback
- Bounded report logs
- Small run manifests
- Runtime, payload-size, provider-coverage, and storage diagnostics
- Short-lived shared market-data cache

## Pull-on-demand snapshots

`/api/dev/snapshot` reads stored state and does not call providers in `latest`,
`summary`, or `manifest_only` modes. `fresh` remains disabled unless explicitly
enabled.

Snapshots are recursively redacted. Raw provider payloads, secrets, full option
chains, and unbounded logs are excluded by default.

## Dormant data policy

Heavy debug payloads, full historical records, backtest-ready exports, and
long-term archives belong in a future local developer/user vault. Railway is
not treated as the permanent financial-data archive.

## Storage hygiene

Storage profiling reports SQLite size, table counts, and dry-run pruning counts.
Patch 27A does not delete cached market data.
