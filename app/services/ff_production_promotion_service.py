"""
Sprint 28 — Epic I: Forward Factor Production Promotion Service

Manages the controlled promotion of the Forward Factor strategy from
dry-run research mode to the daily opportunity feed.

Promotion gate
--------------
All of the following must be true for FF rows to appear in the daily
opportunity feed:
1. `FF_PRODUCTION_PROMOTION_ENABLED=true` (env flag — default false)
2. `FORWARD_FACTOR_DRY_RUN` is still True (enforces no live execution)
3. Calibration version is at least 32C.ff.v1
4. FF row carries complete provenance annotation

Rollback
--------
Set `FF_PRODUCTION_PROMOTION_ENABLED=false` to instantly revert — no
code change or redeploy required.

This service is read-only: it never calls providers, writes to brokers,
or modifies strategy logic. It only inspects configuration and row metadata.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app import config
from app.models.data_provenance import (
    CALC_DERIVED,
    CONFIDENCE_SINGLE_SOURCE,
    PROVENANCE_SCHEMA_VERSION,
    SOURCE_CALCULATED,
    DataProvenanceRecord,
)

_MINIMUM_CALIBRATION_VERSION = "32C.ff.v1"
_PROMOTION_SCHEMA_VERSION = "28.I.v1"


def is_promotion_active() -> bool:
    """Return True when FF promotion is currently enabled and safe."""
    if not getattr(config, "FF_PRODUCTION_PROMOTION_ENABLED", False):
        return False
    if not getattr(config, "FORWARD_FACTOR_DRY_RUN", True):
        return False
    cal_ver = str(getattr(config, "FF_CALIBRATION_VERSION", "") or "")
    if not _calibration_version_sufficient(cal_ver):
        return False
    return True


def promotion_status() -> dict[str, Any]:
    """Return a structured status dict for the promotion gate.

    This is suitable for the diagnostics endpoint and operator review.
    """
    enabled = bool(getattr(config, "FF_PRODUCTION_PROMOTION_ENABLED", False))
    dry_run = bool(getattr(config, "FORWARD_FACTOR_DRY_RUN", True))
    cal_ver = str(getattr(config, "FF_CALIBRATION_VERSION", "") or "")
    cal_ok = _calibration_version_sufficient(cal_ver)
    active = enabled and dry_run and cal_ok

    checks: list[dict[str, Any]] = [
        {
            "check": "FF_PRODUCTION_PROMOTION_ENABLED",
            "passed": enabled,
            "value": enabled,
            "note": "Feature flag controlling promotion. Set to true to enable.",
        },
        {
            "check": "FORWARD_FACTOR_DRY_RUN",
            "passed": dry_run,
            "value": dry_run,
            "note": "Must remain true. Dry-run enforces no live execution even when promoted.",
        },
        {
            "check": "calibration_version_sufficient",
            "passed": cal_ok,
            "value": cal_ver,
            "note": f"Calibration version must be >= {_MINIMUM_CALIBRATION_VERSION}.",
        },
    ]

    return {
        "promotion_active": active,
        "promotion_schema_version": _PROMOTION_SCHEMA_VERSION,
        "enabled_flag": enabled,
        "dry_run_enforced": dry_run,
        "calibration_version": cal_ver,
        "calibration_sufficient": cal_ok,
        "can_trade_live": False,
        "rollback_instruction": "Set FF_PRODUCTION_PROMOTION_ENABLED=false to revert instantly.",
        "checks": checks,
        "checked_at": _utcnow(),
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "provider_calls_triggered": False,
        "read_only": True,
    }


def attach_ff_provenance(row: dict[str, Any]) -> None:
    """Attach Sprint 28 provenance annotations to an FF strategy row in-place.

    Adds `_ff_provenance` with source attribution for key computed fields.
    """
    ts = str(row.get("observed_at") or _utcnow())
    front_src = str(row.get("front_iv_source") or "tradier")
    back_src = str(row.get("back_iv_source") or "tradier")
    front_dte = int(row.get("front_dte") or 60)
    back_dte = int(row.get("back_dte") or 90)

    ff_prov = DataProvenanceRecord(
        source=SOURCE_CALCULATED,
        retrieved_at=ts,
        confidence=CONFIDENCE_SINGLE_SOURCE,
        calculation_method=CALC_DERIVED,
        selection_reason=(
            f"Forward Factor derived from {front_src} front-IV ({front_dte}d) "
            f"and {back_src} back-IV ({back_dte}d) via variance term structure."
        ),
    )

    row["_ff_provenance"] = {
        "forward_factor": ff_prov.to_dict(),
        "front_iv": DataProvenanceRecord.single_source(front_src, ts).to_dict(),
        "back_iv": DataProvenanceRecord.single_source(back_src, ts).to_dict(),
        "promotion_active": is_promotion_active(),
        "calibration_version": str(getattr(config, "FF_CALIBRATION_VERSION", "")),
        "provenance_version": _PROMOTION_SCHEMA_VERSION,
        "dry_run": True,
        "can_trade_live": False,
        "schema_version": PROVENANCE_SCHEMA_VERSION,
    }


def validate_ff_row_for_promotion(row: dict[str, Any]) -> dict[str, Any]:
    """Check whether an FF row meets promotion eligibility criteria.

    Returns a validation dict with passed/failed checks. This is purely
    advisory — it does not change row behavior.
    """
    checks: list[dict[str, Any]] = []
    verdict = str(row.get("verdict") or "").upper()
    # FF verdicts use "POSITIVE FF SIGNAL" (PASS) or "WATCH ZONE" rather than literal "PASS"
    is_pass_or_watch = (
        "PASS" in verdict or "WATCH" in verdict
        or "POSITIVE FF SIGNAL" in verdict or "POSITIVE" in verdict
    )
    has_ff = row.get("forward_factor") is not None
    has_provenance = "_ff_provenance" in row
    dry_run = bool(row.get("dry_run"))
    can_trade = bool(row.get("can_trade_live"))

    checks.append({"check": "verdict_is_pass_or_watch", "passed": is_pass_or_watch, "value": verdict})
    checks.append({"check": "forward_factor_present", "passed": has_ff, "value": row.get("forward_factor")})
    checks.append({"check": "provenance_annotated", "passed": has_provenance})
    checks.append({"check": "dry_run_true", "passed": dry_run, "value": dry_run})
    checks.append({"check": "can_trade_live_false", "passed": not can_trade, "value": can_trade})

    passed = all(c["passed"] for c in checks)
    return {
        "ticker": row.get("ticker"),
        "eligible_for_promotion": passed,
        "promotion_active": is_promotion_active(),
        "checks": checks,
        "schema_version": PROVENANCE_SCHEMA_VERSION,
    }


def _calibration_version_sufficient(version: str) -> bool:
    if not version:
        return False
    v = str(version).strip().lower()
    return v >= _MINIMUM_CALIBRATION_VERSION.lower()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
