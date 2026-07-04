"""
app/api/auth.py — TKT-FEAT-001: broker-optional registration + broker connect.

POST /api/auth/register         — create broker-optional account (no invite, no broker required).
POST /api/auth/connect-broker   — validate + store Robinhood credentials, set broker_connected=1.
"""

from __future__ import annotations

import collections
import threading
import time
from datetime import datetime, timezone
from typing import Any

from flask import Blueprint, g, jsonify, request

from app.auth import require_auth

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

# ---------------------------------------------------------------------------
# In-memory rate limiters (stateless across restarts — acceptable for this use)
# ---------------------------------------------------------------------------

_rate_lock = threading.Lock()
_reg_attempts: dict[str, list[float]] = collections.defaultdict(list)
_connect_attempts: dict[str, list[float]] = collections.defaultdict(list)

_REG_MAX = 5
_REG_WINDOW = 3600.0   # 1 hour

_CONNECT_MAX = 10
_CONNECT_WINDOW = 3600.0


def _check_rate_limit(store: dict[str, list[float]], ip: str, max_count: int, window: float) -> bool:
    """Return True if this IP can proceed, False if rate limited."""
    now = time.time()
    with _rate_lock:
        store[ip] = [t for t in store[ip] if now - t < window]
        if len(store[ip]) >= max_count:
            return False
        store[ip].append(now)
        return True


def _client_ip() -> str:
    return request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_email(email: str) -> bool:
    return bool(email) and "@" in email and "." in email.split("@")[-1] and len(email) <= 254


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@auth_bp.route("/register", methods=["POST"])
def register():
    """
    Create a broker-optional account.
    No invite code required. No broker credentials required.
    Rate limited to 5 registrations per IP per hour.
    """
    ip = _client_ip()
    if not _check_rate_limit(_reg_attempts, ip, _REG_MAX, _REG_WINDOW):
        return jsonify({
            "error": "rate_limited",
            "message": f"Too many registration attempts. Max {_REG_MAX} per hour.",
            "provider_calls_triggered": False,
        }), 429

    data = request.get_json(silent=True) or {}
    email = str(data.get("email") or "").strip().lower()
    password = str(data.get("password") or "")

    if not _valid_email(email):
        return jsonify({"error": "valid email required", "provider_calls_triggered": False}), 400
    if not password or len(password) < 8:
        return jsonify({"error": "password must be at least 8 characters", "provider_calls_triggered": False}), 400

    try:
        from app.db.users import create_user_broker_optional
        user = create_user_broker_optional(email=email, password_plain=password)
    except ValueError:
        return jsonify({"error": "email already registered", "provider_calls_triggered": False}), 409
    except Exception as exc:
        return jsonify({"error": "registration_failed", "message": str(exc), "provider_calls_triggered": False}), 500

    return jsonify({
        "user_id": user.get("id"),
        "user_key": user.get("api_key"),
        "broker_connected": False,
        "message": "Account created. Connect your brokerage anytime to track your portfolio.",
        "provider_calls_triggered": False,
    }), 201


@auth_bp.route("/connect-broker", methods=["POST"])
@require_auth
def connect_broker():
    """
    Validate and store Robinhood credentials for a broker-optional user.
    Sets broker_connected=1 on success.
    Requires authentication.
    """
    user = g.current_user or {}
    user_id = user.get("id")

    ip = _client_ip()
    if not _check_rate_limit(_connect_attempts, ip, _CONNECT_MAX, _CONNECT_WINDOW):
        return jsonify({
            "error": "rate_limited",
            "message": "Too many broker connect attempts. Try again later.",
            "provider_calls_triggered": False,
        }), 429

    from app.db.users import get_encryption_key_status
    if not get_encryption_key_status():
        return jsonify({
            "error": "service_unavailable",
            "message": "Credential storage unavailable. Contact administrator.",
            "provider_calls_triggered": False,
        }), 503

    body = request.get_json(silent=True) or {}
    rh_username = str(body.get("robinhood_username") or "").strip()
    rh_password = str(body.get("robinhood_password") or "")

    if not rh_username or not rh_password:
        return jsonify({
            "error": "missing_fields",
            "message": "robinhood_username and robinhood_password required.",
            "provider_calls_triggered": False,
        }), 400

    from app.services.broker_provider import BrokerCredentialProvider
    provider = BrokerCredentialProvider.get_provider("robinhood")
    valid, err_key = provider.validate_credentials(rh_username, rh_password)

    if not valid:
        _msgs: dict[str, str] = {
            "validation_timeout": "Robinhood validation timed out. Try again shortly.",
            "device_approval_required": "Robinhood requires device approval. Check your email/SMS and approve, then retry.",
            "rate_limited": "Robinhood rate limit hit. Try again in a few minutes.",
            "login_failed": "Robinhood login failed. Check username and password.",
        }
        return jsonify({
            "error": err_key,
            "message": _msgs.get(err_key or "", "Credential validation failed."),
            "broker_connected": False,
            "provider_calls_triggered": True,
        }), 400

    from app.db.users import update_broker_credentials, set_broker_connected
    update_broker_credentials(user_id, rh_username, rh_password)
    set_broker_connected(user_id, broker_type="robinhood")

    return jsonify({
        "status": "ok",
        "broker_connected": True,
        "message": "Brokerage connected successfully. Run personalization to fetch your positions.",
        "connected_at": datetime.now(timezone.utc).isoformat(),
        "provider_calls_triggered": True,
    }), 200
