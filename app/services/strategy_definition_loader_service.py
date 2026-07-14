"""Trusted local strategy-definition loader.

Definitions are checked-in JSON files.  This loader validates shape, field IDs,
operators, calculation IDs, and leg references without executing strategy code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.models.strategy_definition import STRATEGY_DEFINITION_SCHEMA_VERSION, StrategyDefinition
from app.services.strategy_calculation_registry import validate_calculation_id
from app.services.strategy_data_catalog_service import field_catalog, validate_rule_definition


DEFAULT_STRATEGY_DEFINITION_DIR = Path(__file__).resolve().parents[2] / "config" / "strategies"


def load_strategy_definition(path: str | Path, *, trusted_root: str | Path | None = None) -> StrategyDefinition:
    root = Path(trusted_root or DEFAULT_STRATEGY_DEFINITION_DIR).resolve()
    candidate = Path(path).resolve()
    if root not in candidate.parents and candidate != root:
        raise ValueError("STRATEGY_DEFINITION_OUTSIDE_TRUSTED_ROOT")
    raw = json.loads(candidate.read_text(encoding="utf-8"))
    validation = validate_strategy_definition(raw)
    if not validation["valid"]:
        codes = ",".join(error["code"] for error in validation["errors"])
        raise ValueError(f"STRATEGY_DEFINITION_INVALID:{codes}")
    meta = raw.get("metadata") or {}
    return StrategyDefinition(
        strategy_id=str(raw["strategy_id"]),
        version=str(raw["version"]),
        name=str(meta.get("name") or raw["strategy_id"]),
        schema_version=str(raw["schema_version"]),
        raw=raw,
    )


def load_builtin_strategy_definitions(directory: str | Path | None = None) -> dict[str, StrategyDefinition]:
    root = Path(directory or DEFAULT_STRATEGY_DEFINITION_DIR)
    definitions: dict[str, StrategyDefinition] = {}
    for path in sorted(root.glob("*.json")):
        definition = load_strategy_definition(path, trusted_root=root)
        if definition.strategy_id in definitions:
            raise ValueError(f"DUPLICATE_STRATEGY_ID:{definition.strategy_id}")
        definitions[definition.strategy_id] = definition
    return definitions


def validate_strategy_definition(raw: dict[str, Any]) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    if not isinstance(raw, dict):
        return {"valid": False, "errors": [{"code": "DEFINITION_NOT_OBJECT"}]}
    for key in ("schema_version", "strategy_id", "version", "metadata", "data_requirements", "structures"):
        if key not in raw:
            errors.append({"code": "REQUIRED_KEY_MISSING", "key": key})
    if raw.get("schema_version") != STRATEGY_DEFINITION_SCHEMA_VERSION:
        errors.append({"code": "SCHEMA_VERSION_UNSUPPORTED", "expected": STRATEGY_DEFINITION_SCHEMA_VERSION, "actual": raw.get("schema_version")})
    strategy_id = str(raw.get("strategy_id") or "")
    if not strategy_id or any(ch in strategy_id for ch in " ./\\:"):
        errors.append({"code": "STRATEGY_ID_INVALID", "strategy_id": strategy_id})

    _validate_rules(raw.get("universe", {}).get("filters") or [], errors, "universe.filters")
    _validate_rules(raw.get("gates") or [], errors, "gates")
    _validate_rules(raw.get("ranking", {}).get("rules") or [], errors, "ranking.rules")
    _validate_data_requirements(raw.get("data_requirements") or {}, errors)
    _validate_structures(raw.get("structures") or [], errors)
    return {"valid": not errors, "errors": errors}


def strategy_definition_summary() -> dict[str, Any]:
    definitions = load_builtin_strategy_definitions()
    return {
        "schema_version": STRATEGY_DEFINITION_SCHEMA_VERSION,
        "definition_count": len(definitions),
        "strategies": {
            sid: {
                "strategy_id": item.strategy_id,
                "version": item.version,
                "name": item.name,
                "structure_count": len(item.raw.get("structures") or []),
            }
            for sid, item in sorted(definitions.items())
        },
        "provider_calls_triggered": False,
        "read_only": True,
    }


def _validate_rules(rules: list[Any], errors: list[dict[str, Any]], path: str) -> None:
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            errors.append({"code": "RULE_NOT_OBJECT", "path": f"{path}[{index}]"})
            continue
        result = validate_rule_definition(rule)
        if not result.get("valid"):
            for error in result.get("errors") or []:
                errors.append({"code": error.get("code") or "RULE_INVALID", "path": f"{path}[{index}]", "field_id": rule.get("field_id")})


def _validate_data_requirements(requirements: dict[str, Any], errors: list[dict[str, Any]]) -> None:
    fields = field_catalog()
    for req_id, spec in requirements.items():
        if not isinstance(spec, dict):
            errors.append({"code": "DATA_REQUIREMENT_NOT_OBJECT", "requirement": req_id})
            continue
        for field_id in spec.get("fields") or []:
            if field_id not in fields:
                errors.append({"code": "FIELD_NOT_FOUND", "field_id": field_id, "requirement": req_id})


def _validate_structures(structures: list[Any], errors: list[dict[str, Any]]) -> None:
    seen_templates: set[str] = set()
    for index, structure in enumerate(structures):
        path = f"structures[{index}]"
        if not isinstance(structure, dict):
            errors.append({"code": "STRUCTURE_NOT_OBJECT", "path": path})
            continue
        template_id = str(structure.get("template_id") or "")
        if not template_id:
            errors.append({"code": "STRUCTURE_TEMPLATE_ID_MISSING", "path": path})
        elif template_id in seen_templates:
            errors.append({"code": "DUPLICATE_STRUCTURE_TEMPLATE_ID", "template_id": template_id})
        seen_templates.add(template_id)

        leg_ids: set[str] = set()
        same_refs: dict[str, str] = {}
        for leg in structure.get("legs") or []:
            if not isinstance(leg, dict):
                errors.append({"code": "LEG_NOT_OBJECT", "template_id": template_id})
                continue
            leg_id = str(leg.get("leg_id") or "")
            if not leg_id:
                errors.append({"code": "LEG_ID_MISSING", "template_id": template_id})
                continue
            if leg_id in leg_ids:
                errors.append({"code": "DUPLICATE_LEG_ID", "template_id": template_id, "leg_id": leg_id})
            leg_ids.add(leg_id)
            ref = (((leg.get("strike_rule") or {}) if isinstance(leg.get("strike_rule"), dict) else {}).get("same_strike_as"))
            if ref:
                same_refs[leg_id] = str(ref)
        for leg_id, ref in same_refs.items():
            if ref not in leg_ids:
                errors.append({"code": "LEG_REFERENCE_NOT_FOUND", "template_id": template_id, "leg_id": leg_id, "reference": ref})
            if same_refs.get(ref) == leg_id:
                errors.append({"code": "CIRCULAR_LEG_REFERENCE", "template_id": template_id, "leg_id": leg_id, "reference": ref})
        for calc_id in structure.get("calculations") or []:
            ok, code = validate_calculation_id(str(calc_id))
            if not ok:
                errors.append({"code": code, "template_id": template_id, "calculation_id": calc_id})
