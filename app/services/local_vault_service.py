"""Local vault reader — design stub for offline Stonk consumers (iOS Shortcuts, Saku/朝策).

This module is the read counterpart to the vault write performed by
_write_local_vault() in analysis_service.py. It is intentionally a stub:
the core read path is implemented; the planned extensions listed below are
NOT and are marked with NOT_IMPLEMENTED.

## Vault write (implemented in 27AG)

After each successful run, when LOCAL_VAULT_OUTPUT_PATH is set, the pipeline
writes a compact JSON file containing the same payload shape served by
/api/advisor/snapshot. The file is overwritten on every successful run and
is safe to read at any time (it is written atomically from json.dump).

## Vault read (implemented here)

    from app.services.local_vault_service import read_vault, vault_is_fresh

    data = read_vault()                    # reads from LOCAL_VAULT_OUTPUT_PATH
    data = read_vault("/path/to/vault.json")  # explicit path override

    if vault_is_fresh(data):
        actions = data["snapshot"]["daily_opportunity"]["actions"]

## Vault schema (vault_schema_version: 1)

    {
      "vault_schema_version": 1,
      "run_id": "...",
      "run_mode": "prod",
      "snapshot": {
        "schema_version": 1,
        "generated_at": "ISO-8601",
        "run_id": "...",
        "run_date": "YYYY-MM-DD",
        "run_quality": "SUCCESS_COMPLETE",
        "provider_calls_triggered": false,
        "read_only": true,
        "freshness": { "status": "fresh|warn|stale|unknown", "age_seconds": int, ... },
        "daily_opportunity": { "action_count": int, "actions": [...] },
        "strategy_summary": { "<strategy_id>": { "pass": int, "watch": int, ... } },
        "positions_summary": [...],
        "ff_journal_summary": { "total_observations": int, "distinct_tickers": int, ... },
        "calendar_candidates": [...],
        "skew_candidates": [...],
      }
    }

## Planned extensions (NOT_IMPLEMENTED)

The following are explicitly deferred to a later patch (28A or later):

- NOT_IMPLEMENTED: watch-mode reader — inotify/kqueue or polling loop that
  notifies a caller when the vault file changes after a new run.

- NOT_IMPLEMENTED: push to local consumer — after a successful vault write,
  optionally POST the snapshot to a LOCAL_VAULT_PUSH_URL (e.g. a local
  Saku/朝策 instance on localhost:8080).

- NOT_IMPLEMENTED: vault diff — compare the current snapshot to the previous
  one and return a structured diff (new actions, closed actions, score
  changes). Useful for "what changed since yesterday" briefings.

- NOT_IMPLEMENTED: multi-vault support — write to more than one path (e.g.
  a shared NAS mount + a local copy) via LOCAL_VAULT_OUTPUT_PATHS list.

- NOT_IMPLEMENTED: vault encryption — encrypt at rest for vault paths on
  shared filesystems. Placeholder config key LOCAL_VAULT_ENCRYPT = false.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from app import config

VAULT_SCHEMA_VERSION = 1

# Threshold below which a vault is considered "fresh" enough for consumer use.
# Distinct from REPORT_FRESHNESS_WARN_SECONDS — this is about the vault file
# age from the local reader's perspective, not the run age.
_DEFAULT_FRESH_THRESHOLD_SECONDS = 43200  # 12 hours


class VaultNotFoundError(FileNotFoundError):
    """Raised when the vault file does not exist at the configured or given path."""


class VaultSchemaError(ValueError):
    """Raised when the vault file exists but has an unrecognised schema version."""


def read_vault(path: str | None = None) -> dict[str, Any]:
    """Read and return the vault JSON from disk.

    Args:
        path: Explicit path to the vault file. If None, reads from
              config.LOCAL_VAULT_OUTPUT_PATH.

    Returns:
        Parsed vault dict with at least ``vault_schema_version`` and
        ``snapshot`` keys.

    Raises:
        VaultNotFoundError: if the file does not exist.
        VaultSchemaError: if vault_schema_version is not 1.
        json.JSONDecodeError: if the file is not valid JSON.
    """
    resolved = _resolve_path(path)
    if not os.path.isfile(resolved):
        raise VaultNotFoundError(f"Vault file not found: {resolved}")
    with open(resolved, encoding="utf-8") as fh:
        data = json.load(fh)
    version = data.get("vault_schema_version")
    if version != VAULT_SCHEMA_VERSION:
        raise VaultSchemaError(
            f"Unsupported vault_schema_version {version!r}; expected {VAULT_SCHEMA_VERSION}."
        )
    return data


def vault_exists(path: str | None = None) -> bool:
    """Return True if the vault file exists and is non-empty."""
    try:
        resolved = _resolve_path(path)
        return os.path.isfile(resolved) and os.path.getsize(resolved) > 0
    except Exception:
        return False


def vault_age_seconds(vault_data: dict[str, Any]) -> int | None:
    """Return how many seconds old the vault snapshot is, or None if undetermined."""
    try:
        generated_at = (vault_data.get("snapshot") or {}).get("generated_at")
        if not generated_at:
            return None
        generated_dt = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
        return int((datetime.now(timezone.utc) - generated_dt).total_seconds())
    except Exception:
        return None


def vault_is_fresh(vault_data: dict[str, Any], threshold_seconds: int = _DEFAULT_FRESH_THRESHOLD_SECONDS) -> bool:
    """Return True if the vault snapshot is younger than threshold_seconds."""
    age = vault_age_seconds(vault_data)
    if age is None:
        return False
    return age < threshold_seconds


def vault_run_id(vault_data: dict[str, Any]) -> str | None:
    """Return the run_id recorded in the vault, or None."""
    return vault_data.get("run_id") or (vault_data.get("snapshot") or {}).get("run_id")


def vault_run_quality(vault_data: dict[str, Any]) -> str | None:
    """Return the run_quality string from the vault snapshot."""
    return (vault_data.get("snapshot") or {}).get("run_quality")


def vault_action_count(vault_data: dict[str, Any]) -> int:
    """Return the number of daily-opportunity actions in the vault snapshot."""
    return int((vault_data.get("snapshot") or {}).get("daily_opportunity", {}).get("action_count") or 0)


def vault_path() -> str | None:
    """Return the configured vault path, or None if not configured."""
    return getattr(config, "LOCAL_VAULT_OUTPUT_PATH", None) or None


def _resolve_path(path: str | None) -> str:
    resolved = path or vault_path()
    if not resolved:
        raise VaultNotFoundError(
            "No vault path provided and LOCAL_VAULT_OUTPUT_PATH is not configured."
        )
    return resolved
