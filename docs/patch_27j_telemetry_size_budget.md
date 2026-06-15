# Patch 27J - Telemetry Baseline and Size Budget

Patch 27J turns Patch 27I counters into a concise diagnostic baseline. It does
not prune data or change runtime behavior.

## Size Budgets

Default diagnostic thresholds:

- Warning: 250 KB
- Large: 500 KB
- Critical: 1 MB

The report categorizes measured sizes into hot summary, compact full summary,
raw provider archive, strategy/cache output, HTML reports, and other sections.
Flags are informational only.

## Usage Breakdown

The read-only telemetry endpoint separates:

- Snapshot modes
- Dashboard shell/full views
- Detail sections
- Full/provider-raw compatibility requests
- Copy/download export actions

## Baseline Readiness

`baseline_ready=false` and `awaiting_successful_snapshot` mean no successful
report snapshot has been saved since telemetry was enabled. The next successful
run records the first size baseline automatically.

## Scope Boundary

No pruning, provider calls, strategy changes, ranking changes, UI layout
changes, Forward Factor promotion, Daily Opportunity changes, or trade
execution are included.
