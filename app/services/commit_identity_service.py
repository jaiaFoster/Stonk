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
    app_git_commit = _first(env, ("RAILWAY_GIT_COMMIT_SHA", "GIT_COMMIT"))
    build_git_commit = _first(env, COMMIT_ENV_KEYS)
    run_manifest_git_commit = _clean(manifest.get("git_commit"))
    source_field, source_value = _source_of_truth(run_manifest_git_commit, build_git_commit, app_git_commit)
    values = {value for value in (app_git_commit, build_git_commit, run_manifest_git_commit) if value}
    return {
        "app_git_commit": app_git_commit or "unknown",
        "build_git_commit": build_git_commit or "unknown",
        "run_manifest_git_commit": run_manifest_git_commit or "unknown",
        "source_of_truth": source_value or "unknown",
        "source_of_truth_field": source_field,
        "commit_identity_mismatch": len(values) > 1,
        "env_commit_sources": env_commits,
        "git_branch": _clean(manifest.get("git_branch")) or _first(env, BRANCH_ENV_KEYS) or "unknown",
        "deploy_label": _clean(manifest.get("deploy_label")) or _clean(env.get("RAILWAY_DEPLOYMENT_ID")) or "unknown",
        "provider_calls_triggered": False,
        "read_only": True,
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

