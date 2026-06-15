# Patch 27P - Post-Slimming Stability and Dashboard Contract Checkpoint

Validated against the live Patch 27O Railway deployment on 2026-06-15 UTC.

This checkpoint changes no runtime behavior. It freezes the accepted
post-slimming budget and protects the user-facing dashboard, cached snapshot,
explicit detail, and Forward Factor dry-run contracts.

## Accepted Live Baseline

| Metric | Accepted Patch 27O value | Guardrail intent |
| --- | ---: | --- |
| Hot summary | 99,420 bytes | Keep default cached state comfortably below 250 KB. |
| Compact Tradier snapshot | 65,941 bytes | Keep operational provider state below 250 KB. |
| Compact full summary | 110,382 bytes | Keep compact compatibility state below 250 KB. |
| Compressed full summary | 16,715 bytes | Keep dormant compressed detail below 100 KB. |
| Report snapshot save | 259,796 bytes | Keep bounded snapshot storage below 500 KB. |
| Raw provider archive | about 2.84 MB | Intentionally dormant; explicit read-only access only. |

Guardrails are intentionally non-brittle. They protect broad budget classes,
not exact byte values.

## Dashboard Contract

The default cached shell must retain:

- Portfolio Status
- Active Calendar Lifecycle
- Daily Opportunity
- Top Actionable Adds
- Urgent Risk Review
- Strategy Summary with Forward Factor dry-run status
- Open Full Report
- Dormant-detail messaging

The full report remains an explicit compatibility path and retains full
strategy, lifecycle, holdings, export, and Monitor / Debug sections.

## Snapshot and Detail Contract

- `latest`, `summary`, and `full` developer snapshots remain read-only and
  provider-free.
- Latest snapshots expose compact portfolio, lifecycle, Daily Opportunity,
  strategy, profile, and available-detail metadata.
- Full snapshots preserve requested strategy rows.
- Explicit detail endpoints preserve Daily Opportunity, lifecycle, portfolio,
  provider, strategy, pipeline, and raw-provider detail.
- Raw provider detail remains a separate dormant compatibility archive.

## Strategy and Safety Contract

- Daily Opportunity remains populated from stored report state.
- Forward Factor remains visible and dry run.
- Forward Factor remains excluded from Daily Opportunity.
- No provider fetch, strategy, ranking, lifecycle, or execution behavior changes.
- Trade execution remains disabled.
