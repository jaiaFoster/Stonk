# Pre-30A Strategy Unification Readiness

**Prepared:** ASA Patch 29.8 — Final Pre-Unification Hardening  
**Next milestone:** 30A — Universal Strategy Row Schema + Strategy Spec Registry

---

## Overview

This document tracks the state of all four active strategies against the requirements for 30A unification. Each strategy must expose a stable, normalized row schema before the unification patch can begin. Patch 29.8 completes that normalization layer.

---

## Normalized Field Contract (all strategies)

Every strategy row must carry these fields after Patch 29.8:

| Field | Type | Notes |
|---|---|---|
| `strategy_id` | str | Stable ID (`earnings_calendar`, `skew_momentum_vertical`, `forward_factor_calendar`, `stock_momentum`) |
| `friendly_verdict` | str | Human-readable verdict for public display |
| `primary_reason` | str | Most relevant reason string for the verdict |
| `daily_opportunity_reason` | str | Why this strategy is/isn't eligible for Daily Opportunity |
| `gates` | list[dict] | Universal gate schema: `{name, status, detail}` |

Gate `status` values: `pass`, `watch`, `fail`, `skipped`, `dry_run`, `not_applicable`, `unknown`

---

## Strategy Readiness Checklist

### 1. Earnings Calendar (`earnings_calendar`)

- [x] `strategy_id` set
- [x] `calendar_entry_allowed` (True for CANDIDATE/URGENT actions)
- [x] `liquidity_status`, `spread_status`, `debit_status` computed from config thresholds
- [x] `iv_relationship_status` (favorable/neutral/unfavorable/unavailable)
- [x] `structure_status` (earnings_relation value)
- [x] `earnings_date`, `earnings_time`, `earnings_source`, `earnings_sources_seen` at top level
- [x] `expiration_pair_diagnostics` forwarded
- [x] `friendly_verdict`, `primary_reason`, `daily_opportunity_reason`, `gates` via normalization service
- [x] Earnings trust: single-source = warning (not block), conflict = block (unchanged)

### 2. Skew Momentum Vertical (`skew_momentum_vertical`)

- [x] `strategy_id` set
- [x] `momentum_status` (confirmed/unavailable/not_confirmed)
- [x] `skew_status` (pass/fail)
- [x] `spread_width` from `possible_spread.width`
- [x] `estimated_debit` from `conservative_debit`
- [x] `structure_status` (complete/watch/fail)
- [x] `atm_iv`
- [x] `friendly_verdict`, `primary_reason`, `daily_opportunity_reason`, `gates` via normalization service

### 3. Forward Factor Calendar (`forward_factor_calendar`)

- [x] `strategy_id` set
- [x] `source_qualified`, `chain_approved`, `structure_built`, `diagnostic_model`, `cheap_eligible` promoted to top level
- [x] `earnings_contaminated`, `source_qualification` at top level
- [x] `front_iv`, `back_iv`, `ex_earnings_iv` aliases
- [x] `dry_run = bool(FORWARD_FACTOR_DRY_RUN)` — always True in current config
- [x] `can_enter_daily_opportunity = False` (FF excluded from Daily Opportunity)
- [x] `can_trade_live = False` (FF dry-run only — no live trading)
- [x] `friendly_verdict`, `primary_reason`, `daily_opportunity_reason`, `gates` via normalization service
- [x] FORWARD_FACTOR_DRY_RUN=true preserved — no live trading path added

### 4. Stock Momentum (`stock_momentum`)

- [x] `strategy_id` set
- [x] `momentum_score` (alias for `score`)
- [x] `relative_strength` (6M percentile)
- [x] `trend_status` (clean/partial/broken based on SMA50/SMA200)
- [x] `volume_status` (adequate/low/unavailable based on 30d avg volume ≥ 100K)
- [x] `price_action_status` (positive/mixed/negative based on 3M/6M returns)
- [x] `risk_status` (elevated/normal)
- [x] `friendly_verdict`, `primary_reason`, `daily_opportunity_reason`, `gates` via normalization service

---

## Infrastructure Readiness

### Payload Safety (TKT-038)

- [x] Tiered budget thresholds: healthy ≤750KB, watch 750KB–1MB, warning 1MB–2MB, critical >2MB
- [x] `summary_payload_status` in payload profile
- [x] `largest_strategy_rows` (top 5 by serialized size)
- [x] `build_payload_warnings()` emits tiered warnings with contributor context
- [x] Provider payload compaction service integrated

### Serialization Contract

- [x] `_STRATEGY_SUMMARY_EXCLUDE` updated — excludes `payload`, `scenario_grid`, `candidate_selection_audit`
- [x] Full exclude list: `observation_history`, `ff_journal`, `raw_chain_data`, `canonical_opportunities`, `raw_json`, `raw_provider_payload`, `full_chain`, `options_chain`, `chain_snapshot`, `provider_payload`, `debug_trace`, `lifecycle_log_full`, `payload`, `scenario_grid`, `candidate_selection_audit`

### Positions / Lifecycle

- [x] `lifecycle_overlay_status` added to positions payload
  - `"applied"` — lifecycle data found and overlaid
  - `"unavailable"` — no lifecycle snapshot
  - `"reconciled"` — DB and lifecycle disagree (DB reported no calendars; lifecycle found active ones)
- [x] `positions_lifecycle_reconciliation_notes` emitted when status is `"reconciled"`

### Public Screener

- [x] `source_iv_status` mapped through `public_ff_source_label()` — `SOURCE_UNSPECIFIED` and other raw enum values no longer reach the public screener
- [x] No internal dev-cap language exposed (`DEV CAP`, `STRATEGY CAP`, `PROVIDER BUDGET`)

### Normalization Layer

- [x] `app/services/strategy_row_normalization_service.py` — thin shared service
- [x] Called at the end of every strategy's verdict/evaluation function
- [x] No external I/O, no DB calls — pure dict mutation

---

## Safety Invariants (CAVEMAN MODE — must remain true after 30A)

| Invariant | Status |
|---|---|
| `FORWARD_FACTOR_DRY_RUN=true` | Preserved — not touched |
| FF excluded from Daily Opportunity | Preserved — `can_enter_daily_opportunity=False` hard-set |
| `trade_execution_enabled=false` | Not touched |
| Read-only endpoints | Not touched — `provider_calls_triggered=False` on screener |
| Public `/screener` exposes no broker/account/private data | Preserved |
| Single-source earnings = warning | Preserved |
| Earnings source conflict = block | Preserved |
| No order placement | No changes to any trade execution path |
| No position mutation | No changes to any position write path |

---

## What 30A Should Do (Not Started)

1. **Strategy Spec Registry** — a single registry mapping `strategy_id` → spec (display name, gate definitions, row schema, Daily Opportunity eligibility)
2. **Universal Row Schema** — all strategy rows validated against the spec at evaluation time
3. **Unified screener endpoint** — serve all four strategies from a single endpoint with consistent shape
4. **Gate schema enforcement** — gate names standardized per spec, status values validated at write time
5. **`daily_opportunity_reason` from spec** — read from registry instead of normalization service constants

---

## Known Blockers for 30A

- None blocking. All four strategies have the normalized field contract in place.
- FF remains dry-run — no architecture change needed; the `dry_run=True` field is sufficient for 30A gate rendering.
- Stock momentum has no options chain — `gates` will always be empty for that strategy; this is by design.

---

*Last updated: Patch 29.8 — ASA Final Pre-Unification Hardening*
