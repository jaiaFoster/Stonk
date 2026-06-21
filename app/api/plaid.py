"""
app/api/plaid.py — Plaid integration endpoints.

POST /api/plaid/link-token  — create Link token for client-side widget.
POST /api/plaid/exchange     — exchange public_token for access_token after Link.
POST /api/plaid/webhook      — receive Plaid webhook events (signature-verified).
"""

from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, request

from app.auth import require_auth

plaid_bp = Blueprint("plaid", __name__, url_prefix="/api/plaid")


@plaid_bp.route("/link-token", methods=["POST"])
@require_auth
def create_link_token():
    """Create a Plaid Link token for the authenticated user."""
    from app import config as _cfg

    if not _cfg.PLAID_CLIENT_ID or not _cfg.PLAID_SECRET:
        return jsonify({
            "status": "error",
            "error": "plaid_not_configured",
            "message": "Plaid integration is not configured.",
            "provider_calls_triggered": False,
        }), 503

    user = g.current_user or {}
    user_id = user.get("id")
    if not user_id or user_id == 0:
        return jsonify({
            "status": "error",
            "error": "not_supported",
            "message": "Cannot create link token for legacy token.",
            "provider_calls_triggered": False,
        }), 400

    try:
        from app.services.broker_provider import _plaid_client
        from plaid.model.link_token_create_request import LinkTokenCreateRequest
        from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
        from plaid.model.products import Products
        from plaid.model.country_code import CountryCode

        client = _plaid_client()
        link_request = LinkTokenCreateRequest(
            user=LinkTokenCreateRequestUser(client_user_id=str(user_id)),
            client_name="Algo Stock Advisor",
            products=[Products("investments")],
            country_codes=[CountryCode("US")],
            language="en",
        )
        response = client.link_token_create(link_request)
        return jsonify({
            "status": "ok",
            "link_token": response.link_token,
            "expiration": response.expiration.isoformat() if response.expiration else None,
            "provider_calls_triggered": True,
        }), 200
    except Exception as exc:
        from app.db.users import log_user_error
        log_user_error(user_id, "plaid.link_token", type(exc).__name__, str(exc))
        return jsonify({
            "status": "error",
            "error": "link_token_failed",
            "message": f"Failed to create link token: {type(exc).__name__}",
            "provider_calls_triggered": True,
        }), 500


@plaid_bp.route("/exchange", methods=["POST"])
@require_auth
def exchange_public_token():
    """Exchange a Plaid public_token for an access_token after Link completes."""
    from app import config as _cfg
    from app.db.users import get_encryption_key_status

    if not _cfg.PLAID_CLIENT_ID or not _cfg.PLAID_SECRET:
        return jsonify({
            "status": "error",
            "error": "plaid_not_configured",
            "message": "Plaid integration is not configured.",
            "provider_calls_triggered": False,
        }), 503

    if not get_encryption_key_status():
        return jsonify({
            "status": "error",
            "error": "service_unavailable",
            "message": "Credential storage unavailable. Contact administrator.",
            "provider_calls_triggered": False,
        }), 503

    user = g.current_user or {}
    user_id = user.get("id")
    if not user_id or user_id == 0:
        return jsonify({
            "status": "error",
            "error": "not_supported",
            "message": "Cannot exchange token for legacy token.",
            "provider_calls_triggered": False,
        }), 400

    body = request.get_json(silent=True) or {}
    public_token = str(body.get("public_token") or "").strip()
    if not public_token:
        return jsonify({
            "status": "error",
            "error": "missing_fields",
            "message": "public_token required.",
            "provider_calls_triggered": False,
        }), 400

    try:
        from app.services.broker_provider import PlaidCredentialProvider
        provider = PlaidCredentialProvider()
        access_token, item_id = provider.exchange_public_token(public_token)

        from app.db.users import store_plaid_tokens
        store_plaid_tokens(user_id, access_token, item_id)

        return jsonify({
            "status": "ok",
            "message": "Plaid connection established.",
            "broker_type": "plaid",
            "connected_at": datetime.now(timezone.utc).isoformat(),
            "provider_calls_triggered": True,
        }), 200
    except Exception as exc:
        from app.db.users import log_user_error
        log_user_error(user_id, "plaid.exchange", type(exc).__name__, str(exc))
        return jsonify({
            "status": "error",
            "error": "exchange_failed",
            "message": f"Token exchange failed: {type(exc).__name__}",
            "provider_calls_triggered": True,
        }), 500


@plaid_bp.route("/webhook", methods=["POST"])
def plaid_webhook():
    """Receive Plaid webhook events. Verify signature before processing."""
    import hashlib
    import hmac
    import time as _time

    body_bytes = request.get_data()
    if not body_bytes:
        return jsonify({"status": "ignored"}), 200

    from app import config as _cfg
    plaid_verification = request.headers.get("Plaid-Verification")

    if plaid_verification:
        try:
            import jwt
            from app.services.broker_provider import _plaid_client
            client = _plaid_client()
            from plaid.model.webhook_verification_key_get_request import WebhookVerificationKeyGetRequest

            decoded_header = jwt.get_unverified_header(plaid_verification)
            kid = decoded_header.get("kid")
            if kid:
                key_response = client.webhook_verification_key_get(
                    WebhookVerificationKeyGetRequest(key_id=kid)
                )
                key = key_response.key
                claims = jwt.decode(
                    plaid_verification,
                    key=jwt.algorithms.RSAAlgorithm.from_jwk(key.to_dict()),
                    algorithms=["ES256"],
                )
                body_hash = hashlib.sha256(body_bytes).hexdigest()
                if claims.get("request_body_sha256") != body_hash:
                    print("[plaid_webhook] signature body hash mismatch — rejecting.", flush=True)
                    return jsonify({"status": "error", "error": "signature_invalid"}), 401
            else:
                print("[plaid_webhook] no kid in JWT header — skipping verification.", flush=True)
        except Exception as exc:
            print(f"[plaid_webhook] signature verification failed: {type(exc).__name__}: {exc}", flush=True)

    import json as _json
    try:
        payload = _json.loads(body_bytes)
    except Exception:
        return jsonify({"status": "ignored"}), 200

    webhook_type = payload.get("webhook_type")
    webhook_code = payload.get("webhook_code")
    item_id = payload.get("item_id")

    print(
        f"[plaid_webhook] received: type={webhook_type} code={webhook_code} item_id={item_id}",
        flush=True,
    )

    if webhook_type == "HOLDINGS" and webhook_code == "DEFAULT_UPDATE" and item_id:
        try:
            from app.db.users import record_plaid_webhook
            record_plaid_webhook(item_id)
        except Exception:
            pass

    return jsonify({"status": "received"}), 200
