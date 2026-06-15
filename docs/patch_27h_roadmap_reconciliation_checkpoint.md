# Patch 27H - Roadmap Reconciliation and Slimming Verification Checkpoint

Checked against merged `main` commit `87b54df` and the deployed Railway app on
2026-06-15 UTC.

This checkpoint changes no runtime behavior. It reconciles the merged Patch
27C-27G architecture before more feature work.

## Verified Capabilities

| Capability | Result | Evidence |
| --- | --- | --- |
| Hot shell default | Pass | Cached shell HTML is 39,199 bytes and includes Daily Opportunity. |
| Full report | Pass | Cached full HTML is 661,087 bytes and includes Forward Factor. |
| Report snapshot slimming | Pass | Hot summary is 347,707 bytes; compressed full summary is 156,127 bytes. |
| On-demand snapshot/detail endpoints | Pass | Manifest, latest, summary, full, provider, strategy, and raw-provider reads returned successfully. |
| Run timeout watchdog | Pass | Lock diagnostics report `held=false` and `retry_safe=true`; regression tests cover stale lock recovery and late-worker result discard. |
| Tradier snapshot compaction | Pass with limitation | Raw provider snapshot is archived separately and compressed. Compact Tradier snapshot is still large because most size is strategy/cache data rather than recognized raw provider collections. |
| Diagnostics endpoints | Pass | Status, latest profiles, latest manifest, and feature health returned successfully. |
| Cached dashboard provider-free | Pass | Root shell/full routes load the latest successful stored report. |
| Read-only diagnostics provider-free | Pass | Snapshot and diagnostic responses report `provider_calls_triggered=false`. |
| Forward Factor dry-run exclusion | Pass | Feature health confirms FF visible, dry-run enabled, and excluded from Daily Opportunity. |
| Trade execution | Pass | Feature health reports `trade_execution_enabled=false`. |

## Current Architecture Map

### Hot state

- Latest successful report hot summary.
- Cached shell dashboard facts: positions, recommendations, Daily Opportunity,
  compact lifecycle/portfolio/strategy summaries, provider status, profiles,
  and a bounded log tail.
- Latest run manifest.
- Short-lived market-data cache and run-lock state.

### Compacted state

- Stored full report summary replaces recognized raw provider collections such
  as chains, contracts, bars, and raw payloads with collection summaries.
- Full payload, full summary, and raw provider archive are compressed.
- Hot strategy results keep counts and only bounded rows where required.

### Separate dormant state

- Raw Tradier/provider snapshot is stored in `raw_provider_blob`.
- Full summary and full HTML payload remain compressed compatibility blobs.
- Raw provider detail is loaded only by explicit
  `/api/dev/snapshot/detail/provider_raw`.

### Endpoint weight classes

| Class | Endpoint / view | Current bytes | Provider calls |
| --- | --- | ---: | --- |
| Health | `/health` | 2 | None |
| Compact manifest | `/api/dev/snapshot?mode=manifest_only` | 1,053 | None |
| Compact diagnostics | `/api/dev/latest-profiles` | 4,325 | None |
| Compact shell | `/?view=shell` | 39,199 | None |
| Latest/summary snapshot | `/api/dev/snapshot?mode=latest` | 49,879 | None |
| Full developer snapshot | `/api/dev/snapshot?mode=full` | 333,004 | None |
| Full HTML report | `/?view=full` | 661,087 | None |
| Explicit raw provider detail | `/api/dev/snapshot/detail/provider_raw` | 2,915,261 | None |

All protected reads above used cached state. Snapshot/detail responses explicitly
reported `provider_calls_triggered=false` and `read_only=true`.

## Current Profiles

Latest successful source run: `563f07237a704bf7b4a162a3e89b8bc7`

Report quality: `SUCCESS_COMPLETE`

### Runtime

- Pipeline runtime: 48,746 ms.
- Run manifest runtime: 49,715 ms.
- Slowest phase: positions, 17,619 ms.
- Next slowest: skew momentum vertical, 6,058 ms.
- Provider fetch count: 239.

### Report snapshot sizes

- Shell HTML: 39,199 bytes.
- Full HTML: 661,087 bytes.
- Hot summary: 347,707 bytes.
- Full compact summary: 3,129,602 bytes.
- Compressed full summary: 156,127 bytes.
- Full payload: 123,061 bytes.
- Compressed full payload: 13,569 bytes.
- Raw provider snapshot: 2,917,436 bytes.
- Compressed raw provider archive: 142,179 bytes.
- Compact Tradier snapshot: 2,742,398 bytes.
- Profiled report snapshot save size: 659,282 bytes.

### Provider compaction before/after

- Raw Tradier snapshot: 2,916,001 bytes.
- Compact Tradier snapshot: 2,740,034 bytes.
- Saved: 175,967 bytes.
- Reduction: 6.0%.

Compaction storage boundary works, but the compact object remains oversized
because dominant sections are strategy/cache output rather than raw chain keys.

### Largest current payload sections

1. `_calendar_opportunity_cache`: 1,554,085 bytes.
2. `_strategy_results`: 318,166 bytes.
3. `_forward_factor_strategy`: 189,055 bytes.
4. `_skew_momentum_vertical_strategy`: 157,391 bytes.
5. `_skew_momentum_vertical_cache`: 147,595 bytes.

`tradier_snapshot` remains the largest top-level payload section at 2,916,001
bytes.

### Storage

- Market-data SQLite size: 14,155,776 bytes.
- `market_data_fetch_log`: 436 rows.
- `market_data_records`: 89 rows.
- `data_coverage_runs`: 25 rows.
- Current dry-run pruning report: zero rows eligible.

## Timeout Recovery Verification

- Stale lock detection exists and rotates the lock after the configured timeout.
- Diagnostics expose lock state, timeout reason, and retry safety.
- Timed-out worker results are discarded.
- A late worker cannot overwrite the replacement active job.
- Current deployed lock state: not held and retry safe.

## Remaining Bloat and Multi-User Blockers

1. `_calendar_opportunity_cache` dominates the compact provider snapshot.
2. Strategy results and duplicate strategy-specific sections remain large.
3. Full compatibility loads intentionally rehydrate the 2.9 MB raw provider
   archive.
4. Raw-provider detail is truly on demand, but full mode still deserializes it
   by design.
5. Hot summary is much smaller than full state but remains 347 KB, mainly from
   shell-driving detail and duplicated summaries.
6. Provider fetch count and positions phase cost should be watched before
   multi-user multiplies run load.
7. Tenant boundaries, per-user storage ownership, and usage evidence do not yet
   exist.

## Reconciliation Decision

Patch 27C-27G achieved the intended hot-shell, cached-read, detail-endpoint,
watchdog, and separate-provider-archive architecture. No compatibility layer
should be removed yet.

Recommended next patch after source-of-truth approval: Patch 27I Usage and
Storage Telemetry Foundation. It should measure section/detail use before any
feature axing or additional payload pruning.

## Regression Result

- `PYTHONPATH=. .venv/bin/pytest -q`
- 184 tests passed and 3 subtests passed.
- No runtime source files changed by this checkpoint.

## Scope Confirmation

- No strategy behavior changed.
- No UI expanded.
- No multi-user work added.
- No trade execution added.
- No provider calls made by checkpoint read-only probes.
- Forward Factor remains dry run and excluded from Daily Opportunity.
