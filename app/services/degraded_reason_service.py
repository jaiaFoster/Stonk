"""Classify degraded run reasons from already-stored metadata only."""

from __future__ import annotations

from typing import Any


UNKNOWN_REASON = {
    "degraded_reason_code": "UNKNOWN",
    "degraded_reason_label": "Unknown degraded reason",
    "degraded_stage": None,
    "degraded_provider": None,
    "degraded_evidence": [],
    "reason_confidence": "low",
}


def classify_degraded_reason(
    manifest: dict[str, Any] | None = None,
    pipeline_status: dict[str, Any] | None = None,
    provider_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a conservative degraded-reason classification without fetching data."""
    manifest = manifest or {}
    pipeline_status = pipeline_status or {}
    provider_status = provider_status or {}

    existing = _existing_reason(manifest)
    if existing:
        return existing

    stage = _first_text(manifest.get("failed_stage"), manifest.get("timeout_reason"))
    evidence = _collect_evidence(manifest, pipeline_status, provider_status)
    text = " ".join(evidence).lower()
    robinhood = _robinhood_status(pipeline_status, provider_status)

    if "approval" in text and ("timeout" in text or "timed out" in text):
        return _reason(
            "ROBINHOOD_APPROVAL_TIMEOUT",
            "Robinhood approval timed out",
            stage or "positions",
            "robinhood",
            evidence,
            "high",
        )
    if robinhood in {"auth_required", "auth_failed", "rate_limited"} or any(token in text for token in ("auth_required", "auth failed", "rate limited", "429")):
        label = "Robinhood rate limited" if robinhood == "rate_limited" or "429" in text else "Robinhood authentication unavailable"
        return _reason("ROBINHOOD_AUTH_UNAVAILABLE", label, stage or "positions", "robinhood", evidence, "high")
    if (
        manifest.get("has_broker_data") is False
        or robinhood in {"positions_failed", "positions_partial"}
        or "broker position data is unavailable" in text
        or "broker unavailable" in text
        or "positions_failed" in text
    ):
        return _reason("BROKER_DATA_UNAVAILABLE", _clean(manifest.get("degraded_reason")) or "Broker position data unavailable", stage or "positions", "robinhood", evidence, "medium")
    if manifest.get("has_market_data") is False or manifest.get("has_options_data") is False:
        missing = []
        if manifest.get("has_market_data") is False:
            missing.append("market")
        if manifest.get("has_options_data") is False:
            missing.append("options")
        return _reason(
            "MARKET_OR_OPTIONS_DATA_UNAVAILABLE",
            f"{' and '.join(missing).capitalize()} data unavailable",
            stage,
            None,
            evidence,
            "medium",
        )
    if "timeout" in text or "stale lock" in text:
        return _reason("RUN_TIMEOUT_OR_STALE_LOCK", "Run timeout or stale lock recovery", stage, None, evidence, "medium")
    if any(provider in text for provider in ("tradier", "finnhub", "alpha vantage", "provider")) and any(token in text for token in ("failed", "unavailable", "partial", "error")):
        return _reason("PROVIDER_PARTIAL_FAILURE", "Provider partial failure", stage, None, evidence, "medium")
    return dict(UNKNOWN_REASON)


def _existing_reason(manifest: dict[str, Any]) -> dict[str, Any] | None:
    code = _clean(manifest.get("degraded_reason_code"))
    if not code:
        return None
    return _reason(
        code,
        _clean(manifest.get("degraded_reason_label")) or str(code).replace("_", " ").title(),
        _clean(manifest.get("degraded_stage")),
        _clean(manifest.get("degraded_provider")),
        manifest.get("degraded_evidence") if isinstance(manifest.get("degraded_evidence"), list) else [],
        _clean(manifest.get("reason_confidence")) or "medium",
    )


def _collect_evidence(manifest: dict[str, Any], pipeline_status: dict[str, Any], provider_status: dict[str, Any]) -> list[str]:
    evidence: list[str] = []
    for key in ("status", "report_quality", "failed_stage", "timeout_reason", "degraded_reason", "error"):
        if manifest.get(key):
            evidence.append(f"manifest.{key}={manifest.get(key)}")
    for key in ("has_broker_data", "has_market_data", "has_options_data"):
        if manifest.get(key) is False:
            evidence.append(f"manifest.{key}=false")
    for item in list(manifest.get("errors") or [])[:3]:
        evidence.append(f"manifest.error={item}")
    for item in list(pipeline_status.get("errors") or [])[:3]:
        evidence.append(f"pipeline.error={_message(item)}")
    for item in list(pipeline_status.get("warnings") or [])[:3]:
        evidence.append(f"pipeline.warning={_message(item)}")
    for step in list(pipeline_status.get("steps") or [])[:20]:
        if isinstance(step, dict) and str(step.get("status") or "").lower() in {"warning", "error", "skipped"}:
            evidence.append(f"pipeline.step.{step.get('key')}={step.get('message')}")
    rh = (provider_status or {}).get("robinhood") if isinstance(provider_status, dict) else None
    if isinstance(rh, dict):
        for key in ("status", "error", "rate_limited", "auth_required", "positions_available", "stale_fallback"):
            if rh.get(key) not in (None, "", False):
                evidence.append(f"robinhood.{key}={rh.get(key)}")
    return [_clip(str(item)) for item in evidence if str(item).strip()][:12]


def _robinhood_status(pipeline_status: dict[str, Any], provider_status: dict[str, Any]) -> str:
    rh = (provider_status or {}).get("robinhood") if isinstance(provider_status, dict) else None
    if isinstance(rh, dict) and rh.get("status"):
        return str(rh.get("status")).lower()
    for step in pipeline_status.get("steps") or []:
        meta = step.get("meta") if isinstance(step, dict) else {}
        step_rh = (meta or {}).get("provider_status") if isinstance(meta, dict) else None
        if isinstance(step_rh, dict) and step_rh.get("status"):
            return str(step_rh.get("status")).lower()
    return ""


def _reason(code: str, label: str, stage: Any, provider: Any, evidence: list[Any], confidence: str) -> dict[str, Any]:
    return {
        "degraded_reason_code": str(code),
        "degraded_reason_label": str(label),
        "degraded_stage": _clean(stage),
        "degraded_provider": _clean(provider),
        "degraded_evidence": [_clip(str(item)) for item in evidence if str(item).strip()][:8],
        "reason_confidence": str(confidence),
    }


def _message(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("message") or item)
    return str(item)


def _first_text(*values: Any) -> str | None:
    for value in values:
        cleaned = _clean(value)
        if cleaned:
            return cleaned
    return None


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _clip(value: str) -> str:
    return value[:240]
