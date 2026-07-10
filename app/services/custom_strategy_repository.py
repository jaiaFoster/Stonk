"""SQLite persistence for custom strategy definitions.

ASA Patch 31B — schema_version 31B.v1.
Table: custom_strategy_definitions.
All reads and writes are owner-scoped; users cannot access others' definitions.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Generator

from app import config

_DB_PATH = getattr(config, "STRATEGY_DB_PATH", "data/strategy_rows.db")
_SCHEMA_VERSION = "31B.v1"


class CustomStrategyConflictError(Exception):
    """Raised when an optimistic-locking version conflict is detected (HTTP 409)."""


class CustomStrategyNotFoundError(Exception):
    """Raised when a definition is not found or does not belong to the owner."""


class CustomStrategyRepository:

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        try:
            self._ensure_schema(conn)
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS custom_strategy_definitions (
                definition_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                definition_version INTEGER NOT NULL DEFAULT 1,
                definition_json TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_custom_strategy_owner
            ON custom_strategy_definitions (owner_id, status)
        """)
        self._ensure_columns(conn)

    def _ensure_columns(self, conn: sqlite3.Connection) -> None:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(custom_strategy_definitions)")}
        additions: list[tuple[str, str]] = []
        if "schema_version" not in existing:
            additions.append(("schema_version", "TEXT NOT NULL DEFAULT '31B.v1'"))
        for col_name, col_def in additions:
            conn.execute(f"ALTER TABLE custom_strategy_definitions ADD COLUMN {col_name} {col_def}")

    # ── CRUD ────────────────────────────────────────────────────────────────────

    def create(self, definition: dict[str, Any]) -> dict[str, Any]:
        """Persist a new definition. Returns the saved definition dict."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO custom_strategy_definitions
                    (definition_id, owner_id, name, status, definition_version,
                     definition_json, schema_version, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    definition["definition_id"],
                    definition["owner_id"],
                    definition["name"],
                    definition.get("status", "draft"),
                    definition.get("definition_version", 1),
                    json.dumps(definition, default=str),
                    definition.get("schema_version", _SCHEMA_VERSION),
                    definition["created_at"],
                    definition["updated_at"],
                ),
            )
        return definition

    def get(self, definition_id: str, owner_id: str) -> dict[str, Any]:
        """Return definition by ID, scoped to owner. Raises CustomStrategyNotFoundError if missing."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT definition_json FROM custom_strategy_definitions WHERE definition_id=? AND owner_id=?",
                (definition_id, owner_id),
            ).fetchone()
        if row is None:
            raise CustomStrategyNotFoundError(definition_id)
        return json.loads(row["definition_json"])

    def list_for_owner(
        self,
        owner_id: str,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return all definitions for an owner, optionally filtered by status."""
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT definition_json FROM custom_strategy_definitions WHERE owner_id=? AND status=? ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                    (owner_id, status, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT definition_json FROM custom_strategy_definitions WHERE owner_id=? ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                    (owner_id, limit, offset),
                ).fetchall()
        return [json.loads(row["definition_json"]) for row in rows]

    def update(
        self,
        definition_id: str,
        owner_id: str,
        updates: dict[str, Any],
        expected_version: int,
    ) -> dict[str, Any]:
        """Update a draft definition with optimistic locking.

        Raises CustomStrategyConflictError if expected_version does not match.
        Raises CustomStrategyNotFoundError if definition does not exist or owner mismatch.
        Only 'draft' definitions may be updated through this path.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT definition_json, definition_version, status FROM custom_strategy_definitions WHERE definition_id=? AND owner_id=?",
                (definition_id, owner_id),
            ).fetchone()
            if row is None:
                raise CustomStrategyNotFoundError(definition_id)
            current_version = row["definition_version"]
            if current_version != expected_version:
                raise CustomStrategyConflictError(
                    f"Version conflict: expected {expected_version}, got {current_version}."
                )
            existing = json.loads(row["definition_json"])
            now = datetime.now(timezone.utc).isoformat()
            new_version = current_version + 1
            updated = {
                **existing,
                **{k: v for k, v in updates.items() if k not in ("definition_id", "owner_id", "created_at", "definition_version", "schema_version")},
                "definition_version": new_version,
                "updated_at": now,
            }
            conn.execute(
                """
                UPDATE custom_strategy_definitions
                SET name=?, status=?, definition_version=?, definition_json=?, updated_at=?
                WHERE definition_id=? AND owner_id=?
                """,
                (
                    updated.get("name", existing.get("name")),
                    updated.get("status", existing.get("status")),
                    new_version,
                    json.dumps(updated, default=str),
                    now,
                    definition_id,
                    owner_id,
                ),
            )
        return updated

    def archive(self, definition_id: str, owner_id: str) -> dict[str, Any]:
        """Set status to 'archived'. Returns updated definition."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT definition_json FROM custom_strategy_definitions WHERE definition_id=? AND owner_id=?",
                (definition_id, owner_id),
            ).fetchone()
            if row is None:
                raise CustomStrategyNotFoundError(definition_id)
            existing = json.loads(row["definition_json"])
            now = datetime.now(timezone.utc).isoformat()
            updated = {**existing, "status": "archived", "updated_at": now}
            conn.execute(
                "UPDATE custom_strategy_definitions SET status='archived', definition_json=?, updated_at=? WHERE definition_id=? AND owner_id=?",
                (json.dumps(updated, default=str), now, definition_id, owner_id),
            )
        return updated

    def delete(self, definition_id: str, owner_id: str) -> bool:
        """Hard-delete a draft definition. Returns True if deleted, False if not found."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM custom_strategy_definitions WHERE definition_id=? AND owner_id=?",
                (definition_id, owner_id),
            ).fetchone()
            if row is None:
                return False
            conn.execute(
                "DELETE FROM custom_strategy_definitions WHERE definition_id=? AND owner_id=? AND status='draft'",
                (definition_id, owner_id),
            )
        return True
