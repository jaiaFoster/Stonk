"""
app/services/trade_memory_service.py — SQLite-backed manual trade memory.

V1 scope:
- Store manually entered calendar spreads and their entry debit.
- Persist across deploys when TRADE_MEMORY_DB_PATH points at a Railway Volume.
- Provide simple read/write helpers for Flask routes and pipeline/report usage.
- Keep all operations defensive; failures should not break /run.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app import config

LogFn = Callable[[str], None]

SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def db_path() -> str:
    return str(getattr(config, "TRADE_MEMORY_DB_PATH", "/app/data/trade_memory.sqlite3"))


def ensure_db() -> str:
    path = Path(db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_memory_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS calendar_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                ticker TEXT NOT NULL,
                strategy TEXT NOT NULL DEFAULT 'earnings_calendar',
                option_type TEXT NOT NULL DEFAULT 'call',
                strike REAL NOT NULL,
                short_expiration TEXT NOT NULL,
                long_expiration TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1,
                entry_debit REAL,
                entry_total REAL,
                entry_underlying_price REAL,
                profit_target_pct REAL,
                max_loss_pct REAL,
                broker TEXT DEFAULT 'tradier',
                source TEXT DEFAULT 'manual',
                notes TEXT,
                close_value REAL,
                close_total REAL,
                closed_at TEXT,
                close_notes TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO trade_memory_meta(key, value)
            VALUES ('schema_version', ?)
            """,
            (str(SCHEMA_VERSION),),
        )
        conn.commit()
    return str(path)


def _connect() -> sqlite3.Connection:
    ensure_db()
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_dict(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    for key in [
        "strike",
        "entry_debit",
        "entry_total",
        "entry_underlying_price",
        "profit_target_pct",
        "max_loss_pct",
        "close_value",
        "close_total",
    ]:
        if item.get(key) is not None:
            try:
                item[key] = float(item[key])
            except Exception:
                pass
    for key in ["id", "quantity"]:
        if item.get(key) is not None:
            try:
                item[key] = int(item[key])
            except Exception:
                pass
    return item


def _float_or_none(value: Any) -> float | None:
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except Exception:
        return None


def _int_or_default(value: Any, default: int = 1) -> int:
    try:
        if value in {None, ""}:
            return default
        return int(float(value))
    except Exception:
        return default


def _clean_status(status: str | None) -> str:
    value = str(status or getattr(config, "TRADE_MEMORY_DEFAULT_STATUS", "open")).strip().lower()
    return value if value in {"open", "closed", "watch", "cancelled"} else "open"


def normalize_trade_input(data: dict[str, Any]) -> dict[str, Any]:
    ticker = str(data.get("ticker") or data.get("underlying") or "").upper().strip()
    option_type = str(data.get("option_type") or "call").lower().strip()
    option_type = option_type if option_type in {"call", "put"} else "call"
    strike = _float_or_none(data.get("strike"))
    short_expiration = str(data.get("short_expiration") or data.get("front_expiration") or "").strip()
    long_expiration = str(data.get("long_expiration") or data.get("back_expiration") or "").strip()
    quantity = _int_or_default(data.get("quantity"), 1)
    entry_debit = _float_or_none(data.get("entry_debit"))
    entry_total = _float_or_none(data.get("entry_total"))
    if entry_total is None and entry_debit is not None and quantity > 0:
        entry_total = entry_debit * quantity * 100.0

    if not ticker:
        raise ValueError("ticker is required")
    if strike is None or strike <= 0:
        raise ValueError("strike must be a positive number")
    if not short_expiration:
        raise ValueError("short_expiration is required")
    if not long_expiration:
        raise ValueError("long_expiration is required")
    if quantity <= 0:
        raise ValueError("quantity must be positive")

    return {
        "status": _clean_status(data.get("status")),
        "ticker": ticker,
        "strategy": str(data.get("strategy") or "earnings_calendar").strip() or "earnings_calendar",
        "option_type": option_type,
        "strike": strike,
        "short_expiration": short_expiration,
        "long_expiration": long_expiration,
        "quantity": quantity,
        "entry_debit": entry_debit,
        "entry_total": entry_total,
        "entry_underlying_price": _float_or_none(data.get("entry_underlying_price")),
        "profit_target_pct": _float_or_none(data.get("profit_target_pct"))
        if data.get("profit_target_pct") not in {None, ""}
        else float(getattr(config, "TRADE_MEMORY_DEFAULT_PROFIT_TARGET_PCT", 50)),
        "max_loss_pct": _float_or_none(data.get("max_loss_pct"))
        if data.get("max_loss_pct") not in {None, ""}
        else float(getattr(config, "TRADE_MEMORY_DEFAULT_MAX_LOSS_PCT", -35)),
        "broker": str(data.get("broker") or "tradier").strip().lower() or "tradier",
        "source": str(data.get("source") or "manual").strip().lower() or "manual",
        "notes": str(data.get("notes") or "").strip(),
    }


def add_calendar_trade(data: dict[str, Any]) -> dict[str, Any]:
    if not getattr(config, "TRADE_MEMORY_ENABLED", True):
        raise RuntimeError("TRADE_MEMORY_ENABLED=false")
    item = normalize_trade_input(data)
    now = utc_now()
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO calendar_trades (
                created_at, updated_at, status, ticker, strategy, option_type, strike,
                short_expiration, long_expiration, quantity, entry_debit, entry_total,
                entry_underlying_price, profit_target_pct, max_loss_pct, broker, source, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                now,
                item["status"],
                item["ticker"],
                item["strategy"],
                item["option_type"],
                item["strike"],
                item["short_expiration"],
                item["long_expiration"],
                item["quantity"],
                item["entry_debit"],
                item["entry_total"],
                item["entry_underlying_price"],
                item["profit_target_pct"],
                item["max_loss_pct"],
                item["broker"],
                item["source"],
                item["notes"],
            ),
        )
        conn.commit()
        trade_id = cursor.lastrowid
    return get_trade(int(trade_id)) or {"id": trade_id, **item}


def get_trade(trade_id: int) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM calendar_trades WHERE id = ?", (trade_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_calendar_trades(status: str | None = None) -> list[dict[str, Any]]:
    if not getattr(config, "TRADE_MEMORY_ENABLED", True):
        return []
    ensure_db()
    query = "SELECT * FROM calendar_trades"
    params: tuple[Any, ...] = ()
    if status:
        query += " WHERE status = ?"
        params = (_clean_status(status),)
    query += " ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'watch' THEN 1 WHEN 'closed' THEN 2 ELSE 3 END, created_at DESC"
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(row) for row in rows]


def close_calendar_trade(trade_id: int, close_value: Any = None, notes: str | None = None) -> dict[str, Any]:
    existing = get_trade(trade_id)
    if not existing:
        raise ValueError(f"Trade id {trade_id} not found")
    close_val = _float_or_none(close_value)
    quantity = int(existing.get("quantity") or 1)
    close_total = close_val * quantity * 100.0 if close_val is not None else None
    now = utc_now()
    with _connect() as conn:
        conn.execute(
            """
            UPDATE calendar_trades
            SET status = 'closed', updated_at = ?, closed_at = ?, close_value = ?, close_total = ?, close_notes = ?
            WHERE id = ?
            """,
            (now, now, close_val, close_total, str(notes or "").strip(), trade_id),
        )
        conn.commit()
    return get_trade(trade_id) or existing


def delete_trade(trade_id: int) -> bool:
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM calendar_trades WHERE id = ?", (trade_id,))
        conn.commit()
        return cursor.rowcount > 0


def match_trade_to_calendar(trade: dict[str, Any], calendar: dict[str, Any]) -> bool:
    ticker = str(calendar.get("ticker") or calendar.get("underlying") or "").upper().strip()
    option_type = str(calendar.get("option_type") or "call").lower().strip()
    strike = _float_or_none(calendar.get("strike"))
    front = str(calendar.get("front_expiration") or "").strip()
    back = str(calendar.get("back_expiration") or "").strip()
    return (
        str(trade.get("ticker") or "").upper().strip() == ticker
        and str(trade.get("option_type") or "call").lower().strip() == option_type
        and _float_or_none(trade.get("strike")) == strike
        and str(trade.get("short_expiration") or "").strip() == front
        and str(trade.get("long_expiration") or "").strip() == back
    )


def build_trade_memory_snapshot(open_options: dict[str, Any] | None = None, log_print: LogFn | None = None) -> dict[str, Any]:
    logger = log_print or (lambda msg: print(msg, flush=True))
    result: dict[str, Any] = {
        "source": "sqlite_trade_memory_v1",
        "enabled": bool(getattr(config, "TRADE_MEMORY_ENABLED", True)),
        "has_data": False,
        "db_path": db_path(),
        "open_trades": [],
        "closed_trades": [],
        "watch_trades": [],
        "matches": [],
        "summary": {},
        "errors": [],
    }
    if not result["enabled"]:
        result["errors"].append("TRADE_MEMORY_ENABLED=false")
        return _finalize_snapshot(result)
    try:
        path = ensure_db()
        open_trades = list_calendar_trades("open")
        closed_trades = list_calendar_trades("closed")
        watch_trades = list_calendar_trades("watch")
        result["db_path"] = path
        result["open_trades"] = open_trades
        result["closed_trades"] = closed_trades[:20]
        result["watch_trades"] = watch_trades
        result["has_data"] = bool(open_trades or closed_trades or watch_trades)
        calendars = (open_options or {}).get("calendars", []) if isinstance(open_options, dict) else []
        matches = []
        for trade in open_trades:
            for calendar in calendars:
                if match_trade_to_calendar(trade, calendar):
                    matches.append({"trade_id": trade.get("id"), "ticker": trade.get("ticker"), "trade": trade, "calendar": calendar})
        result["matches"] = matches
        logger(
            f"Trade Memory v1 loaded {len(open_trades)} open, {len(watch_trades)} watch, "
            f"{len(closed_trades)} closed trade(s); matches={len(matches)}."
        )
    except Exception as exc:
        result["errors"].append(str(exc))
        logger(f"Trade Memory v1 failed: {exc}")
    return _finalize_snapshot(result)


def _finalize_snapshot(result: dict[str, Any]) -> dict[str, Any]:
    open_trades = result.get("open_trades") or []
    closed_trades = result.get("closed_trades") or []
    watch_trades = result.get("watch_trades") or []
    result["summary"] = {
        "open_count": len(open_trades),
        "closed_count": len(closed_trades),
        "watch_count": len(watch_trades),
        "match_count": len(result.get("matches") or []),
        "db_path": result.get("db_path"),
        "enabled": result.get("enabled"),
    }
    return result
