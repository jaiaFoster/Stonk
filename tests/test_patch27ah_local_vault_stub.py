"""Smoke tests for Patch 27AH — local vault reader design stub."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from app.services.local_vault_service import (
    VaultNotFoundError,
    VaultSchemaError,
    read_vault,
    vault_action_count,
    vault_age_seconds,
    vault_exists,
    vault_is_fresh,
    vault_path,
    vault_run_id,
    vault_run_quality,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_vault(path: str, snapshot_override: dict | None = None, version: int = 1) -> None:
    generated_at = datetime.now(timezone.utc).isoformat()
    snapshot = {
        "schema_version": 1,
        "generated_at": generated_at,
        "run_id": "run-vault-001",
        "run_quality": "SUCCESS_COMPLETE",
        "provider_calls_triggered": False,
        "daily_opportunity": {"action_count": 3, "actions": []},
        "strategy_summary": {},
        "positions_summary": [],
        "ff_journal_summary": {"total_observations": 5},
        "calendar_candidates": [],
        "skew_candidates": [],
        "freshness": {"status": "fresh"},
    }
    if snapshot_override:
        snapshot.update(snapshot_override)
    data = {"vault_schema_version": version, "run_id": "run-vault-001", "run_mode": "prod", "snapshot": snapshot}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


# ---------------------------------------------------------------------------
# read_vault
# ---------------------------------------------------------------------------

class TestReadVault:
    def test_reads_valid_vault(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            _write_vault(path)
            data = read_vault(path)
            assert data["vault_schema_version"] == 1
            assert "snapshot" in data
        finally:
            os.unlink(path)

    def test_raises_vault_not_found_when_missing(self):
        with pytest.raises(VaultNotFoundError):
            read_vault("/does/not/exist/vault.json")

    def test_raises_vault_schema_error_on_wrong_version(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            _write_vault(path, version=99)
            with pytest.raises(VaultSchemaError):
                read_vault(path)
        finally:
            os.unlink(path)

    def test_raises_vault_not_found_when_no_path_configured(self):
        with patch("app.config.LOCAL_VAULT_OUTPUT_PATH", None):
            with pytest.raises(VaultNotFoundError):
                read_vault()

    def test_reads_from_configured_path(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            _write_vault(path)
            with patch("app.config.LOCAL_VAULT_OUTPUT_PATH", path):
                data = read_vault()
            assert data["run_id"] == "run-vault-001"
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# vault_exists
# ---------------------------------------------------------------------------

class TestVaultExists:
    def test_returns_true_for_existing_file(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            _write_vault(path)
            assert vault_exists(path) is True
        finally:
            os.unlink(path)

    def test_returns_false_for_missing_file(self):
        assert vault_exists("/no/such/path/vault.json") is False

    def test_returns_false_when_no_path(self):
        with patch("app.config.LOCAL_VAULT_OUTPUT_PATH", None):
            assert vault_exists() is False


# ---------------------------------------------------------------------------
# vault_age_seconds
# ---------------------------------------------------------------------------

class TestVaultAgeSeconds:
    def test_returns_age_for_recent_vault(self):
        generated = datetime.now(timezone.utc).isoformat()
        data = {"snapshot": {"generated_at": generated}}
        age = vault_age_seconds(data)
        assert age is not None
        assert 0 <= age < 5

    def test_returns_none_when_generated_at_missing(self):
        data = {"snapshot": {}}
        assert vault_age_seconds(data) is None

    def test_returns_none_when_snapshot_missing(self):
        assert vault_age_seconds({}) is None

    def test_older_snapshot_has_larger_age(self):
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        data = {"snapshot": {"generated_at": old_time}}
        age = vault_age_seconds(data)
        assert age is not None
        assert age >= 7200


# ---------------------------------------------------------------------------
# vault_is_fresh
# ---------------------------------------------------------------------------

class TestVaultIsFresh:
    def test_fresh_when_recent(self):
        generated = datetime.now(timezone.utc).isoformat()
        data = {"snapshot": {"generated_at": generated}}
        assert vault_is_fresh(data, threshold_seconds=3600) is True

    def test_stale_when_old(self):
        old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        data = {"snapshot": {"generated_at": old_time}}
        assert vault_is_fresh(data, threshold_seconds=86400) is False

    def test_false_when_no_generated_at(self):
        assert vault_is_fresh({"snapshot": {}}) is False


# ---------------------------------------------------------------------------
# Accessor helpers
# ---------------------------------------------------------------------------

class TestAccessorHelpers:
    def _vault(self):
        return {
            "vault_schema_version": 1,
            "run_id": "run-abc",
            "snapshot": {
                "run_id": "run-abc",
                "run_quality": "SUCCESS_COMPLETE",
                "daily_opportunity": {"action_count": 4, "actions": []},
            },
        }

    def test_vault_run_id(self):
        assert vault_run_id(self._vault()) == "run-abc"

    def test_vault_run_quality(self):
        assert vault_run_quality(self._vault()) == "SUCCESS_COMPLETE"

    def test_vault_action_count(self):
        assert vault_action_count(self._vault()) == 4

    def test_vault_action_count_zero_when_missing(self):
        assert vault_action_count({}) == 0

    def test_vault_path_returns_none_when_not_configured(self):
        with patch("app.config.LOCAL_VAULT_OUTPUT_PATH", None):
            assert vault_path() is None

    def test_vault_path_returns_configured_value(self):
        with patch("app.config.LOCAL_VAULT_OUTPUT_PATH", "/tmp/vault.json"):
            assert vault_path() == "/tmp/vault.json"
