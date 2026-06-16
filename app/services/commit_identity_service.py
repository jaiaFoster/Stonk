"""Read-only build/run commit identity diagnostics."""

from __future__ import annotations

import os
from typing import Any, Mapping


COMMIT_ENV_KEYS = (
    "RAILWAY_GIT_COMMIT_SHA",
    "GIT_COMMIT",
    "SOURCE_VERSION",
    "COMMIT_SHA",
    "RAILWAY_DEPLOYMENT_COMMIT_SHA",
)
APP_COMMIT_ENV_KEYS = ("RAILWAY_GIT_COMMIT_SHA",)
BUILD_COMMIT_ENV_KEYS = (
    "GIT_COMMIT",
    "SOURCE_VERSION",
    "COMMIT_SHA",
    "RAILWAY_DEPLOYMENT_COMMIT_SHA",
    "RAILWAY_GIT_COMMIT_SHA",
)
BRANCH_ENV_KEYS = ("RAILWAY_GIT_BRANCH", "GIT_BRANCH", "BRANCH")


def build_commit_identity(
    manifest: dict[str, Any] | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    env = env or os.environ
    manifest = manifest or {}
    env_commits = {
        key.lower(): _clean(env.get(key))
        for key in COMMIT_ENV_KEYS
        if _clean(env.get(key))
    }
    app_git_commit = _first(env, APP_COMMIT_ENV_KEYS) or _first(env, BUILD_COMMIT_ENV_KEYS)
    build_git_commit = _first(env, BUILD_COMMIT_ENV_KEYS) or app_git_commit
    run_manifest_git_commit = _clean(manifest.get("git_commit"))
    source_field, source_value = _source_of_truth(run_manifest_git_commit, build_git_commit, app_git_commit)
    values = {value for value in (app_git_commit, build_git_commit, run_manifest_git_commit) if value}
    state = _identity_state(app_git_commit, build_git_commit, run_manifest_git_commit)
    return {
        "app_git_commit": app_git_commit or "unknown",
        "build_git_commit": build_git_commit or "unknown",
        "run_manifest_git_commit": run_manifest_git_commit or "unknown",
        "source_of_truth": source_value or "unknown",
        "source_of_truth_field": source_field,
        "commit_identity_mismatch": len(values) > 1,
        **state,
        "env_commit_sources": env_commits,
        "git_branch": _clean(manifest.get("git_branch")) or _first(env, BRANCH_ENV_KEYS) or "unknown",
        "deploy_label": _clean(manifest.get("deploy_label")) or _clean(env.get("RAILWAY_DEPLOYMENT_ID")) or "unknown",
        "provider_calls_triggered": False,
        "read_only": True,
    }


def _identity_state(app_git_commit: str | None, build_git_commit: str | None, run_manifest_git_commit: str | None) -> dict[str, Any]:
    current_deploy = build_git_commit or app_git_commit
    app_build_known = bool(app_git_commit and build_git_commit)
    app_build_mismatch = bool(app_build_known and app_git_commit != build_git_commit)
    stale_run_manifest = bool(
        run_manifest_git_commit
        and current_deploy
        and run_manifest_git_commit != current_deploy
        and not app_build_mismatch
    )
    fresh_run_aligned = bool(
        run_manifest_git_commit
        and current_deploy
        and run_manifest_git_commit == current_deploy
        and not app_build_mismatch
    )
    if app_build_mismatch:
        status = "app_build_identity_mismatch"
        summary = "App/build commit metadata disagree; verify deployment metadata before trusting stored run identity."
    elif stale_run_manifest:
        status = "stale_run_manifest_after_deploy"
        summary = "Latest stored run predates current deploy; run manifest should refresh after the next explicit run."
    elif fresh_run_aligned:
        status = "fresh_run_identity_aligned"
        summary = "Latest stored run manifest matches current app/build commit."
    elif not run_manifest_git_commit and current_deploy:
        status = "no_run_manifest_for_current_deploy"
        summary = "No stored run manifest is available; a fresh explicit run can establish run identity."
    elif current_deploy:
        status = "deploy_identity_available"
        summary = "Deploy commit metadata is available; no stored run manifest commit was compared."
    else:
        status = "unknown_commit_identity"
        summary = "Commit metadata is unavailable."
    return {
        "current_deploy_git_commit": current_deploy or "unknown",
        "current_deploy_git_commit_field": "build_git_commit" if build_git_commit else ("app_git_commit" if app_git_commit else "unknown"),
        "app_build_identity_mismatch": app_build_mismatch,
        "latest_run_manifest_stale_after_deploy": stale_run_manifest,
        "latest_run_predates_current_deploy": stale_run_manifest,
        "mismatch_expected_due_to_stale_run": stale_run_manifest,
        "fresh_run_needed_to_refresh_manifest": stale_run_manifest or bool(current_deploy and not run_manifest_git_commit),
        "fresh_run_expected_manifest_commit": current_deploy or "unknown",
        "fresh_run_identity_aligned": fresh_run_aligned,
        "commit_identity_status": status,
        "commit_identity_summary": summary,
        "mismatch_requires_attention": app_build_mismatch,
    }


def _source_of_truth(run_manifest_git_commit: str | None, build_git_commit: str | None, app_git_commit: str | None) -> tuple[str, str | None]:
    if run_manifest_git_commit:
        return "run_manifest_git_commit", run_manifest_git_commit
    if build_git_commit:
        return "build_git_commit", build_git_commit
    if app_git_commit:
        return "app_git_commit", app_git_commit
    return "unknown", None


def _first(env: Mapping[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = _clean(env.get(key))
        if value:
            return value
    return None


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
