# Deploy Self-Check v1

Patch 27B adds a read-only deploy-verification surface. All endpoints return
redacted JSON from stored report state or current in-memory job metadata. They
do not call brokers, market-data providers, or the analysis pipeline.

## Endpoints

- `/api/dev/status`: boot/deploy metadata, current job summary, latest manifest.
- `/api/dev/latest-run-manifest`: latest compact completed-run manifest.
- `/api/dev/latest-profiles`: latest runtime, payload-size, and storage profiles.
- `/api/dev/feature-health`: stored-state feature checks, including Forward
  Factor dry-run and Daily Opportunity exclusion.
- `/api/dev/snapshot?mode=manifest_only`: compact stored manifest.
- `/api/dev/snapshot?mode=summary`: compact stored report summary.

## Protection

Set `ENABLE_DEV_DIAGNOSTICS_ENDPOINTS=true` to enable the four diagnostics
endpoints. Snapshot endpoints retain `ENABLE_DEV_SNAPSHOT_ENDPOINT`.

Set `DEV_API_TOKEN` for a separate read-only diagnostics token. If it is unset,
the endpoints use `RUN_TOKEN`. Tokens are never returned and known token values
are removed by the redaction service.

## Deploy Verification

1. Confirm expected commit is live through `/api/dev/status`.
2. Confirm `/health` returns `OK`.
3. Trigger `/run?token=RUN_TOKEN&mode=dev`.
4. Poll `/run/status/<job_id>?token=RUN_TOKEN`.
5. Inspect latest manifest, profiles, and feature health.
6. Stop if health, run completion, or required feature checks fail.

Codex does not merge its own PR. A human merges and approves any Railway token
or environment-variable changes.
