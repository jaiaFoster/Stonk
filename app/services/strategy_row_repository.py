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
from app.services.strategy_row_schema import NORMALIZED_ROW_EXCLUDE, STRATEGY_ROW_SCHEMA_VERSION, SEMANTIC_FIELDS_VERSION


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
                    decision_class TEXT,
                    action_type TEXT,
                    actionability TEXT,
                    eligibility_status TEXT,
                    eligibility_reason TEXT,
                    exclusion_reason TEXT,
                    priority_tier TEXT,
                    review_status TEXT,
                    dry_run INTEGER,
                    source_strategy_id TEXT,
                    source_row_id TEXT,
                    source_run_id TEXT,
                    semantic_source TEXT,
                    semantic_fields_version TEXT,
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy_run_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    row_count INTEGER NOT NULL DEFAULT 0,
                    execution_status TEXT NOT NULL DEFAULT 'ok',
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, strategy_id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_run_log_strategy ON strategy_run_log(strategy_id, created_at DESC, id DESC)"
            )
            self._ensure_columns(conn)

    def _ensure_columns(self, conn: sqlite3.Connection) -> None:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(strategy_rows)").fetchall()}
        columns = {
            "decision_class": "TEXT",
            "action_type": "TEXT",
            "actionability": "TEXT",
            "eligibility_status": "TEXT",
            "eligibility_reason": "TEXT",
            "exclusion_reason": "TEXT",
            "priority_tier": "TEXT",
            "review_status": "TEXT",
            "dry_run": "INTEGER",
            "source_strategy_id": "TEXT",
            "source_row_id": "TEXT",
            "source_run_id": "TEXT",
            "semantic_source": "TEXT",
            "semantic_fields_version": "TEXT",
        }
        for name, sql_type in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE strategy_rows ADD COLUMN {name} {sql_type}")

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
                    decision_class,action_type,actionability,eligibility_status,eligibility_reason,exclusion_reason,
                    priority_tier,review_status,dry_run,source_strategy_id,source_row_id,source_run_id,
                    semantic_source,semantic_fields_version,
                    details_json,gates_json,gate_groups_json,metrics_json,display_json,data_quality_json,risk_json,
                    structure_summary_json,raw_refs_json,normalization_status,normalization_errors_json,
                    missing_required_fields_json,created_at,schema_version
                ) VALUES (
                    :run_id,:strategy_id,:strategy_name,:strategy_version,:row_id,:symbol,:ticker,:asset_type,:row_type,
                    :verdict,:friendly_verdict,:score,:confidence,:primary_reason,:daily_opportunity_eligible,
                    :decision_class,:action_type,:actionability,:eligibility_status,:eligibility_reason,:exclusion_reason,
                    :priority_tier,:review_status,:dry_run,:source_strategy_id,:source_row_id,:source_run_id,
                    :semantic_source,:semantic_fields_version,
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
        # Write per-strategy run log so read_latest() can detect empty/failed runs.
        log_entries = []
        for strategy_id, result in (strategy_results or {}).items():
            if not isinstance(result, dict):
                continue
            count = by_strategy.get(strategy_id, 0)
            has_errors = bool(result.get("errors") or result.get("execution_failed"))
            if count == 0 and has_errors:
                status = "failed"
            elif count == 0:
                status = "empty"
            else:
                status = "ok"
            log_entries.append({
                "run_id": run_id,
                "strategy_id": strategy_id,
                "row_count": count,
                "execution_status": status,
                "created_at": now,
            })
        if log_entries:
            with self._connect() as conn:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO strategy_run_log (run_id, strategy_id, row_count, execution_status, created_at)
                    VALUES (:run_id, :strategy_id, :row_count, :execution_status, :created_at)
                    """,
                    log_entries,
                )
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
        # Prefer the run log for accurate current-run isolation (TKT-STRATEGY-ROW-CURRENT-RUN-ISOLATION).
        with self._connect() as conn:
            log_row = conn.execute(
                "SELECT run_id, row_count, execution_status FROM strategy_run_log "
                "WHERE strategy_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
                (strategy_id,),
            ).fetchone()
        if log_row:
            run_id = str(log_row["run_id"])
            row_count = int(log_row["row_count"])
            execution_status = str(log_row["execution_status"])
            if row_count == 0:
                return {
                    "run_id": run_id,
                    "rows": [],
                    "row_count": 0,
                    "execution_status": execution_status,
                    "fallback_used": False,
                }
            return self.read_run(run_id, strategy_id, limit=limit)
        # No log entry yet (pre-migration data) — fall back to row-based lookup.
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
                source_rows = []
            active_rows = result.get("active_rows") or []
            if isinstance(active_rows, list):
                source_rows = list(source_rows) + [row for row in active_rows if isinstance(row, dict)]
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
        row_type = compact.get("row_type") or compact.get("candidate_type") or _row_type(strategy_id, compact)
        semantics = _semantic_fields(strategy_id, compact, run_id)
        # TKT-CALENDAR-REJECTED-ELIGIBILITY: persistence boundary invariant for rejected_candidate rows.
        # Only override eligibility_status to "ineligible" if the row still carries an affirmative
        # value ("eligible", "conditional") — preserve specific ineligible codes like "excluded".
        if row_type == "rejected_candidate":
            _current_elig = str(semantics.get("eligibility_status") or "")
            if _current_elig not in {"excluded", "ineligible", "dry_run_excluded", "blocked"}:
                semantics["eligibility_status"] = "ineligible"
            # Use the "none" sentinel (string) not Python None — downstream reads check truthiness.
            if str(semantics.get("action_type") or "") in {"calendar_entry", "vertical_entry", "stock_add", "entry", ""}:
                semantics["action_type"] = "none"
            # Force daily_opportunity_eligible=0 — the **semantics spread below overrides compact value.
            semantics["daily_opportunity_eligible"] = 0
            semantics["can_enter_daily_opportunity"] = 0
        return {
            "run_id": run_id,
            "strategy_id": strategy_id,
            "strategy_name": compact.get("strategy_name") or compact.get("strategy_label") or strategy_id,
            "strategy_version": compact.get("strategy_version") or compact.get("version") or "v1",
            "row_id": row_id,
            "symbol": compact.get("symbol") or ticker,
            "ticker": ticker,
            "asset_type": compact.get("asset_type") or ("equity" if strategy_id == "stock_momentum" else "option_strategy"),
            "row_type": row_type,
            "verdict": compact.get("verdict") or compact.get("action") or "UNKNOWN",
            "friendly_verdict": compact.get("friendly_verdict") or _friendly_verdict(strategy_id, compact),
            "score": _float_or_none(compact.get("score") or compact.get("signal_score") or compact.get("priority_score")),
            "confidence": compact.get("confidence") or compact.get("data_quality_status") or "",
            "primary_reason": compact.get("primary_reason") or compact.get("primary_blocker") or compact.get("reason_label") or compact.get("reason") or "",
            "daily_opportunity_eligible": 1 if (compact.get("daily_opportunity_eligible") or compact.get("can_enter_daily_opportunity")) else 0,
            **semantics,
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
            "decision_class": row.get("decision_class"),
            "action_type": row.get("action_type"),
            "actionability": row.get("actionability"),
            "eligibility_status": row.get("eligibility_status"),
            "eligibility_reason": row.get("eligibility_reason"),
            "exclusion_reason": row.get("exclusion_reason"),
            "priority_tier": row.get("priority_tier"),
            "review_status": row.get("review_status"),
            "dry_run": bool(row.get("dry_run")),
            "source_strategy_id": row.get("source_strategy_id"),
            "source_row_id": row.get("source_row_id"),
            "source_run_id": row.get("source_run_id"),
            "source_table": "strategy_rows",
            "semantic_source": row.get("semantic_source"),
            "semantic_fields_version": row.get("semantic_fields_version"),
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
    summary = {
        key: row.get(key)
        for key in (
            "structure_type", "structure_status", "front_expiration", "back_expiration",
            "front_dte", "back_dte", "put_strike", "call_strike", "strike",
            # option_type required to distinguish call vs put calendars on the same ticker (TKT-OPEN-POSITIONS-LIFECYCLE-COMPLETENESS).
            "option_type",
            "conservative_debit", "mid_debit", "package_slippage_pct",
        )
        if row.get(key) is not None
    }
    structure = row.get("structure")
    if isinstance(structure, dict):
        summary.update({key: value for key, value in structure.items() if value is not None})
    value = row.get("value")
    if isinstance(value, dict):
        for key in ("current_debit", "current_mid_debit", "estimated_pnl_pct"):
            if value.get(key) is not None:
                summary[key] = value.get(key)
    return summary


def _row_type(strategy_id: str, row: dict[str, Any]) -> str:
    if strategy_id == "earnings_calendar":
        if str(row.get("type") or "") == "open_calendar":
            return "lifecycle_check"
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
        if str(row.get("type") or "") == "open_calendar":
            return {
                "earnings_calendar": {
                    "lifecycle_status": row.get("verdict") or row.get("action"),
                    "next_action": row.get("next_action"),
                    "structure": row.get("structure") or {},
                    "value": row.get("value") or {},
                    "hold_through_score": row.get("hold_through_score"),
                    "hold_through_action": row.get("hold_through_action"),
                }
            }
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
                "current_dte_to_earnings": row.get("current_dte_to_earnings"),
                "ideal_entry_window": row.get("ideal_entry_window"),
                "estimated_entry_date": row.get("estimated_entry_date"),
                "days_until_entry_window": row.get("days_until_entry_window"),
                "available_expirations": row.get("available_expirations") or [],
                "available_pre_earnings_expirations": row.get("available_pre_earnings_expirations") or [],
                "rejected_expirations": row.get("rejected_expirations") or [],
                "proposed_short_expiration": row.get("proposed_short_expiration"),
                "proposed_long_expiration": row.get("proposed_long_expiration"),
                "blocker_code": row.get("blocker_code") or row.get("entry_window_status"),
                "blocker_detail": row.get("blocker_detail") or row.get("entry_window_reason"),
            }
        }
    return {}


def _semantic_fields(strategy_id: str, row: dict[str, Any], run_id: str | None = None) -> dict[str, Any]:
    row_id = str(row.get("row_id") or row.get("observation_key") or _hash_row(strategy_id, row))
    if row.get("decision_class") and row.get("action_type"):
        semantics = {
            "decision_class": row.get("decision_class"),
            "action_type": row.get("action_type"),
            "actionability": row.get("actionability"),
            "eligibility_status": row.get("eligibility_status"),
            "eligibility_reason": row.get("eligibility_reason"),
            "exclusion_reason": row.get("exclusion_reason"),
            "priority_tier": row.get("priority_tier"),
            "review_status": row.get("review_status"),
            "semantic_source": row.get("semantic_source") or "row",
            "semantic_fields_version": row.get("semantic_fields_version") or SEMANTIC_FIELDS_VERSION,
        }
    else:
        semantics = _infer_semantics(strategy_id, row)
        semantics["semantic_source"] = "legacy_verdict_inference"
        semantics["semantic_fields_version"] = SEMANTIC_FIELDS_VERSION
    semantics.setdefault("actionability", "non_actionable")
    semantics.setdefault("eligibility_status", "excluded")
    semantics.setdefault("eligibility_reason", "")
    semantics.setdefault("exclusion_reason", "")
    semantics.setdefault("priority_tier", "diagnostic")
    semantics.setdefault("review_status", "blocked")
    semantics.update({
        "dry_run": 1 if bool(row.get("dry_run")) or strategy_id == "forward_factor_calendar" else 0,
        "source_strategy_id": strategy_id,
        "source_row_id": row_id,
        "source_run_id": run_id,
    })
    return semantics


def _infer_semantics(strategy_id: str, row: dict[str, Any]) -> dict[str, Any]:
    verdict = str(row.get("verdict") or row.get("action") or "")
    upper = verdict.upper()
    row_type = str(row.get("row_type") or row.get("type") or "")
    status = str(row.get("entry_window_status") or "")
    if strategy_id == "forward_factor_calendar":
        return _semantic("diagnostic", "diagnostic", "dry_run_only", "dry_run_excluded", "Forward Factor remains dry-run.", "dry_run", "diagnostic", "blocked")
    if strategy_id == "stock_momentum":
        if upper.startswith(("CONSIDER ADDING", "ADD ON")):
            return _semantic("add", "stock_add", "review_only", "eligible", "Stock momentum add candidate.", "", "normal", "ready")
        if upper.startswith("WATCH / CONFIRM TREND"):
            return _semantic("watch", "stock_watch", "monitor_only", "eligible", "Positive momentum, but confirmation is still required.", "", "low", "needs_confirmation")
        if upper.startswith(("TACTICAL ONLY", "STARTER ONLY", "HOLD / DO NOT ADD")):
            return _semantic("watch", "tactical_stock_watch", "monitor_only", "eligible", "Tactical/watchlist signal only; do not chase.", "", "low", "needs_confirmation")
        return _semantic("rejected", "none", "non_actionable", "excluded", "", "hard_fail", "diagnostic", "blocked")
    if strategy_id == "earnings_calendar":
        if row_type == "rejected_candidate" or upper.startswith("FAIL") or _has_hard_blocker(row):
            return _semantic("rejected", "none", "non_actionable", "excluded", "", _calendar_exclusion_reason(row, upper, status), "diagnostic", "blocked")
        if row_type in {"open_calendar", "lifecycle_check"} or upper.startswith(("HOLD", "EXIT", "CUT", "TAKE PROFIT", "RECHECK")):
            return _semantic("lifecycle", "active_calendar", "actionable", "eligible", "Active calendar lifecycle row.", "", "high", "monitor")
        if status == "MONITOR_PRE_WINDOW":
            return _semantic("monitor", "monitor", "monitor_only", "excluded", "Calendar candidate is before the entry window.", "pre_window", "low", "monitor")
        if status in {"ENTRY_WINDOW_CLOSED", "SHORT_DTE_TOO_LOW", "FRONT_LEG_TOO_DECAYED", "NO_PRE_EARNINGS_SHORT_EXPIRY"}:
            return _semantic("rejected", "none", "non_actionable", "excluded", "", "entry_window_closed", "diagnostic", "blocked")
        if status == "SHORT_LEG_SPANS_EARNINGS":
            return _semantic("rejected", "none", "non_actionable", "excluded", "", "short_leg_spans_earnings", "diagnostic", "blocked")
        if status in {"DATA_NEEDED", "DATE_CONFLICT_REVIEW"}:
            return _semantic("monitor", "monitor", "monitor_only", "excluded", "Calendar row needs more data or date confirmation.", status.lower(), "low", "needs_data")
        if bool(row.get("calendar_entry_allowed")) or upper.startswith("PASS"):
            return _semantic("entry", "calendar_entry", "review_only", "eligible", "Calendar entry candidate passed row gates.", "", "normal", "ready")
        return _semantic("rejected", "none", "non_actionable", "excluded", "", "hard_fail", "diagnostic", "blocked")
    if strategy_id == "skew_momentum_vertical":
        if upper.startswith("PASS"):
            return _semantic("entry", "vertical_entry", "review_only", "eligible", "Skew vertical candidate passed row gates.", "", "normal", "ready")
        if upper.startswith("WATCH"):
            return _semantic("watch", "monitor", "monitor_only", "conditional", "Skew row is watch-only.", "not_daily_opportunity_eligible", "low", "monitor")
        return _semantic("rejected", "none", "non_actionable", "excluded", "", "hard_fail", "diagnostic", "blocked")
    return _semantic("rejected", "none", "non_actionable", "excluded", "", "not_daily_opportunity_eligible", "diagnostic", "blocked")


def _has_hard_blocker(row: dict[str, Any]) -> bool:
    if bool(row.get("hard_blocker") or row.get("has_hard_blocker")):
        return True
    for gate in row.get("gates") or row.get("checks") or row.get("requirements") or []:
        if not isinstance(gate, dict):
            continue
        status = str(gate.get("status") or gate.get("result") or "").upper()
        if bool(gate.get("is_hard_block") or gate.get("hard_blocker") or gate.get("blocks")) and status in {"FAIL", "FAILED", "BLOCKED"}:
            return True
    return False


def _calendar_exclusion_reason(row: dict[str, Any], upper_verdict: str, status: str) -> str:
    code = str(row.get("blocker_code") or row.get("primary_blocker") or row.get("reason_code") or status or "")
    normalized = code.strip().lower().replace(" ", "_").replace("/", "_")
    mapping = {
        "debit_too_large": "debit_too_large",
        "entry_window_closed": "entry_window_closed",
        "short_leg_spans_earnings": "short_leg_spans_earnings",
        "short_dte_too_low": "short_dte_too_low",
        "front_leg_too_decayed": "front_leg_too_decayed",
        "no_pre_earnings_short_expiry": "no_pre_earnings_short_expiry",
        "date_conflict_review": "date_conflict",
        "date_conflict": "date_conflict",
        "data_quality": "data_quality_fail",
        "data_quality_fail": "data_quality_fail",
    }
    for key, value in mapping.items():
        if key in normalized:
            return value
    if "DEBIT TOO LARGE" in upper_verdict:
        return "debit_too_large"
    if "ENTRY_WINDOW_CLOSED" in upper_verdict:
        return "entry_window_closed"
    if "SHORT_LEG_SPANS_EARNINGS" in upper_verdict:
        return "short_leg_spans_earnings"
    if "SHORT_DTE_TOO_LOW" in upper_verdict:
        return "short_dte_too_low"
    if "NO_PRE_EARNINGS_SHORT_EXPIRY" in upper_verdict:
        return "no_pre_earnings_short_expiry"
    if "DATA QUALITY" in upper_verdict:
        return "data_quality_fail"
    return "hard_fail"


def _semantic(
    decision_class: str,
    action_type: str,
    actionability: str,
    eligibility_status: str,
    eligibility_reason: str,
    exclusion_reason: str,
    priority_tier: str,
    review_status: str,
) -> dict[str, Any]:
    return {
        "decision_class": decision_class,
        "action_type": action_type,
        "actionability": actionability,
        "eligibility_status": eligibility_status,
        "eligibility_reason": eligibility_reason,
        "exclusion_reason": exclusion_reason,
        "priority_tier": priority_tier,
        "review_status": review_status,
    }


def _hash_row(strategy_id: str, row: dict[str, Any]) -> str:
    raw = json.dumps({
        "strategy_id": strategy_id,
        "ticker": row.get("ticker"),
        "verdict": row.get("verdict") or row.get("action"),
        "front": row.get("front_expiration"),
        "back": row.get("back_expiration"),
        # option_type distinguishes call vs put calendars on the same ticker/strike/expiration.
        "option_type": str(row.get("option_type") or "").lower() or None,
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
