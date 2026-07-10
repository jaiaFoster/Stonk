"""Custom strategy definition validation — structural, catalog, and semantic checks.

ASA Patch 31B.
Returns machine-readable ValidationResult with stable error codes.
All validation is purely local — no provider calls, no broker writes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models.custom_strategy_models import (
    ALLOWED_LOGIC_OPERATORS,
    ALLOWED_SIGNALS,
    ALLOWED_STATUSES,
    CUSTOM_STRATEGY_SCHEMA_VERSION,
)

# Hard limits for structural validation.
_MAX_CONDITION_GROUPS = 20
_MAX_CONDITIONS_PER_GROUP = 50
_MAX_TOTAL_CONDITIONS = 200
_MAX_GROUP_DEPTH = 5
_MAX_NAME_LENGTH = 120
_MAX_DESCRIPTION_LENGTH = 2000
_MAX_UNIVERSE_SIZE = 500


@dataclass
class ValidationError:
    code: str
    message: str
    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "path": self.path}


@dataclass
class ValidationResult:
    valid: bool
    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": [e.to_dict() for e in self.errors],
            "warnings": self.warnings,
        }


def validate_custom_strategy(definition: dict[str, Any]) -> ValidationResult:
    """Run all validation passes on a custom strategy definition dict.

    Order: structural → catalog → semantic.
    Returns on first category of failures to keep errors actionable.
    """
    errors: list[ValidationError] = []
    warnings: list[str] = []

    _validate_structural(definition, errors)
    if errors:
        return ValidationResult(valid=False, errors=errors, warnings=warnings)

    _validate_catalog(definition, errors, warnings)
    if errors:
        return ValidationResult(valid=False, errors=errors, warnings=warnings)

    _validate_semantic(definition, errors, warnings)
    return ValidationResult(valid=not errors, errors=errors, warnings=warnings)


# ── Structural validation ──────────────────────────────────────────────────────

def _validate_structural(definition: dict[str, Any], errors: list[ValidationError]) -> None:
    if not isinstance(definition, dict):
        errors.append(ValidationError("INVALID_TYPE", "Definition must be a JSON object.", ""))
        return

    # Required top-level fields.
    for required_field in ("name", "conditions", "output"):
        if required_field not in definition:
            errors.append(ValidationError(
                "MISSING_REQUIRED_FIELD",
                f"Required field '{required_field}' is missing.",
                required_field,
            ))

    if errors:
        return

    name = definition.get("name", "")
    if not isinstance(name, str) or not name.strip():
        errors.append(ValidationError("INVALID_NAME", "Name must be a non-empty string.", "name"))
    elif len(name) > _MAX_NAME_LENGTH:
        errors.append(ValidationError(
            "NAME_TOO_LONG",
            f"Name exceeds {_MAX_NAME_LENGTH} characters.",
            "name",
        ))

    description = definition.get("description", "")
    if description and len(str(description)) > _MAX_DESCRIPTION_LENGTH:
        errors.append(ValidationError(
            "DESCRIPTION_TOO_LONG",
            f"Description exceeds {_MAX_DESCRIPTION_LENGTH} characters.",
            "description",
        ))

    status = definition.get("status")
    if status is not None and status not in ALLOWED_STATUSES:
        errors.append(ValidationError(
            "INVALID_STATUS",
            f"Status '{status}' is not allowed. Must be one of: {sorted(ALLOWED_STATUSES)}.",
            "status",
        ))

    universe = definition.get("universe", [])
    if not isinstance(universe, list):
        errors.append(ValidationError("INVALID_UNIVERSE", "Universe must be a list.", "universe"))
    elif len(universe) > _MAX_UNIVERSE_SIZE:
        errors.append(ValidationError(
            "UNIVERSE_TOO_LARGE",
            f"Universe exceeds {_MAX_UNIVERSE_SIZE} entries.",
            "universe",
        ))

    # Policy constraint: dry_run must never be overridden to False. Check in structural
    # pass so this is always caught regardless of whether 'dry_run' is in the catalog.
    conditions_raw = definition.get("conditions", [])
    if isinstance(conditions_raw, list):
        for gi, grp in enumerate(conditions_raw):
            if not isinstance(grp, dict):
                continue
            for ci, cond in enumerate(grp.get("conditions") or []):
                if not isinstance(cond, dict):
                    continue
                if str(cond.get("field_id") or "") == "dry_run" and cond.get("value") is False:
                    errors.append(ValidationError(
                        "DRY_RUN_OVERRIDE_FORBIDDEN",
                        "Custom strategies may not override dry_run to False. "
                        "Forward Factor remains dry-run by policy.",
                        f"conditions[{gi}].conditions[{ci}].value",
                    ))

    conditions = definition.get("conditions", [])
    if not isinstance(conditions, list):
        errors.append(ValidationError("INVALID_CONDITIONS", "Conditions must be a list of condition groups.", "conditions"))
        return

    if len(conditions) > _MAX_CONDITION_GROUPS:
        errors.append(ValidationError(
            "TOO_MANY_GROUPS",
            f"Exceeds maximum of {_MAX_CONDITION_GROUPS} condition groups.",
            "conditions",
        ))

    total_conditions = 0
    for group_index, group in enumerate(conditions):
        path = f"conditions[{group_index}]"
        if not isinstance(group, dict):
            errors.append(ValidationError("INVALID_GROUP_TYPE", "Each condition group must be a JSON object.", path))
            continue
        logic = group.get("logic")
        if logic not in ALLOWED_LOGIC_OPERATORS:
            errors.append(ValidationError(
                "INVALID_LOGIC_OPERATOR",
                f"Logic operator '{logic}' is not allowed. Must be AND or OR.",
                f"{path}.logic",
            ))
        group_conditions = group.get("conditions", [])
        if not isinstance(group_conditions, list):
            errors.append(ValidationError("INVALID_GROUP_CONDITIONS", "Group conditions must be a list.", f"{path}.conditions"))
            continue
        if len(group_conditions) > _MAX_CONDITIONS_PER_GROUP:
            errors.append(ValidationError(
                "TOO_MANY_CONDITIONS_IN_GROUP",
                f"Group exceeds {_MAX_CONDITIONS_PER_GROUP} conditions.",
                path,
            ))
        total_conditions += len(group_conditions)
        for cond_index, cond in enumerate(group_conditions):
            cond_path = f"{path}.conditions[{cond_index}]"
            if not isinstance(cond, dict):
                errors.append(ValidationError("INVALID_CONDITION_TYPE", "Each condition must be a JSON object.", cond_path))
                continue
            for req in ("field_id", "operator", "value"):
                if req not in cond:
                    errors.append(ValidationError(
                        "MISSING_CONDITION_FIELD",
                        f"Condition missing required field '{req}'.",
                        f"{cond_path}.{req}",
                    ))

    if total_conditions > _MAX_TOTAL_CONDITIONS:
        errors.append(ValidationError(
            "TOO_MANY_TOTAL_CONDITIONS",
            f"Total condition count {total_conditions} exceeds maximum {_MAX_TOTAL_CONDITIONS}.",
            "conditions",
        ))

    output = definition.get("output", {})
    if not isinstance(output, dict):
        errors.append(ValidationError("INVALID_OUTPUT", "Output must be a JSON object.", "output"))
    else:
        signal = output.get("signal")
        if not signal:
            errors.append(ValidationError("MISSING_OUTPUT_SIGNAL", "Output.signal is required.", "output.signal"))
        elif signal not in ALLOWED_SIGNALS:
            errors.append(ValidationError(
                "INVALID_OUTPUT_SIGNAL",
                f"Signal '{signal}' is not allowed. Must be one of: {sorted(ALLOWED_SIGNALS)}.",
                "output.signal",
            ))


# ── Catalog validation ─────────────────────────────────────────────────────────

def _validate_catalog(
    definition: dict[str, Any],
    errors: list[ValidationError],
    warnings: list[str],
) -> None:
    try:
        from app.services.strategy_data_catalog_service import field_catalog
        catalog = field_catalog()
    except Exception as exc:
        errors.append(ValidationError(
            "CATALOG_UNAVAILABLE",
            f"Strategy field catalog could not be loaded: {exc}",
            "",
        ))
        return

    conditions = definition.get("conditions", [])
    for group_index, group in enumerate(conditions):
        if not isinstance(group, dict):
            continue
        group_conditions = group.get("conditions", [])
        if not isinstance(group_conditions, list):
            continue
        for cond_index, cond in enumerate(group_conditions):
            if not isinstance(cond, dict):
                continue
            cond_path = f"conditions[{group_index}].conditions[{cond_index}]"
            field_id = cond.get("field_id")
            operator = cond.get("operator")
            value = cond.get("value")

            field_def = catalog.get(field_id) if field_id else None
            if field_id and field_def is None:
                errors.append(ValidationError(
                    "UNKNOWN_FIELD",
                    f"Field '{field_id}' is not in the strategy field catalog. Unknown fields fail closed.",
                    f"{cond_path}.field_id",
                ))
                continue

            if field_def is None:
                continue

            if operator and operator not in field_def.allowed_operators:
                errors.append(ValidationError(
                    "OPERATOR_NOT_ALLOWED",
                    f"Operator '{operator}' is not allowed for field '{field_id}' (type={field_def.value_type}). "
                    f"Allowed: {list(field_def.allowed_operators)}.",
                    f"{cond_path}.operator",
                ))

            _validate_value_shape(field_def, operator, value, cond_path, errors, warnings)


def _validate_value_shape(
    field_def: Any,
    operator: str | None,
    value: Any,
    cond_path: str,
    errors: list[ValidationError],
    warnings: list[str],
) -> None:
    if operator is None:
        return
    value_type = field_def.value_type

    if operator in ("is_null", "is_not_null"):
        if value is not None:
            warnings.append(f"{cond_path}: value should be null for operator '{operator}'.")
        return

    if operator == "in" or operator == "not_in":
        if not isinstance(value, list):
            errors.append(ValidationError(
                "INVALID_VALUE_TYPE",
                f"Operator '{operator}' requires a list value for field '{field_def.field_id}'.",
                f"{cond_path}.value",
            ))
            return
        if field_def.enum_values:
            for item in value:
                if str(item) not in field_def.enum_values:
                    errors.append(ValidationError(
                        "UNKNOWN_ENUM_VALUE",
                        f"Value '{item}' is not a valid enum value for field '{field_def.field_id}'. "
                        f"Allowed: {list(field_def.enum_values)}.",
                        f"{cond_path}.value",
                    ))
        return

    if operator == "between":
        if not isinstance(value, dict) or "min" not in value or "max" not in value:
            errors.append(ValidationError(
                "INVALID_BETWEEN_VALUE",
                "Operator 'between' requires an object with 'min' and 'max' keys.",
                f"{cond_path}.value",
            ))
        return

    if value_type in ("float", "int", "number"):
        if not isinstance(value, (int, float)):
            errors.append(ValidationError(
                "INVALID_VALUE_TYPE",
                f"Field '{field_def.field_id}' requires a numeric value.",
                f"{cond_path}.value",
            ))
    elif value_type == "bool":
        if not isinstance(value, bool):
            errors.append(ValidationError(
                "INVALID_VALUE_TYPE",
                f"Field '{field_def.field_id}' requires a boolean value.",
                f"{cond_path}.value",
            ))
    elif value_type == "enum":
        if field_def.enum_values and str(value) not in field_def.enum_values:
            errors.append(ValidationError(
                "UNKNOWN_ENUM_VALUE",
                f"Value '{value}' is not valid for field '{field_def.field_id}'. "
                f"Allowed: {list(field_def.enum_values)}.",
                f"{cond_path}.value",
            ))


# ── Semantic validation ────────────────────────────────────────────────────────

def _validate_semantic(
    definition: dict[str, Any],
    errors: list[ValidationError],
    warnings: list[str],
) -> None:
    conditions = definition.get("conditions", [])
    for group_index, group in enumerate(conditions):
        if not isinstance(group, dict):
            continue
        group_conditions = group.get("conditions", [])
        if not isinstance(group_conditions, list):
            continue
        for cond_index, cond in enumerate(group_conditions):
            if not isinstance(cond, dict):
                continue
            cond_path = f"conditions[{group_index}].conditions[{cond_index}]"
            operator = cond.get("operator")
            value = cond.get("value")
            field_id = str(cond.get("field_id") or "")

            if operator == "between" and isinstance(value, dict):
                min_val = value.get("min")
                max_val = value.get("max")
                if min_val is not None and max_val is not None:
                    try:
                        if float(min_val) > float(max_val):
                            errors.append(ValidationError(
                                "BETWEEN_MIN_EXCEEDS_MAX",
                                f"between.min ({min_val}) must not exceed between.max ({max_val}).",
                                f"{cond_path}.value",
                            ))
                    except (TypeError, ValueError):
                        pass

            if "dte" in field_id.lower() and isinstance(value, (int, float)) and value < 0:
                errors.append(ValidationError(
                    "NEGATIVE_DTE",
                    f"Field '{field_id}' (DTE) must be non-negative.",
                    f"{cond_path}.value",
                ))

    # dry_run override is checked in structural validation (always) — not repeated here.
