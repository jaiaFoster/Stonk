"""Custom strategy definition CRUD API.

ASA Patch 31B.
All endpoints are owner-scoped. No market-data provider calls are made.
No broker writes. No trade execution.

Routes:
  POST   /api/custom-strategies                      — create definition
  GET    /api/custom-strategies                      — list definitions for owner
  GET    /api/custom-strategies/<id>                 — get one definition
  PUT    /api/custom-strategies/<id>                 — update draft definition
  DELETE /api/custom-strategies/<id>                 — archive (soft-delete)
  POST   /api/custom-strategies/validate             — validate without saving
  POST   /api/custom-strategies/compile-preview      — compile preview (cost/requirements)
"""
from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from app.auth import require_auth

custom_strategy_bp = Blueprint("custom_strategy", __name__, url_prefix="/api/custom-strategies")


def _owner_id() -> str | None:
    user = getattr(g, "current_user", None) or {}
    uid = user.get("id")
    return str(uid) if uid else None


# ── Create ────────────────────────────────────────────────────────────────────

@custom_strategy_bp.route("", methods=["POST"])
@require_auth
def create_custom_strategy():
    owner_id = _owner_id()
    if not owner_id:
        return jsonify({"error": "Authentication required."}), 401

    body = request.get_json(silent=True) or {}

    from app.services.custom_strategy_validator import validate_custom_strategy
    result = validate_custom_strategy(body)
    if not result.valid:
        return jsonify({
            "error": "Validation failed.",
            "validation": result.to_dict(),
        }), 422

    from app.models.custom_strategy_models import CustomStrategyDefinition
    definition = CustomStrategyDefinition.new(
        owner_id=owner_id,
        name=str(body.get("name", "")).strip(),
        description=str(body.get("description", "")),
        universe=body.get("universe") or [],
        conditions=body.get("conditions") or [],
        output=body.get("output") or {"signal": "WATCH", "label": "", "notes": ""},
        risk=body.get("risk") or {},
    ).to_dict()

    from app.services.custom_strategy_repository import CustomStrategyRepository
    saved = CustomStrategyRepository().create(definition)
    return jsonify({"definition": saved, "provider_calls_triggered": False}), 201


# ── List ──────────────────────────────────────────────────────────────────────

@custom_strategy_bp.route("", methods=["GET"])
@require_auth
def list_custom_strategies():
    owner_id = _owner_id()
    if not owner_id:
        return jsonify({"error": "Authentication required."}), 401

    status_filter = request.args.get("status")
    try:
        limit = max(1, min(int(request.args.get("limit", 100)), 200))
        offset = max(0, int(request.args.get("offset", 0)))
    except (ValueError, TypeError):
        limit, offset = 100, 0

    from app.services.custom_strategy_repository import CustomStrategyRepository
    definitions = CustomStrategyRepository().list_for_owner(
        owner_id, status=status_filter, limit=limit, offset=offset
    )
    return jsonify({
        "definitions": definitions,
        "count": len(definitions),
        "provider_calls_triggered": False,
        "read_only": True,
    })


# ── Get one ───────────────────────────────────────────────────────────────────

@custom_strategy_bp.route("/<definition_id>", methods=["GET"])
@require_auth
def get_custom_strategy(definition_id: str):
    owner_id = _owner_id()
    if not owner_id:
        return jsonify({"error": "Authentication required."}), 401

    from app.services.custom_strategy_repository import (
        CustomStrategyNotFoundError,
        CustomStrategyRepository,
    )
    try:
        definition = CustomStrategyRepository().get(definition_id, owner_id)
    except CustomStrategyNotFoundError:
        return jsonify({"error": "Definition not found."}), 404
    return jsonify({"definition": definition, "provider_calls_triggered": False})


# ── Update ────────────────────────────────────────────────────────────────────

@custom_strategy_bp.route("/<definition_id>", methods=["PUT"])
@require_auth
def update_custom_strategy(definition_id: str):
    owner_id = _owner_id()
    if not owner_id:
        return jsonify({"error": "Authentication required."}), 401

    body = request.get_json(silent=True) or {}
    expected_version = body.get("definition_version")
    if expected_version is None:
        return jsonify({"error": "definition_version is required for optimistic locking."}), 422
    try:
        expected_version = int(expected_version)
    except (TypeError, ValueError):
        return jsonify({"error": "definition_version must be an integer."}), 422

    from app.services.custom_strategy_validator import validate_custom_strategy
    result = validate_custom_strategy(body)
    if not result.valid:
        return jsonify({
            "error": "Validation failed.",
            "validation": result.to_dict(),
        }), 422

    from app.services.custom_strategy_repository import (
        CustomStrategyConflictError,
        CustomStrategyNotFoundError,
        CustomStrategyRepository,
    )
    try:
        updated = CustomStrategyRepository().update(
            definition_id, owner_id, body, expected_version
        )
    except CustomStrategyNotFoundError:
        return jsonify({"error": "Definition not found."}), 404
    except CustomStrategyConflictError as exc:
        return jsonify({"error": str(exc), "conflict": True}), 409
    return jsonify({"definition": updated, "provider_calls_triggered": False})


# ── Delete (archive) ──────────────────────────────────────────────────────────

@custom_strategy_bp.route("/<definition_id>", methods=["DELETE"])
@require_auth
def delete_custom_strategy(definition_id: str):
    owner_id = _owner_id()
    if not owner_id:
        return jsonify({"error": "Authentication required."}), 401

    from app.services.custom_strategy_repository import (
        CustomStrategyNotFoundError,
        CustomStrategyRepository,
    )
    try:
        archived = CustomStrategyRepository().archive(definition_id, owner_id)
    except CustomStrategyNotFoundError:
        return jsonify({"error": "Definition not found."}), 404
    return jsonify({"definition": archived, "archived": True, "provider_calls_triggered": False})


# ── Validate ──────────────────────────────────────────────────────────────────

@custom_strategy_bp.route("/validate", methods=["POST"])
@require_auth
def validate_custom_strategy_endpoint():
    owner_id = _owner_id()
    if not owner_id:
        return jsonify({"error": "Authentication required."}), 401

    body = request.get_json(silent=True) or {}
    from app.services.custom_strategy_validator import validate_custom_strategy
    result = validate_custom_strategy(body)
    return jsonify({
        "valid": result.valid,
        "validation": result.to_dict(),
        "provider_calls_triggered": False,
    }), 200 if result.valid else 422


# ── Compile preview ───────────────────────────────────────────────────────────

@custom_strategy_bp.route("/compile-preview", methods=["POST"])
@require_auth
def compile_preview_endpoint():
    owner_id = _owner_id()
    if not owner_id:
        return jsonify({"error": "Authentication required."}), 401

    body = request.get_json(silent=True) or {}

    from app.services.custom_strategy_validator import validate_custom_strategy
    validation = validate_custom_strategy(body)
    if not validation.valid:
        return jsonify({
            "error": "Cannot compile invalid definition.",
            "validation": validation.to_dict(),
        }), 422

    from app.services.custom_strategy_compiler import compile_preview
    preview = compile_preview(body)
    return jsonify(preview)
