"""Queryable universal strategy row store.

This is the hot-path source for /api/strategies/<strategy_id>/rows. It stores
compact normalized row facts during /run and keeps full/debug archives in the
snapshot layer.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import config
from app.services.strategy_row_schema import NORMALIZED_ROW_EXCLUDE, STRATEGY_ROW_SCHEMA_VERSION


class StrategyRowRepository:
    SCHEMA_VERSION = 1

    def __init__(self, db_path: str | None = None):
        self.db_path = str(db_path or config.STRATEGY_ROW_DB_PATH)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=5)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy_rows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    strategy_name TEXT,
                    strategy_version TEXT,
                    row_id TEXT NOT NULL,
                    symbol TEXT,
                    ticker TEXT,
                    asset_type TEXT,
                    row_type TEXT,
                    verdict TEXT,
                    friendly_verdict TEXT,
                    score REAL,
                    confidence TEXT,
                    primary_reason TEXT,
                    daily_opportunity_eligible INTEGER,
                    details_json TEXT,
                    gates_json TEXT,
                    gate_groups_json TEXT,
                    metrics_json TEXT,
                    display_json TEXT,
                    data_quality_json TEXT,
                    risk_json TEXT,
                    structure_summary_json TEXT,
                    raw_refs_json TEXT,
                    normalization_status TEXT,
                    normalization_errors_json TEXT,
                    missing_required_fields_json TEXT,
                    created_at TEXT,
                    schema_version INTEGER,
                    UNIQUE(run_id, strategy_id, row_id)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_rows_latest ON strategy_rows(strategy_id, run_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_rows_run ON strategy_rows(run_id, strategy_id)")

    def write_run(self, run_id: str, strategy_results: dict[str, Any]) -> dict[str, Any]:
        rows = self._rows_from_results(run_id, strategy_results)
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("DELETE FROM strategy_rows WHERE run_id=?", (run_id,))
            conn.executemany(
                """
                INSERT OR REPLACE INTO strategy_rows (
                    run_id,strategy_id,strategy_name,strategy_version,row_id,symbol,ticker,asset_type,row_type,
                    verdict,friendly_verdict,score,confidence,primary_reason,daily_opportunity_eligible,
                    details_json,gates_json,gate_groups_json,metrics_json,display_json,data_quality_json,risk_json,
                    structure_summary_json,raw_refs_json,normalization_status,normalization_errors_json,
                    missing_required_fields_json,created_at,schema_version
                ) VALUES (
                    :run_id,:strategy_id,:strategy_name,:strategy_version,:row_id,:symbol,:ticker,:asset_type,:row_type,
                    :verdict,:friendly_verdict,:score,:confidence,:primary_reason,:daily_opportunity_eligible,
                    :details_json,:gates_json,:gate_groups_json,:metrics_json,:display_json,:data_quality_json,:risk_json,
                    :structure_summary_json,:raw_refs_json,:normalization_status,:normalization_errors_json,
                    :missing_required_fields_json,:created_at,:schema_version
                )
                """,
                [{**row, "created_at": now, "schema_version": self.SCHEMA_VERSION} for row in rows],
            )
        by_strategy: dict[str, int] = {}
        for row in rows:
            sid = row["strategy_id"]
            by_strategy[sid] = by_strategy.get(sid, 0) + 1
        return {"write_count": len(rows), "by_strategy": by_strategy}

    def latest_run_id(self, strategy_id: str | None = None) -> str | None:
        where = "WHERE strategy_id=?" if strategy_id else ""
        params = (strategy_id,) if strategy_id else ()
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT run_id FROM strategy_rows {where} ORDER BY created_at DESC, id DESC LIMIT 1",
                params,
            ).fetchone()
        return str(row["run_id"]) if row else None

    def read_latest(self, strategy_id: str, *, limit: int = 50) -> dict[str, Any]:
        run_id = self.latest_run_id(strategy_id)
        if not run_id:
            return {"run_id": None, "rows": [], "row_count": 0}
        return self.read_run(run_id, strategy_id, limit=limit)

    def read_run(self, run_id: str, strategy_id: str, *, limit: int = 50) -> dict[str, Any]:
        limit = min(max(int(limit or 50), 1), 200)
        with self._connect() as conn:
            raw_rows = conn.execute(
                """
                SELECT * FROM strategy_rows
                WHERE run_id=? AND strategy_id=?
                ORDER BY score IS NULL, score DESC, id ASC
                LIMIT ?
                """,
                (run_id, strategy_id, limit),
            ).fetchall()
        rows = [self._row_to_api(dict(row)) for row in raw_rows]
        return {"run_id": run_id, "rows": rows, "row_count": len(rows)}

    def _rows_from_results(self, run_id: str, strategy_results: dict[str, Any]) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for strategy_id, result in (strategy_results or {}).items():
            if not isinstance(result, dict):
                continue
            source_rows = result.get("canonical_opportunities") or result.get("rows") or result.get("items") or []
            if not isinstance(source_rows, list):
                continue
            errors_by_index = {
                int(err.get("row_index")): err
                for err in (result.get("canonical_normalizer_errors") or [])
                if isinstance(err, dict) and str(err.get("row_index", "")).isdigit()
            }
            for index, row in enumerate(source_rows):
                if not isinstance(row, dict):
                    continue
                output.append(self._normalize_for_store(run_id, strategy_id, row, errors_by_index.get(index)))
        return output

    def _normalize_for_store(
        self, run_id: str, strategy_id: str, row: dict[str, Any], error: dict[str, Any] | None
    ) -> dict[str, Any]:
        compact = _strip_heavy(row)
        ticker = str(compact.get("ticker") or compact.get("symbol") or "UNKNOWN").upper()
        row_id = str(compact.get("row_id") or compact.get("observation_key") or _hash_row(strategy_id, compact))
        missing = [
            key for key in ("strategy_id", "ticker", "verdict")
            if not compact.get(key) and not (key == "strategy_id" and strategy_id)
        ]
        normalization_status = "error" if error else ("missing_required_fields" if missing else "ok")
        normalization_errors = [error] if error else []
        details = compact.get("details") or _derived_details(strategy_id, compact)
        return {
            "run_id": run_id,
            "strategy_id": strategy_id,
            "strategy_name": compact.get("strategy_name") or compact.get("strategy_label") or strategy_id,
            "strategy_version": compact.get("strategy_version") or compact.get("version") or "v1",
            "row_id": row_id,
            "symbol": compact.get("symbol") or ticker,
            "ticker": ticker,
            "asset_type": compact.get("asset_type") or ("equity" if strategy_id == "stock_momentum" else "option_strategy"),
            "row_type": compact.get("row_type") or compact.get("candidate_type") or _row_type(strategy_id, compact),
            "verdict": compact.get("verdict") or compact.get("action") or "UNKNOWN",
            "friendly_verdict": compact.get("friendly_verdict") or _friendly_verdict(strategy_id, compact),
            "score": _float_or_none(compact.get("score") or compact.get("signal_score") or compact.get("priority_score")),
            "confidence": compact.get("confidence") or compact.get("data_quality_status") or "",
            "primary_reason": compact.get("primary_reason") or compact.get("primary_blocker") or compact.get("reason_label") or compact.get("reason") or "",
            "daily_opportunity_eligible": 1 if compact.get("daily_opportunity_eligible") else 0,
            "details_json": _json(details),
            "gates_json": _json(compact.get("gates") or []),
            "gate_groups_json": _json(compact.get("gate_groups") or {}),
            "metrics_json": _json(compact.get("metrics") or {}),
            "display_json": _json(compact.get("display") or {}),
            "data_quality_json": _json(compact.get("data_quality") or {}),
            "risk_json": _json(compact.get("risk") or compact.get("risk_flags") or {}),
            "structure_summary_json": _json(_structure_summary(compact)),
            "raw_refs_json": _json({
                "observation_key": compact.get("observation_key"),
                "schema_version": compact.get("schema_version") or compact.get("strategy_row_schema_version") or STRATEGY_ROW_SCHEMA_VERSION,
            }),
            "normalization_status": normalization_status,
            "normalization_errors_json": _json(normalization_errors),
            "missing_required_fields_json": _json(missing),
        }

    @staticmethod
    def _row_to_api(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "strategy_id": row.get("strategy_id"),
            "strategy_name": row.get("strategy_name"),
            "strategy_version": row.get("strategy_version"),
            "row_id": row.get("row_id"),
            "symbol": row.get("symbol"),
            "ticker": row.get("ticker"),
            "asset_type": row.get("asset_type"),
            "row_type": row.get("row_type"),
            "verdict": row.get("verdict"),
            "friendly_verdict": row.get("friendly_verdict"),
            "score": row.get("score"),
            "confidence": row.get("confidence"),
            "primary_reason": row.get("primary_reason"),
            "daily_opportunity_eligible": bool(row.get("daily_opportunity_eligible")),
            "details": _loads(row.get("details_json"), {}),
            "gates": _loads(row.get("gates_json"), []),
            "gate_groups": _loads(row.get("gate_groups_json"), {}),
            "metrics": _loads(row.get("metrics_json"), {}),
            "display": _loads(row.get("display_json"), {}),
            "data_quality": _loads(row.get("data_quality_json"), {}),
            "risk": _loads(row.get("risk_json"), {}),
            "structure_summary": _loads(row.get("structure_summary_json"), {}),
            "raw_refs": _loads(row.get("raw_refs_json"), {}),
            "normalization_status": row.get("normalization_status"),
            "normalization_errors": _loads(row.get("normalization_errors_json"), []),
            "missing_required_fields": _loads(row.get("missing_required_fields_json"), []),
            "created_at": row.get("created_at"),
            "schema_version": row.get("schema_version"),
        }


def _strip_heavy(row: dict[str, Any]) -> dict[str, Any]:
    compact = dict(row)
    for key in NORMALIZED_ROW_EXCLUDE:
        compact.pop(key, None)
    raw = compact.get("raw")
    if isinstance(raw, dict):
        compact["raw"] = {
            key: value for key, value in raw.items()
            if key in {"ticker", "action", "verdict", "final_verdict", "front_expiration", "back_expiration"}
        }
    return compact


def _structure_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "structure_type", "structure_status", "front_expiration", "back_expiration",
            "front_dte", "back_dte", "put_strike", "call_strike", "strike",
            "conservative_debit", "mid_debit", "package_slippage_pct",
        )
        if row.get(key) is not None
    }


def _row_type(strategy_id: str, row: dict[str, Any]) -> str:
    if strategy_id == "earnings_calendar":
        status = str(row.get("entry_window_status") or "")
        verdict = str(row.get("verdict") or "")
        if status in {"MONITOR_PRE_WINDOW", "DATA_NEEDED", "DATE_CONFLICT_REVIEW"}:
            return "diagnostic"
        if verdict.upper().startswith("FAIL") or status:
            return "rejected_candidate"
    return "observation"


def _friendly_verdict(strategy_id: str, row: dict[str, Any]) -> str:
    if strategy_id == "earnings_calendar":
        status = str(row.get("entry_window_status") or "")
        labels = {
            "ENTRY_WINDOW_CLOSED": "ENTRY WINDOW CLOSED / DO NOT ENTER",
            "SHORT_LEG_SPANS_EARNINGS": "SHORT LEG SPANS EARNINGS / DO NOT ENTER",
            "SHORT_DTE_TOO_LOW": "SHORT DTE TOO LOW / DO NOT ENTER",
            "FRONT_LEG_TOO_DECAYED": "FRONT LEG TOO DECAYED / DO NOT ENTER",
            "NO_PRE_EARNINGS_SHORT_EXPIRY": "NO PRE-EARNINGS SHORT EXPIRY",
            "MONITOR_PRE_WINDOW": "MONITOR / PRE-WINDOW",
            "DATA_NEEDED": "MONITOR / DATA NEEDED",
            "DATE_CONFLICT_REVIEW": "DATE CONFLICT REVIEW",
        }
        if status in labels:
            return labels[status]
    return row.get("verdict") or row.get("action") or "UNKNOWN"


def _derived_details(strategy_id: str, row: dict[str, Any]) -> dict[str, Any]:
    if strategy_id == "earnings_calendar":
        return {
            "earnings_calendar": {
                "entry_window_status": row.get("entry_window_status"),
                "entry_window_open": row.get("entry_window_open"),
                "entry_window_reason": row.get("entry_window_reason") or row.get("reason_label"),
                "short_leg_status": row.get("entry_window_status"),
                "short_leg_expires_before_earnings": row.get("short_leg_expires_before_earnings"),
                "short_leg_dte_minimum": row.get("short_leg_dte_minimum"),
                "short_leg_time_value_minimum": row.get("short_leg_time_value_minimum"),
                "short_leg_does_not_span_event": row.get("short_leg_does_not_span_event"),
                "available_pre_earnings_expirations": row.get("available_pre_earnings_expirations") or [],
                "rejected_expirations": row.get("rejected_expirations") or [],
                "proposed_short_expiration": row.get("proposed_short_expiration"),
                "proposed_long_expiration": row.get("proposed_long_expiration"),
            }
        }
    return {}


def _hash_row(strategy_id: str, row: dict[str, Any]) -> str:
    raw = json.dumps({
        "strategy_id": strategy_id,
        "ticker": row.get("ticker"),
        "verdict": row.get("verdict") or row.get("action"),
        "front": row.get("front_expiration"),
        "back": row.get("back_expiration"),
    }, default=str, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _json(value: Any) -> str:
    return json.dumps(value, default=str, separators=(",", ":"))


def _loads(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(value) if isinstance(value, str) and value else fallback
    except Exception:
        return fallback


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
