# Patch 27R - Build/Deploy Commit Identity Reconciliation

Patch 27R makes commit metadata explicit and non-blocking.

Diagnostics now expose:

- `app_git_commit`
- `build_git_commit`
- `run_manifest_git_commit`
- `source_of_truth`
- `source_of_truth_field`
- `commit_identity_mismatch`
- `env_commit_sources`

`run_manifest_git_commit` is preferred when present because it records the
commit observed by the completed report run. If commit sources disagree,
diagnostics set `commit_identity_mismatch=true` and keep the app healthy.

This is metadata-only. It does not change providers, strategies, Daily
Opportunity, Forward Factor, lifecycle, raw archives, or execution behavior.

