"""Reusable expiration normalization and pair enumeration.

This service is deliberately provider-free. It accepts already-known expiration
dates and returns complete coverage/audit records so strategies do not reject a
ticker merely because the nearest expiration is invalid.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from typing import Any, Iterable

from app.models.strategy_definition import ExpirationPairRule, ExpirationRecord, ExpirationRequirement


EXPIRATION_ENUMERATION_POLICY_VERSION = "34A.expiration_enumeration.v1"


def normalize_expiration_records(
    raw_expirations: Iterable[Any],
    *,
    valuation_date: date | None = None,
    provider: str = "unknown",
    source_timestamp: str | None = None,
    complete: bool = True,
) -> list[ExpirationRecord]:
    today = valuation_date or date.today()
    records: list[ExpirationRecord] = []
    seen: set[str] = set()
    for raw in raw_expirations or []:
        exp = _extract_date(raw)
        if exp is None:
            records.append(ExpirationRecord(
                expiration=str(raw)[:40],
                dte=None,
                expiration_type="UNKNOWN",
                classification_confidence="invalid",
                provider=provider,
                source_timestamp=source_timestamp,
                data_state="MALFORMED",
                rejection_code="MALFORMED_EXPIRATION",
                rejection_detail="Expiration date could not be parsed.",
            ))
            continue
        iso = exp.isoformat()
        if iso in seen:
            continue
        seen.add(iso)
        dte = (exp - today).days
        expiration_type, confidence = classify_expiration(exp, dte)
        records.append(ExpirationRecord(
            expiration=iso,
            dte=dte,
            expiration_type=expiration_type,
            classification_confidence=confidence,
            provider=provider,
            source_timestamp=source_timestamp,
            data_state="COMPLETE" if complete else "PARTIAL",
        ))
    return sorted(records, key=lambda rec: (rec.dte is None, rec.dte if rec.dte is not None else 99999, rec.expiration))


def classify_expiration(expiration: date, dte: int | None = None) -> tuple[str, str]:
    if dte is not None and dte >= 365:
        return "LEAPS", "inferred_calendar"
    if expiration.weekday() != 4:
        return "UNKNOWN", "inferred_non_friday"
    if 15 <= expiration.day <= 21:
        if expiration.month in {3, 6, 9, 12}:
            return "QUARTERLY", "inferred_calendar"
        return "MONTHLY", "inferred_calendar"
    return "WEEKLY", "inferred_calendar"


def filter_expirations_for_requirement(
    records: list[ExpirationRecord],
    requirement: ExpirationRequirement,
    *,
    event_date: date | str | None = None,
) -> tuple[list[ExpirationRecord], list[dict[str, Any]]]:
    event = _coerce_date(event_date)
    eligible: list[ExpirationRecord] = []
    rejected: list[dict[str, Any]] = []
    for rec in records:
        codes = _record_rejection_codes(rec, requirement, event)
        if codes:
            rejected.append({
                "expiration": rec.expiration,
                "role": requirement.role,
                "dte": rec.dte,
                "expiration_type": rec.expiration_type,
                "rejection_codes": codes,
                "primary_rejection_code": codes[0],
            })
        else:
            eligible.append(rec)
    return eligible, rejected


def enumerate_expiration_pairs(
    records: list[ExpirationRecord],
    requirements: dict[str, ExpirationRequirement],
    pair_rule: ExpirationPairRule,
    *,
    event_date: date | str | None = None,
    max_pairs: int | None = None,
) -> dict[str, Any]:
    event = _coerce_date(event_date)
    front_req = requirements[pair_rule.front_role]
    back_req = requirements[pair_rule.back_role]
    front_candidates, front_rejections = filter_expirations_for_requirement(records, front_req, event_date=event)
    back_candidates, back_rejections = filter_expirations_for_requirement(records, back_req, event_date=event)

    attempts: list[dict[str, Any]] = []
    valid_pairs: list[dict[str, Any]] = []
    for front in front_candidates:
        for back in back_candidates:
            codes: list[str] = []
            if front.dte is None or back.dte is None:
                codes.append("MALFORMED_EXPIRATION")
                gap = None
            else:
                gap = back.dte - front.dte
                if gap <= 0:
                    codes.append("BACK_BEFORE_FRONT")
                if pair_rule.min_gap_days is not None and gap < pair_rule.min_gap_days:
                    codes.append("PAIR_GAP_TOO_SMALL")
                if pair_rule.max_gap_days is not None and gap > pair_rule.max_gap_days:
                    codes.append("PAIR_GAP_TOO_LARGE")
            front_date = _coerce_date(front.expiration)
            back_date = _coerce_date(back.expiration)
            if event and front_date and pair_rule.front_must_expire_before_event and front_date >= event:
                codes.append("SHORT_LEG_SPANS_EARNINGS")
            if event and back_date and pair_rule.back_must_expire_after_event and back_date <= event:
                codes.append("BACK_BEFORE_EVENT")
            if event and front_date and back_date and pair_rule.event_must_be_between and not (front_date < event < back_date):
                codes.append("EVENT_NOT_BETWEEN_LEGS")
            attempt = {
                "front_expiration": front.expiration,
                "back_expiration": back.expiration,
                "front_dte": front.dte,
                "back_dte": back.dte,
                "gap_days": gap,
                "front_expiration_type": front.expiration_type,
                "back_expiration_type": back.expiration_type,
                "valid": not codes,
                "rejection_codes": codes,
                "primary_rejection_code": codes[0] if codes else None,
                "policy_version": EXPIRATION_ENUMERATION_POLICY_VERSION,
            }
            attempts.append(attempt)
            if not codes:
                valid_pairs.append(attempt)

    valid_pairs.sort(key=lambda row: (
        abs((row.get("front_dte") or 0) - _midpoint(front_req.min_dte, front_req.max_dte)),
        abs((row.get("back_dte") or 0) - _midpoint(back_req.min_dte, back_req.max_dte)),
        row.get("gap_days") or 999,
        row.get("front_expiration") or "",
        row.get("back_expiration") or "",
    ))
    if max_pairs is not None:
        valid_pairs = valid_pairs[:max(0, int(max_pairs))]

    code_counts = Counter()
    for item in front_rejections + back_rejections:
        code_counts.update(item.get("rejection_codes") or [])
    for item in attempts:
        code_counts.update(item.get("rejection_codes") or [])

    return {
        "policy_version": EXPIRATION_ENUMERATION_POLICY_VERSION,
        "records_count": len([rec for rec in records if rec.rejection_code is None]),
        "front_candidates": [rec.to_dict() for rec in front_candidates],
        "back_candidates": [rec.to_dict() for rec in back_candidates],
        "front_rejections": front_rejections,
        "back_rejections": back_rejections,
        "pair_attempts": attempts,
        "valid_pairs": valid_pairs,
        "coverage": {
            "front_candidate_count": len(front_candidates),
            "back_candidate_count": len(back_candidates),
            "pair_attempt_count": len(attempts),
            "valid_pair_count": len(valid_pairs),
            "rejected_pair_count": len([row for row in attempts if not row["valid"]]),
            "failure_by_code": dict(sorted(code_counts.items())),
        },
    }


def _record_rejection_codes(
    rec: ExpirationRecord,
    req: ExpirationRequirement,
    event: date | None,
) -> list[str]:
    codes: list[str] = []
    if rec.rejection_code:
        return [rec.rejection_code]
    if rec.dte is None:
            codes.append("MALFORMED_EXPIRATION")
    else:
        if req.min_dte is not None and rec.dte < req.min_dte:
            codes.append("FRONT_BELOW_MIN_DTE" if req.role == "front" else "BACK_BELOW_MIN_DTE")
        if req.max_dte is not None and rec.dte > req.max_dte:
            codes.append("FRONT_ABOVE_MAX_DTE" if req.role == "front" else "BACK_ABOVE_MAX_DTE")
    if rec.expiration_type not in req.allowed_types():
        codes.append("EXPIRATION_TYPE_NOT_ALLOWED")
    exp = _coerce_date(rec.expiration)
    if event and exp:
        if req.relation_to_event == "before" and exp >= event:
            codes.append("FRONT_SPANS_EVENT" if exp == event else "FRONT_AFTER_EVENT")
        elif req.relation_to_event == "after" and exp <= event:
            codes.append("BACK_BEFORE_EVENT")
        if req.min_days_before_event is not None and exp < event:
            days = (event - exp).days
            if days < req.min_days_before_event:
                codes.append("FRONT_TOO_CLOSE_TO_EVENT")
        if req.min_days_after_event is not None and exp > event:
            days = (exp - event).days
            if days < req.min_days_after_event:
                codes.append("BACK_TOO_CLOSE_TO_EVENT")
    return codes


def _extract_date(raw: Any) -> date | None:
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, dict):
        for key in ("expiration", "expiration_date", "date"):
            if key in raw:
                return _extract_date(raw.get(key))
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except (TypeError, ValueError):
        return None


def _coerce_date(value: date | str | None) -> date | None:
    if value is None:
        return None
    return _extract_date(value)


def _midpoint(low: int | None, high: int | None) -> float:
    if low is not None and high is not None:
        return (low + high) / 2.0
    if low is not None:
        return float(low)
    if high is not None:
        return float(high)
    return 0.0
