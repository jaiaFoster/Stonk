# Patch 33C.1 — Calendar Legacy Pipeline Deletion and Canonical Cutover

## Purpose

Patch 33C.1 removes the remaining live legacy Earnings Calendar business pipeline.

Canonical path:

```text
earnings discovery
-> parent opportunity creation
-> lifecycle classification
-> structure/fact calculation
-> CalendarDecisionService
-> CalendarOpportunityProjectionService
-> StrategyRowRepository
-> repository-backed APIs/UI
```

No normal API or runtime service should rebuild calendar lifecycle, verdict, entry eligibility, or open-position grouping from legacy report objects.

## Before Execution Graph

```text
earnings discovery
-> quality filter
-> scanner
-> earnings calendar strategy
-> ranking
-> Unified Calendar Trade Engine
-> legacy verdict finalizer
-> strategy registry
-> row store / report / APIs
```

This allowed one scanned event to expand into multiple strategy rows and allowed pre-window monitor rows to receive final PASS/FAIL semantics.

## After Execution Graph

```text
earnings discovery
-> quality/scanner/ranking facts
-> CalendarOpportunityProjectionService
   -> one parent opportunity row per ticker + earnings date
   -> nested structure_attempt_summary
   -> open-position child rows
-> CalendarDecisionService
-> StrategyRowRepository
-> Daily Opportunity / Open Positions / Strategy Rows APIs
```

## Canonical Ownership Map

| Responsibility | Owner |
|---|---|
| Parent opportunity identity | `CalendarOpportunityLifecycleAdapter` |
| Event-DTE lifecycle stage | `CalendarOpportunityLifecycleAdapter` |
| Final decision fields | `CalendarDecisionService` |
| Parent row projection | `CalendarOpportunityProjectionService` |
| Structure-attempt nesting | `CalendarOpportunityProjectionService` |
| Account-risk facts | `calendar_risk_fact_service.py` |
| Trade-type facts | `calendar_trade_type_service.py` |
| Child calendar grouping | `OpenOptionsPositionReconciliationService` |
| Double-calendar parent grouping | `OpenOptionsPositionReconciliationService` |
| Persistence | `StrategyRowRepository` |
| API truth | Repository-backed API routes |

## Delete-Versus-Retain Review

| Component | Action | Reason |
|---|---|---|
| Unified Calendar Trade Engine | DELETE | Duplicate row, action, and verdict owner |
| `UNIFIED_CALENDAR_ENGINE_ENABLED` | DELETE | Canonical architecture is not feature-flagged |
| Legacy Calendar Verdict Service | DELETE | Final verdict owner competed with decision service |
| Account-risk calculation | RETAIN AS PURE FACTS | Moved to `calendar_risk_fact_service.py` |
| Trade-type calculation | RETAIN AS PURE FACTS | Moved to `calendar_trade_type_service.py` |
| Calendar Ranking final-verdict branches | DELETE | Ranker remains facts/scoring only |
| API calendar reconstruction helpers | DELETE | APIs read repository truth |
| Daily Opportunity calendar inference | DELETE/RESTRICT | Uses canonical lifecycle/action state |
| API double-calendar grouping | DELETE | Reconciliation occurs before API serialization |
| Structure-attempt strategy rows | DELETE | Attempts are nested diagnostics, not opportunity rows |
| Old stage writers | DELETE FROM LIVE PATH | Lifecycle adapter and projection own compatibility fields |

## Deletion Report

Files deleted:

```text
app/services/unified_calendar_trade_engine_service.py
app/services/calendar_verdict_service.py
```

Files added:

```text
app/services/calendar_risk_fact_service.py
app/services/calendar_trade_type_service.py
```

Functions/classes deleted:

```text
build_unified_calendar_trade_engine
_build_new_trade_row
_build_open_trade_rows
_new_trade_verdict
_entry_plan
_verdict_tier
CalendarFinalVerdict
build_final_calendar_verdict
attach_final_verdicts_to_ranking
apply_hard_fail_overrides
```

Flags deleted:

```text
UNIFIED_CALENDAR_ENGINE_ENABLED
```

Imports deleted from live app:

```text
app.services.unified_calendar_trade_engine_service
app.services.calendar_verdict_service
build_final_calendar_verdict
attach_final_verdicts_to_ranking
UNIFIED_CALENDAR_ENGINE_ENABLED
```

Compatibility retained:

```text
Historical report snapshots may still contain _unified_calendar_engine.
report_service can read that key for archive rendering only.
New runs write _calendar_canonical_projection.
```

## Verdict Invariants

Pre-persistence canonical validation now checks:

```text
entry_evaluation_eligible=false -> trade_verdict=NOT_EVALUATED
final PASS/WATCH/NEAR_MISS/FAIL -> evaluation_state=FULLY_EVALUATED
parent opportunity IDs are unique
structure attempts are not parent row IDs
recommended_action is always present
```

Expected production effects:

```text
MONITOR_PRE_WINDOW -> NOT_EVALUATED / MONITOR / entry_allowed=false
STRUCTURE_UNAVAILABLE -> NOT_EVALUATED / NONE / entry_allowed=false
DEFERRED_BUDGET -> NOT_EVALUATED / NONE / entry_allowed=false
OPEN_POSITION -> WATCH / HOLD / entry_allowed=false
```

## Parent Row Model

Canonical row models:

```text
OPPORTUNITY_PARENT
OPEN_POSITION_CHILD
```

One ticker + canonical earnings date creates one parent opportunity row.
Rejected expirations, alternate strikes, and calculation branches are nested under `structure_attempt_summary`.

## SBUX 4 -> 2 -> 1 -> 0 Proof

Fixture proof remains enforced:

```text
4 broker legs
-> 2 child calendars
-> 1 double-calendar parent
-> 0 unmatched child calendars
```

Open Positions API exposes:

```text
child_calendar_count=2
parent_double_calendar_count=1
active_parent_calendar_count=1
has_open_calendars=true
```

## Reconciliation Contract

Runtime logs now use explicit dimensions:

```text
CALENDAR_ROW_RECONCILIATION
parent_generated=N
open_parent_generated=N
open_child_generated=N
structure_records=N
diagnostic_records=N
normalized=N
persisted=N
api_visible_parents=N
api_visible_children=N
daily_visible=N
api_exclusions={...}
persistence_exclusions={...}
strategy_dispositions={...}
```

No `pending_*` values are emitted by the reconciliation builder.

## Source Scan Proof

App-code scan is expected to return no matches for:

```text
UNIFIED_CALENDAR_ENGINE_ENABLED
Running Unified Calendar Trade Engine
Unified Calendar Trade Engine produced
unified_calendar_trade_engine_service
calendar_verdict_service
build_final_calendar_verdict
attach_final_verdicts_to_ranking
```

## Tests

Focused affected suite:

```text
247 passed
126 passed
46 passed
```

Full regression:

```text
3233 passed, 1 skipped, 2 subtests passed
```

## Railway Validation Plan

After merge/deploy, verify logs:

```text
CALENDAR_CANONICAL_PROJECTION ...
CALENDAR_DECISION_AUDIT ... invariant_violations=0
OPEN_POSITION_RECONCILIATION ... child_calendars=2 parent_double_calendars=1 unmatched_legs=0
DATA_CONFIDENCE_VALIDATION failed=0
```

Forbidden next-run log strings:

```text
UNIFIED_CALENDAR_ENGINE_ENABLED
Running Unified Calendar Trade Engine
Unified Calendar Trade Engine produced
```

## Known Remaining Work

- Rename old archive helper function names in report/export code if desired; they no longer represent live execution and read canonical `_calendar_canonical_projection` for new runs.
- Run live endpoint packet after Railway deploy to prove production ALGN/SBUX behavior.
