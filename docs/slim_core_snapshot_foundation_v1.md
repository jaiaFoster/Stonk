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

## Patch 27D snapshot slimming

Completed report snapshots now use a compatibility-preserving two-part record:

- `summary_json` is compact hot state used by the default shell, latest profiles,
  feature health, and lightweight Advisor reads.
- Full report detail and the full advisor payload are compressed and dormant
  until `?view=full` or a full developer snapshot explicitly requests them.

Existing uncompressed snapshots remain readable. New runs do not permanently
delete full report detail, and read-only shell/profile requests do not fetch or
decompress full-detail blobs.

Snapshot diagnostics expose hot-summary, original-full, and compressed-full
sizes so later patches can measure storage improvements safely.

## Patch 27E on-demand detail boundary

Hot snapshot state removes duplicate top-level pipeline and strategy payloads
and keeps only bounded shell-driving rows. Dormant compressed full state remains
the compatibility source for the full dashboard and full developer snapshot.

Explicit token-protected detail reads use:

`/api/dev/snapshot/detail/<section>`

Whitelisted sections are daily opportunity, data coverage, lifecycle, pipeline,
portfolio, providers, all strategies, or one strategy selected with
`strategy_id`. These reads never trigger providers and every snapshot/detail
response includes `provider_calls_triggered=false` and `read_only=true`.
