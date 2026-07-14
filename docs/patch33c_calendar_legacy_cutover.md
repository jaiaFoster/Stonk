# Patch 33C — Calendar Legacy Path Retirement and Canonical Pipeline Cutover

## Purpose

Patch 33C cuts calendar business-state ownership down to one canonical pipeline:

```text
Raw earnings events
-> parent opportunity creation
-> lifecycle classification
-> data-requirement planning
-> structure building / quantitative facts
-> CalendarDecisionService
-> CalendarOpportunityProjectionService
-> StrategyRowRepository
-> repository-backed APIs
```

No read-only API should rebuild lifecycle, verdict, entry eligibility, or double-calendar grouping from the legacy report snapshot.

## Inventory Summary

| Symbol / module | Classification | Notes |
|---|---|---|
| `calendar_opportunity_lifecycle_adapter.py` | KEEP_AS_CANONICAL_OWNER | Event-DTE lifecycle classification and stable opportunity/structure IDs. |
| `calendar_decision_service.py` | KEEP_AS_CANONICAL_OWNER | Sole final calendar decision owner for `evaluation_state`, `trade_verdict`, `recommended_action`, `entry_allowed`. |
| `calendar_opportunity_projection_service.py` | KEEP_AS_CANONICAL_OWNER | Sole calendar row projection path. Collapses many structure attempts into one parent opportunity row. |
| `open_options_position_reconciliation_service.py` | KEEP_AS_CANONICAL_OWNER | Sole child-calendar and double-calendar parent grouping service. |
| `strategy_row_repository.py` | KEEP_AS_CANONICAL_OWNER | Persistence and repository readback. Does not own strategy math. |
| `daily_opportunity_api.py` | KEEP_AS_CANONICAL_API | Reads row store only. No legacy snapshot fallback. |
| `open_positions_api.py` | KEEP_AS_CANONICAL_API | Reads row store and delegates grouping to reconciliation service. No legacy snapshot fallback. |
| `strategy_api.py#get_strategy_rows` | KEEP_AS_CANONICAL_API | Reads StrategyRowRepository only. Legacy row reconstruction retired. |
| `calendar_ranking_service.py` | KEEP_AS_PURE_CALCULATION | Ranking no longer calls final verdict attachment. |
| `calendar_verdict_service.py` | TEMPORARY_COMPATIBILITY_ADAPTER | Finalizer functions retained for older tests/compat, but live ranking/unified engine callers removed. `evaluate_account_risk` remains pure calculation. |
| `unified_calendar_trade_engine_service.py` | TEMPORARY_COMPATIBILITY_ADAPTER | Retained as a compatibility row assembler. Projection/decision services override canonical fields. |
| `calendar_audit_service.py` | KEEP_AS_DIAGNOSTIC | Budget skips now emit `BUDGET_DEFERRED`, `DEFERRED_BUDGET`, `NOT_EVALUATED`, `NONE`, not `OPTIONABILITY`. |

No `UNKNOWN_REQUIRES_INVESTIGATION` items remain for 33C live-path ownership.

## Before Graph

```text
earnings discovery
-> quality filter
-> scanner
-> earnings calendar strategy
-> ranking
-> Calendar Verdict Service
-> Unified Calendar Trade Engine
-> strategy registry
-> report snapshot
-> Daily Opportunity / Open Positions / Strategy APIs sometimes reconstructed state
```

Parallel/fallback branches existed through legacy report summary, lifecycle checks, open-position API grouping, and strategy API snapshot row rebuilds.

## After Graph

```text
earnings discovery
-> quality filter
-> scanner
-> compatibility row assembly
-> CalendarOpportunityLifecycleAdapter
-> CalendarDecisionService
-> CalendarOpportunityProjectionService
-> StrategyRowRepository
-> Daily Opportunity API
-> OpenOptionsPositionReconciliationService
-> Open Positions API
-> Strategy Rows API
```

APIs may filter, paginate, redact, and project response fields only.

## Ownership Map

| Responsibility | Sole owner |
|---|---|
| Event parent identity | `CalendarOpportunityLifecycleAdapter` |
| Event-DTE lifecycle stage | `CalendarOpportunityLifecycleAdapter` |
| Structure/fact attempts | Existing scanner/builder path, projected as nested attempts |
| Final trade decision | `CalendarDecisionService` |
| Parent row projection | `CalendarOpportunityProjectionService` |
| Daily Opportunity calendar inclusion | Canonical row semantics in `StrategyRowRepository` + `daily_opportunity_api.py` |
| Child calendar grouping | `OpenOptionsPositionReconciliationService` |
| Double-calendar parent grouping | `OpenOptionsPositionReconciliationService` |
| Persistence | `StrategyRowRepository` |
| API truth | Repository-backed APIs |

## Deleted / Retired Paths

Deleted or retired from normal API flow:

- Daily Opportunity legacy report snapshot fallback.
- Open Positions legacy report snapshot fallback and API-side calendar grouping helpers.
- Strategy rows legacy snapshot reconstruction for built-in strategies.
- Calendar ranking final verdict attachment call.
- Unified engine live call to `build_final_calendar_verdict`.
- Misleading budget `OPTIONABILITY` classification for dev-budget not-selected names.

Retained compatibility adapters:

- `calendar_verdict_service.py`: removal ticket `TKT-CALENDAR-VERDICT-OWNER`; finalizer has no live ranking/unified-engine caller.
- `unified_calendar_trade_engine_service.py`: removal ticket `TKT-CALENDAR-LEGACY-RETIREMENT`; retained as row assembly input to canonical projection until upstream scanner emits canonical parent rows directly.

## Schema / Cache / API Decisions

- No destructive migration. Historical rows remain readable.
- New/current calendar rows rely on first-class lifecycle/opportunity columns.
- No API fallback to stale report snapshot when row store is empty. Endpoints return `source=empty`.
- Legacy report archive may still exist for debug/export, but not normal strategy row, Daily Opportunity, or Open Positions source.

## Verdict Gating

- `DISCOVERED`, `DEVELOPING`, and `SURFACED` rows produce `trade_verdict=NOT_EVALUATED`.
- Pre-window rows can preserve blockers such as high debit/liquidity, but cannot become final `FAIL`.
- `DEFERRED_BUDGET` produces `NOT_EVALUATED` and `recommended_action=NONE`.
- `ACTIONABLE` rows with unavailable structures become `BLOCKED / AVOID`.
- Only fully evaluated actionable rows can become `PASS / ENTER`.

## Parent Versus Structure Records

- One ticker + earnings event produces one parent strategy opportunity row.
- Multiple rejected expiration/strike attempts are stored under `structure_attempt_summary`.
- Structure attempts are not counted as strategy opportunities.

## Double-Calendar Proof

Fixture proof:

```text
SBUX 110 call calendar + SBUX 100 put calendar
-> child_calendars=2
-> double_calendar_parents=1
-> unmatched_child_calendars=0
```

API projection exposes both compatibility child count and canonical parent count:

```text
child_calendar_count=2
parent_double_calendar_count=1
active_parent_calendar_count=1
```

## Row Reconciliation

`CALENDAR_ROW_RECONCILIATION` now names pending write/read phases instead of emitting naked `None` before persistence. After run finalization, persisted/history/journal/API-visible counts are recomputed from finalized artifacts.

## Tests

Focused tests:

```text
tests/test_patch33c_calendar_legacy_cutover.py
tests/test_patch33b_calendar_lifecycle_finalization.py
tests/test_patch30h1_endpoint_truthing.py
tests/test_patch30gh_rowstore_consumers.py
tests/test_patch30e_payload_budget.py
tests/test_legacy_summary_hot_path_absence.py
```

Current focused result:

```text
54 passed
```

## Known Remaining Work

- Delete `unified_calendar_trade_engine_service.py` fully after scanner/strategy output natively emits canonical parent rows.
- Delete or shrink `calendar_verdict_service.py` finalizer functions after older compatibility tests are migrated.
- Move report HTML calendar sections to canonical row projections where they still display legacy labels.
- Verify Railway post-deploy endpoint packet for data-confidence hard failures and live SBUX grouping.
