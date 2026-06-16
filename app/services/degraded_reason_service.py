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

    stage = _first_text(
        manifest.get("failed_stage"),
        manifest.get("timeout_reason"),
        manifest.get("degraded_stage"),
        manifest.get("degraded_timeout_reason"),
    )
    evidence = _collect_evidence(manifest, pipeline_status, provider_status)
    text = " ".join(evidence).lower()
    robinhood = _robinhood_status(pipeline_status, provider_status)
    inferred_provider = _first_text(manifest.get("degraded_provider"), _infer_provider(provider_status, text))

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
        return _reason("RUN_TIMEOUT_OR_STALE_LOCK", "Run timeout or stale lock recovery", stage, inferred_provider, evidence, "medium")
    if any(provider in text for provider in ("tradier", "finnhub", "alpha vantage", "provider")) and any(token in text for token in ("failed", "unavailable", "partial", "error")):
        return _reason("PROVIDER_PARTIAL_FAILURE", "Provider partial failure", stage, inferred_provider, evidence, "medium")
    return dict(UNKNOWN_REASON)


def build_degraded_evidence_fields(
    *,
    status: str | None = None,
    report_quality: str | None = None,
    pipeline_status: dict[str, Any] | None = None,
    provider_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build stored degraded-run evidence from already-available run metadata."""
    pipeline_status = pipeline_status or {}
    provider_status = provider_status or {}
    status_text = str(status or "").lower()
    quality_text = str(report_quality or "").upper()
    if "degraded" not in status_text and quality_text != "SUCCESS_DEGRADED" and status_text not in {"error", "timeout", "failed"}:
        return {}

    evidence = _collect_evidence(
        {"status": status, "report_quality": report_quality},
        pipeline_status,
        provider_status,
    )
    text = " ".join(evidence).lower()
    stage = _infer_stage(pipeline_status, text)
    provider = _infer_provider(provider_status, text)
    timeout_reason = _infer_timeout_reason(pipeline_status, text)
    broker_status = _robinhood_status(pipeline_status, provider_status)
    rh = provider_status.get("robinhood") if isinstance(provider_status, dict) else None
    stale_fallback = False
    if isinstance(rh, dict):
        stale_fallback = bool(rh.get("stale_fallback"))
        summary = rh.get("account_summary")
        if isinstance(summary, dict):
            stale_fallback = stale_fallback or bool(summary.get("stale_fallback"))

    provider_errors = _provider_error_summaries(provider_status, pipeline_status)
    fields: dict[str, Any] = {
        "degraded_stage": stage,
        "degraded_provider": provider,
        "degraded_timeout": bool(timeout_reason),
        "degraded_timeout_reason": timeout_reason,
        "degraded_auth_status": _auth_status(provider_status, text),
        "degraded_broker_status": broker_status or None,
        "degraded_provider_errors": provider_errors,
        "degraded_stale_fallback_used": stale_fallback,
        "degraded_evidence": evidence,
    }
    return {key: value for key, value in fields.items() if value not in (None, "", [], False)}


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
    for key in (
        "status",
        "report_quality",
        "failed_stage",
        "timeout_reason",
        "degraded_reason",
        "degraded_timeout_reason",
        "degraded_auth_status",
        "degraded_broker_status",
        "error",
    ):
        if manifest.get(key):
            evidence.append(f"manifest.{key}={manifest.get(key)}")
    for item in list(manifest.get("degraded_provider_errors") or [])[:3]:
        evidence.append(f"manifest.provider_error={item}")
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


def _infer_stage(pipeline_status: dict[str, Any], evidence_text: str) -> str | None:
    for key in ("failed_stage", "timeout_stage", "degraded_stage"):
        value = _clean(pipeline_status.get(key))
        if value:
            return value
    for step in pipeline_status.get("steps") or []:
        if isinstance(step, dict) and str(step.get("status") or "").lower() in {"warning", "error", "skipped"}:
            return _clean(step.get("key") or step.get("step"))
    if any(token in evidence_text for token in ("robinhood", "broker", "positions")):
        return "positions"
    if "tradier" in evidence_text or "option" in evidence_text:
        return "market_data"
    return None


def _infer_provider(provider_status: dict[str, Any], evidence_text: str) -> str | None:
    for provider, status in (provider_status or {}).items():
        if not isinstance(status, dict):
            continue
        state = str(status.get("status") or "").lower()
        if state and state not in {"ok", "success", "complete", "available"}:
            return str(provider)
        if status.get("error") or status.get("rate_limited") or status.get("auth_required"):
            return str(provider)
    for provider in ("robinhood", "tradier", "finnhub", "alpha_vantage"):
        if provider.replace("_", " ") in evidence_text or provider in evidence_text:
            return provider
    return None


def _infer_timeout_reason(pipeline_status: dict[str, Any], evidence_text: str) -> str | None:
    value = _clean(pipeline_status.get("timeout_reason"))
    if value:
        return value
    if "approval" in evidence_text and ("timeout" in evidence_text or "timed out" in evidence_text):
        return "robinhood_approval_timeout"
    if "timeout" in evidence_text or "timed out" in evidence_text:
        return "timeout"
    return None


def _auth_status(provider_status: dict[str, Any], evidence_text: str) -> str | None:
    rh = (provider_status or {}).get("robinhood") if isinstance(provider_status, dict) else None
    if isinstance(rh, dict):
        status = _clean(rh.get("status"))
        if status in {"auth_required", "auth_failed", "auth_timeout", "rate_limited"}:
            return status
        if rh.get("auth_required"):
            return "auth_required"
        if rh.get("rate_limited"):
            return "rate_limited"
    if "auth_required" in evidence_text or "approval" in evidence_text:
        return "auth_required"
    if "rate limited" in evidence_text or "429" in evidence_text:
        return "rate_limited"
    return None


def _provider_error_summaries(provider_status: dict[str, Any], pipeline_status: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for provider, status in (provider_status or {}).items():
        if isinstance(status, dict):
            if status.get("error"):
                errors.append(f"{provider}: {status.get('error')}")
            elif str(status.get("status") or "").lower() in {"failed", "partial", "unavailable", "auth_failed", "auth_required", "rate_limited", "positions_failed"}:
                errors.append(f"{provider}: status={status.get('status')}")
    for item in list(pipeline_status.get("errors") or [])[:3]:
        errors.append(_message(item))
    for item in list(pipeline_status.get("warnings") or [])[:3]:
        message = _message(item)
        if any(token in message.lower() for token in ("failed", "unavailable", "timeout", "timed out", "partial", "auth", "429")):
            errors.append(message)
    return [_clip(str(item)) for item in errors if str(item).strip()][:6]


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
