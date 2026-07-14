# Patch 33C.2 — Canonical Status Semantics and Verified Promotion

## Purpose

Patch 33C.2 hardens the canonical calendar pipeline after the legacy calendar
engine deletion. It fixes remaining semantic inconsistencies where
`NOT_EVALUATED` rows were summarized as failures, ambiguous eligibility fields
could leak into entry behavior, endpoint verification ran after canonical
snapshot promotion, and data-confidence logs could report hard failures without
hard failure codes.

## Root Causes

- The Strategy Registry adapter still used legacy pass/watch/skipped/default-fail
  aggregation for Earnings Calendar rows. Canonical `NOT_EVALUATED` rows fell
  into the default fail bucket.
- Endpoint verification used a broad rejected-row eligibility assertion instead
  of explicit entry-permission invariants.
- Calendar semantic validation existed but did not enforce entry permission,
  deferred-budget, final-verdict, and open-position invariants strongly enough
  before row-store persistence.
- The run persisted a successful report snapshot before endpoint verification
  executed.
- Data-confidence logging used `true_failures or failed_reports`, which allowed
  warning-only failed reports to appear as hard failures.
- Calendar policy source attribution compared values to defaults instead of
  checking whether an environment variable was explicitly present.

## Implemented Changes

- Earnings Calendar registry summaries now count canonical dimensions:
  `not_evaluated`, `fully_evaluated`, `pass`, `watch`, `near_miss`, `fail`,
  `blocked`, `structure_unavailable`, `deferred_budget`, and related lifecycle
  states. `NOT_EVALUATED` is not counted as `FAIL`.
- Calendar row validation now enforces:
  - `entry_allowed=True` requires entry evaluation, `FULLY_EVALUATED`, `PASS`,
    and `ENTER`;
  - `NOT_EVALUATED`, `STRUCTURE_UNAVAILABLE`, and `DEFERRED_BUDGET` rows cannot
    be entry-allowed;
  - final verdict rows must be fully evaluated and entry-evaluable;
  - open-position rows must have HOLD/EXIT/REVIEW actions.
- Run finalization runs required calendar semantic validation before
  `StrategyRowRepository.write_run()`. Violations block row-store persistence
  and mark required finalization failure.
- Endpoint verification returns `required_failed_count` and explicit check
  categories. Required failures now gate snapshot promotion inside the analysis
  run.
- The analysis run saves a candidate manifest, runs read-only endpoint
  verification, and only then promotes the canonical snapshot. Required
  verification failures result in `FAILED_VALIDATION` and preserve the previous
  canonical complete snapshot via `record_failure`.
- Data-confidence logs now use hard-failure counts. Warning-only validation
  reports produce `failed=0` and `failure_codes=[]`.
- Calendar reconciliation logs now separate opportunity parents, open-position
  parents, and open-position children.
- Daily Opportunity parity is logged as `DAILY_OPPORTUNITY_PARITY`.
- Calendar policy source attribution now reports `railway_env:<VAR>` whenever
  the environment variable exists, even if the value equals the approved
  default.

## Snapshot Promotion Contract

The intended order is now:

1. execute strategies;
2. validate canonical rows;
3. persist strategy artifacts;
4. build payload/profile state;
5. save candidate run manifest;
6. run read-only endpoint verification;
7. promote success snapshot only if required verification passes;
8. preserve the prior canonical snapshot on `FAILED_VALIDATION`;
9. emit run completion.

## Remaining Notes

- The report snapshot repository still has success/degraded/failed storage
  primitives rather than a separate candidate-snapshot table. This patch uses a
  candidate run manifest plus failed snapshot record to preserve canonical
  pointer safety without a broader storage rewrite.
- Legacy fixture compatibility remains for old registry tests that provide no
  canonical calendar fields. Live canonical rows use canonical summaries.

## Validation

- Focused regression slice:
  `357 passed`
- Full regression:
  `3240 passed, 1 skipped, 2 subtests passed`

