# Patch 27T - Degraded Run Explainability

Patch 27T makes cached dashboard state more explicit when the latest run is
degraded but the last complete canonical snapshot is still being shown.

Added stored-state metadata:

- `latest_run_id`
- `latest_run_status`
- `latest_run_report_quality`
- `latest_run_degraded_reason`
- `canonical_snapshot_run_id`
- `canonical_snapshot_status`
- `canonical_snapshot_quality`
- `dashboard_data_source`
- `dashboard_using_latest_run`
- `dashboard_using_canonical_snapshot`
- `canonical_snapshot_preserved`

Shell dashboard behavior:

- Complete latest run: show that the dashboard uses latest complete run data.
- Degraded latest run: show that the latest attempted run degraded and the
  dashboard is using the preserved canonical complete snapshot.
- Missing degraded reason: report `unknown` instead of guessing.

This is diagnostics/display-only. It does not change providers, strategies,
Daily Opportunity, Forward Factor, lifecycle, raw archives, or execution.
