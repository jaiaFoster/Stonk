"""
app/auth.py — Auth decorators for 28A.

Two decorators:
  @require_auth  — valid session token OR API key. Attaches g.current_user.
  @require_admin — require_auth + is_admin=1.

Token resolution order:
  1. Authorization: Bearer <token> header
  2. ?token=<token> query param

Token can be a session token OR an api_key — checked in that order.

Legacy bypass: if LEGACY_DEV_TOKEN_ENABLED and the token matches DEV_API_TOKEN
(or RUN_TOKEN fallback), the request is treated as admin with a synthetic user.
"""

from __future__ import annotations

from functools import wraps
from typing import Any

from flask import g, jsonify, request

from app import config


def _token_from_request() -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return request.args.get("token")


def _legacy_dev_token() -> str | None:
    """Return the configured legacy dev token, or None if not enabled."""
    if not config.LEGACY_DEV_TOKEN_ENABLED:
        return None
    return config.DEV_API_TOKEN or config.RUN_TOKEN or None


def _is_legacy_token(token: str | None) -> bool:
    if not token:
        return False
    legacy = _legacy_dev_token()
    return bool(legacy) and token == legacy


def _resolve_user(token: str) -> dict[str, Any] | None:
    """Resolve a token to a user dict. Returns None if not found/expired."""
    from app.db.users import get_user_by_session_token, get_user_by_api_key
    # Try session token first, then API key
    user = get_user_by_session_token(token)
    if user:
        return user
    return get_user_by_api_key(token)


def _synthetic_admin_user() -> dict[str, Any]:
    return {
        "id": 0,
        "username": "_legacy_dev",
        "is_admin": 1,
        "is_active": 1,
        "api_key": "",
        "last_login_at": None,
        "created_at": None,
    }


def require_auth(f):
    """Decorator: valid session token or API key required. Sets g.current_user."""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = _token_from_request()
        if token and _is_legacy_token(token):
            g.current_user = _synthetic_admin_user()
            return f(*args, **kwargs)
        if not token:
            return jsonify({"status": "error", "error": "Unauthorized.", "provider_calls_triggered": False}), 401
        try:
            user = _resolve_user(token)
        except Exception:
            user = None
        if not user or not user.get("is_active"):
            return jsonify({"status": "error", "error": "Unauthorized.", "provider_calls_triggered": False}), 401
        g.current_user = user
        return f(*args, **kwargs)
    return decorated


def require_admin(f):
    """Decorator: require_auth + is_admin=1."""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = _token_from_request()
        if token and _is_legacy_token(token):
            g.current_user = _synthetic_admin_user()
            return f(*args, **kwargs)
        if not token:
            return jsonify({"status": "error", "error": "Unauthorized.", "provider_calls_triggered": False}), 401
        try:
            user = _resolve_user(token)
        except Exception:
            user = None
        if not user or not user.get("is_active"):
            return jsonify({"status": "error", "error": "Unauthorized.", "provider_calls_triggered": False}), 401
        if not user.get("is_admin"):
            return jsonify({"status": "error", "error": "Forbidden.", "provider_calls_triggered": False}), 403
        g.current_user = user
        return f(*args, **kwargs)
    return decorated
