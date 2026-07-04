"""
app/api/telemetry.py — TKT-FEAT-002: signal engagement telemetry.

POST /api/telemetry/signal-engagement — record signal engagement (optional auth).
"""

from __future__ import annotations

import collections
import threading
import time

from flask import Blueprint, request, jsonify

telemetry_bp = Blueprint("telemetry", __name__, url_prefix="/api/telemetry")

# ---------------------------------------------------------------------------
# Rate limiter: 60 events per IP per minute
# ---------------------------------------------------------------------------

_rate_lock = threading.Lock()
_engagement_attempts: dict[str, list[float]] = collections.defaultdict(list)

_ENGAGEMENT_MAX = 60
_ENGAGEMENT_WINDOW = 60.0  # 1 minute


def _check_rate_limit(ip: str) -> bool:
    now = time.time()
    with _rate_lock:
        _engagement_attempts[ip] = [t for t in _engagement_attempts[ip] if now - t < _ENGAGEMENT_WINDOW]
        if len(_engagement_attempts[ip]) >= _ENGAGEMENT_MAX:
            return False
        _engagement_attempts[ip].append(now)
        return True


def _client_ip() -> str:
    return request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()


def _resolve_user_id(token: str | None) -> str | None:
    """Return user_id (as str) if token resolves to a real user, else None."""
    if not token:
        return None
    try:
        from app.db.users import get_user_by_api_key, get_user_by_session_token
        user = get_user_by_session_token(token) or get_user_by_api_key(token)
        if user and user.get("is_active"):
            uid = user.get("id")
            return str(uid) if uid else None
    except Exception:
        pass
    return None


def _get_broker_mode(user_id: str | None) -> str | None:
    """Return 'connected' or 'signals_only' for known users, None for anonymous."""
    if not user_id:
        return None
    try:
        from app.db.users import get_user_by_id
        user = get_user_by_id(int(user_id))
        if not user:
            return None
        if user.get("broker_connection_optional") and not user.get("broker_connected"):
            return "signals_only"
        return "connected"
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@telemetry_bp.route("/signal-engagement", methods=["POST"])
def record_signal_engagement():
    """
    Record when a user engages with a signal.
    Auth is optional — accepts anonymous engagement with session_id.
    Rate limited: 60 events per IP per minute.
    Fire-and-forget — never raises to the caller.
    """
    ip = _client_ip()
    if not _check_rate_limit(ip):
        return jsonify({"error": "rate_limited", "message": "Too many requests."}), 429

    data = request.get_json(silent=True) or {}
    token = request.args.get("token") or data.get("token")

    user_id = _resolve_user_id(token)

    ticker = str(data.get("ticker") or "").upper().strip()[:20]
    strategy_id = str(data.get("strategy_id") or "")[:64]
    action = str(data.get("action") or "view_signal")
    verdict = str(data.get("verdict") or "")[:20] or None
    session_id = str(data.get("session_id") or "")[:64] or None
    run_id = str(data.get("run_id") or "")[:64] or None

    if not ticker or not strategy_id:
        return jsonify({"error": "ticker and strategy_id required"}), 400

    broker_mode = _get_broker_mode(user_id)

    from app.db.telemetry import record_signal_engagement as _record
    _record(
        ticker=ticker,
        strategy_id=strategy_id,
        action=action,
        verdict=verdict,
        user_id=user_id,
        broker_mode=broker_mode,
        session_id=session_id,
        run_id=run_id,
    )

    return jsonify({"recorded": True}), 200
