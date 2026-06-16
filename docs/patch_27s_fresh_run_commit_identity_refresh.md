# Patch 27S - Fresh Run Commit Identity Refresh

Patch 27S keeps deploy checks provider-free while making commit mismatch
semantics clearer.

Diagnostics now distinguish:

- `stale_run_manifest_after_deploy`: app/build commit is current, but latest
  stored run manifest was produced by an older deploy.
- `fresh_run_identity_aligned`: latest stored run manifest matches current
  app/build commit after a fresh explicit run.
- `app_build_identity_mismatch`: app/build commit metadata disagree and need
  investigation.

Useful fields:

- `current_deploy_git_commit`
- `latest_run_manifest_stale_after_deploy`
- `latest_run_predates_current_deploy`
- `mismatch_expected_due_to_stale_run`
- `fresh_run_needed_to_refresh_manifest`
- `fresh_run_expected_manifest_commit`
- `fresh_run_identity_aligned`
- `mismatch_requires_attention`
- `commit_identity_status`

Deploy self-check rule:

1. Immediately after deploy, a mismatch can be informational if
   `latest_run_manifest_stale_after_deploy=true`.
2. After a fresh explicit dev run, the run manifest should normally refresh to
   the current deployed app/build commit and `fresh_run_identity_aligned=true`.
3. If a mismatch remains after a fresh run and
   `mismatch_expected_due_to_stale_run=false`, treat it as a bug/blocker.

This patch is metadata-only. It does not change providers, strategies, Daily
Opportunity, Forward Factor, lifecycle, raw archives, or execution behavior.
