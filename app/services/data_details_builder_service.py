"""
Sprint 28 — Epic K: Data Details Builder Service

Builds expandable "Data Details" panels for any strategy row.  The output
is structured so the frontend can render a collapsible "Data Details" section
next to each candidate row showing users exactly what data ASA used, where
it came from, and how confident the system is.

Panel structure
---------------
Each panel has:
- section: identifier string ("earnings", "options", "greeks", "quote", "forward_factor")
- label: human-readable title
- fields: list of FieldDetail objects
- confidence_summary: overall section confidence
- warnings: any data quality issues

FieldDetail structure
---------------------
- name: field slug
- label: human-readable label
- value: formatted display value
- source: data source slug
- confidence: confidence tier
- retrieved_at: ISO timestamp or None
- notes: list of explanatory strings

Design: purely read-only, never calls providers.
"""

from __future__ import annotations

from typing import Any

from app.models.data_provenance import (
    CONFIDENCE_CONFIRMED,
    CONFIDENCE_DISPUTED,
    CONFIDENCE_NO_DATA,
    CONFIDENCE_SINGLE_SOURCE,
    PROVENANCE_SCHEMA_VERSION,
)
from app.services.data_provenance_service import (
    compact_provenance,
    freshness_label,
    freshness_age_seconds,
)

_SECTION_SCHEMA_VERSION = "28.K.v1"


# ─── Public API ───────────────────────────────────────────────────────────────

def build_data_details(row: dict[str, Any], strategy_id: str) -> dict[str, Any]:
    """Build the full _data_details payload for a strategy row.

    Attach as `row['_data_details'] = build_data_details(row, strategy_id)`.
    """
    panels: list[dict[str, Any]] = []

    if strategy_id == "earnings_calendar":
        panels.append(_earnings_panel(row))
        panels.append(_options_panel(row))
        panels.append(_greeks_panel(row))

    elif strategy_id == "forward_factor_calendar":
        panels.append(_forward_factor_panel(row))
        panels.append(_options_panel(row))
        panels.append(_greeks_panel(row))

    elif strategy_id in ("skew_momentum_vertical", "stock_momentum"):
        panels.append(_quote_panel(row))
        panels.append(_momentum_panel(row))

    # Universal: always include data diagnostics if attached
    diag = row.get("_data_diagnostics")
    if isinstance(diag, dict):
        panels.append(_diagnostics_panel(diag))

    return {
        "panels": panels,
        "panel_count": len(panels),
        "schema_version": _SECTION_SCHEMA_VERSION,
        "provenance_schema_version": PROVENANCE_SCHEMA_VERSION,
        "provider_calls_triggered": False,
        "read_only": True,
    }


def attach_data_details(row: dict[str, Any], strategy_id: str) -> None:
    """Attach _data_details to a strategy row in-place."""
    row["_data_details"] = build_data_details(row, strategy_id)


def data_details_compact(row: dict[str, Any]) -> dict[str, Any] | None:
    """Return a compact summary of data details (for API responses)."""
    dd = (row or {}).get("_data_details")
    if not isinstance(dd, dict):
        return None
    return {
        "panel_count": dd.get("panel_count", 0),
        "panels": [
            {
                "section": p.get("section"),
                "label": p.get("label"),
                "confidence_summary": p.get("confidence_summary"),
                "warning_count": len(p.get("warnings") or []),
                "field_count": len(p.get("fields") or []),
            }
            for p in (dd.get("panels") or [])
        ],
        "schema_version": dd.get("schema_version"),
    }


# ─── Section builders ─────────────────────────────────────────────────────────

def _earnings_panel(row: dict[str, Any]) -> dict[str, Any]:
    conf_report = row.get("_confidence") or {}
    sources = conf_report.get("sources_returned_data") or row.get("earnings_sources_seen") or []
    date_conf = conf_report.get("date_confidence") or row.get("earnings_date_confidence") or CONFIDENCE_NO_DATA
    conflict = bool(conf_report.get("conflict_detected") or row.get("date_conflict"))
    earnings_date = row.get("earnings_date") or conf_report.get("earnings_date")
    session = row.get("earnings_time") or row.get("session_label") or conf_report.get("earnings_session")
    retrieved_at = conf_report.get("retrieved_at") or row.get("retrieved_at")

    warnings: list[str] = []
    if conflict:
        warnings.append(conf_report.get("conflict_summary") or "Provider date conflict detected.")
    if not sources:
        warnings.append("No provider sources recorded for this earnings date.")
    if date_conf == CONFIDENCE_SINGLE_SOURCE:
        warnings.append("Single-source earnings date — lower confidence.")

    fields = [
        _field("earnings_date", "Earnings Date", earnings_date,
               source=_first_source(sources), confidence=date_conf, retrieved_at=retrieved_at),
        _field("earnings_session", "Session", session,
               source=_first_source(sources), confidence=date_conf if session else CONFIDENCE_NO_DATA),
        _field("sources_seen", "Data Sources", ", ".join(sources) if sources else "None",
               source="composite", confidence=CONFIDENCE_CONFIRMED if len(sources) >= 2 else CONFIDENCE_SINGLE_SOURCE),
        _field("trust_label", "Trust Label", row.get("earnings_trust_label"),
               source="asa_computed", confidence=date_conf),
    ]

    # Provider rows from confidence report
    for pr_row in (conf_report.get("provider_rows") or []):
        prov_slug = pr_row.get("provider") or "unknown"
        pname = pr_row.get("provider_display") or prov_slug.title()
        fields.append(_field(
            f"provider_{prov_slug}", f"{pname} date",
            pr_row.get("date_reported"),
            source=prov_slug,
            confidence=CONFIDENCE_SINGLE_SOURCE if pr_row.get("date_reported") else CONFIDENCE_NO_DATA,
            retrieved_at=pr_row.get("retrieved_at"),
            notes=[f"Session: {pr_row.get('session_reported') or 'not reported'}",
                   f"Confirmed: {'yes' if pr_row.get('timestamp_confirmed') else 'no'}"],
        ))

    return _panel("earnings", "Earnings Data", fields, date_conf, warnings)


def _options_panel(row: dict[str, Any]) -> dict[str, Any]:
    front_bid = row.get("front_bid") or row.get("front_leg_bid")
    front_ask = row.get("front_ask") or row.get("front_leg_ask")
    back_bid = row.get("back_bid") or row.get("back_leg_bid")
    back_ask = row.get("back_ask") or row.get("back_leg_ask")
    front_oi = row.get("front_open_interest") or row.get("min_leg_open_interest")
    back_oi = row.get("back_open_interest")
    debit = row.get("conservative_debit") or row.get("debit")
    strike = row.get("strike")
    front_exp = row.get("front_expiration")
    back_exp = row.get("back_expiration")

    chain_prov = row.get("_chain_provenance") or {}
    chain_provider = str(chain_prov.get("provider") or row.get("chain_provider") or "tradier")
    retrieved_at = chain_prov.get("retrieved_at") or row.get("chain_retrieved_at")
    age = freshness_age_seconds({"retrieved_at": retrieved_at}) if retrieved_at else None
    age_label = freshness_label(age)

    warnings: list[str] = []
    if age_label == "stale":
        warnings.append(f"Chain data may be stale ({age}s since retrieval).")
    if age_label == "aging":
        warnings.append(f"Chain data is aging ({age}s since retrieval).")

    conf = CONFIDENCE_SINGLE_SOURCE if front_bid is not None else CONFIDENCE_NO_DATA
    fields = [
        _field("strike", "Strike", _fmt_price(strike), source="asa_computed", confidence=conf),
        _field("front_expiration", "Front Expiration", front_exp, source=chain_provider, confidence=conf),
        _field("back_expiration", "Back Expiration", back_exp, source=chain_provider, confidence=conf),
        _field("front_bid_ask", "Front Bid/Ask", _fmt_bid_ask(front_bid, front_ask),
               source=chain_provider, confidence=conf, retrieved_at=retrieved_at),
        _field("back_bid_ask", "Back Bid/Ask", _fmt_bid_ask(back_bid, back_ask),
               source=chain_provider, confidence=conf, retrieved_at=retrieved_at),
        _field("conservative_debit", "Conservative Debit", _fmt_price(debit),
               source="asa_computed", confidence=conf),
        _field("front_open_interest", "Front OI", _fmt_int(front_oi), source=chain_provider, confidence=conf),
        _field("back_open_interest", "Back OI", _fmt_int(back_oi), source=chain_provider, confidence=conf),
    ]
    if retrieved_at:
        fields.append(_field("chain_freshness", "Chain Freshness", age_label,
                              source=chain_provider, confidence=conf, retrieved_at=retrieved_at))

    return _panel("options", "Options Chain Data", fields, conf, warnings)


def _greeks_panel(row: dict[str, Any]) -> dict[str, Any]:
    greek_prov = row.get("_greek_provenance") or {}
    greeks_data = (greek_prov.get("greeks") or {}) if greek_prov else {}
    provider = str(greek_prov.get("provider") or row.get("chain_provider") or "tradier")
    retrieved_at = greek_prov.get("retrieved_at")
    model_notes = str(greek_prov.get("model_notes") or "")
    approx = bool(greek_prov.get("approximation"))

    warnings: list[str] = []
    if approx:
        warnings.append("Greeks may be approximated — source provider not verified.")
    if not greeks_data:
        warnings.append("No Greek attribution data available for this row.")

    fields: list[dict[str, Any]] = []
    labels = {
        "delta": "Delta", "gamma": "Gamma", "theta": "Theta",
        "vega": "Vega", "rho": "Rho", "iv": "Implied Volatility",
    }
    for greek, label in labels.items():
        gd = greeks_data.get(greek) or {}
        val = gd.get("value")
        if val is None:
            val = row.get(greek) or row.get(f"front_{greek}") or row.get(f"back_{greek}")
        conf = gd.get("confidence") or (CONFIDENCE_SINGLE_SOURCE if val is not None else CONFIDENCE_NO_DATA)
        src = gd.get("source") or provider
        method = gd.get("calculation_method") or "unknown"
        fields.append(_field(greek, label, _fmt_float(val, 4), source=src, confidence=conf,
                              retrieved_at=retrieved_at,
                              notes=[f"Method: {method}"] if method != "unknown" else []))

    conf_overall = CONFIDENCE_SINGLE_SOURCE if any(f["value"] != "—" for f in fields) else CONFIDENCE_NO_DATA
    if model_notes:
        warnings.append(model_notes)

    return _panel("greeks", "Options Greeks", fields, conf_overall, warnings)


def _forward_factor_panel(row: dict[str, Any]) -> dict[str, Any]:
    ff = row.get("forward_factor")
    front_iv = row.get("front_iv") or row.get("front_leg_iv")
    back_iv = row.get("back_iv") or row.get("back_leg_iv")
    front_dte = row.get("front_dte")
    back_dte = row.get("back_dte")
    verdict = row.get("verdict")
    miss_distance = row.get("miss_distance")
    calibration_ver = getattr(__import__("app.config", fromlist=["config"]), "FF_CALIBRATION_VERSION", "")
    ff_prov = row.get("_ff_provenance") or {}

    iv_source = str(ff_prov.get("front_iv", {}).get("source") or "tradier") if ff_prov else "tradier"
    conf = CONFIDENCE_SINGLE_SOURCE if ff is not None else CONFIDENCE_NO_DATA

    warnings: list[str] = []
    if row.get("near_miss_ff"):
        miss_str = f"{miss_distance:.4f}" if miss_distance is not None else "unknown"
        warnings.append(f"NEAR MISS — missed threshold by {miss_str}.")
    if not row.get("watch_zone_ff") and not row.get("near_miss_ff") and "PASS" not in str(verdict):
        warnings.append("Not a PASS or WATCH signal — diagnostic only.")

    fields = [
        _field("forward_factor", "Forward Factor", _fmt_float(ff, 4),
               source="asa_computed", confidence=conf,
               notes=["Derived from front/back IV term structure."]),
        _field("front_iv", "Front IV", _fmt_float(front_iv, 4),
               source=iv_source, confidence=conf),
        _field("back_iv", "Back IV", _fmt_float(back_iv, 4),
               source=iv_source, confidence=conf),
        _field("front_dte", "Front DTE", str(front_dte) if front_dte is not None else "—",
               source="asa_computed", confidence=conf),
        _field("back_dte", "Back DTE", str(back_dte) if back_dte is not None else "—",
               source="asa_computed", confidence=conf),
        _field("calibration_version", "Calibration Version", calibration_ver,
               source="asa_config", confidence=CONFIDENCE_SINGLE_SOURCE),
        _field("verdict", "FF Verdict", verdict or "—",
               source="asa_computed", confidence=conf),
    ]
    if miss_distance is not None:
        fields.append(_field("miss_distance", "Miss Distance", _fmt_float(miss_distance, 4),
                              source="asa_computed", confidence=conf))

    return _panel("forward_factor", "Forward Factor Signal", fields, conf, warnings)


def _quote_panel(row: dict[str, Any]) -> dict[str, Any]:
    price = row.get("price") or row.get("underlying_price") or row.get("last_price")
    retrieved_at = row.get("quote_retrieved_at") or row.get("retrieved_at")
    provider = row.get("quote_provider") or "tradier"
    age = freshness_age_seconds({"retrieved_at": retrieved_at}) if retrieved_at else None
    conf = CONFIDENCE_SINGLE_SOURCE if price is not None else CONFIDENCE_NO_DATA

    warnings: list[str] = []
    if freshness_label(age) == "stale":
        warnings.append("Quote data may be stale.")

    fields = [
        _field("underlying_price", "Last Price", _fmt_price(price), source=provider,
               confidence=conf, retrieved_at=retrieved_at),
        _field("price_return_pct", "Price Return %", _fmt_float(row.get("price_return_pct"), 2),
               source="asa_computed", confidence=conf),
    ]
    return _panel("quote", "Quote Data", fields, conf, warnings)


def _momentum_panel(row: dict[str, Any]) -> dict[str, Any]:
    score = row.get("momentum_score") or row.get("score")
    rs = row.get("relative_strength") or row.get("rs_value")
    provider = row.get("data_provider") or "tradier"
    conf = CONFIDENCE_SINGLE_SOURCE if score is not None else CONFIDENCE_NO_DATA

    fields = [
        _field("momentum_score", "Momentum Score", _fmt_float(score, 1), source="asa_computed", confidence=conf),
        _field("relative_strength", "Relative Strength", _fmt_float(rs, 2), source="asa_computed", confidence=conf),
        _field("vol_trend", "Volume Trend", row.get("volume_trend") or "—", source=provider, confidence=conf),
    ]
    return _panel("momentum", "Momentum Data", fields, conf, [])


def _diagnostics_panel(diag: dict[str, Any]) -> dict[str, Any]:
    missing = list(diag.get("missing_required_fields") or [])
    stale = list(diag.get("stale_fields") or [])
    overall_conf = diag.get("overall_confidence") or CONFIDENCE_NO_DATA
    conf = CONFIDENCE_CONFIRMED if diag.get("data_complete") else CONFIDENCE_SINGLE_SOURCE

    warnings: list[str] = []
    if missing:
        warnings.append(f"Missing required fields: {', '.join(missing)}.")
    if stale:
        warnings.append(f"Stale fields: {', '.join(stale)}.")

    fields = [
        _field("data_complete", "Data Complete", "yes" if diag.get("data_complete") else "no",
               source="asa_diagnostics", confidence=conf),
        _field("overall_confidence", "Overall Confidence", overall_conf,
               source="asa_diagnostics", confidence=conf),
        _field("required_field_count", "Required Fields", str(diag.get("required_field_count") or 0),
               source="asa_diagnostics", confidence=CONFIDENCE_SINGLE_SOURCE),
        _field("present_field_count", "Present Fields", str(diag.get("present_field_count") or 0),
               source="asa_diagnostics", confidence=CONFIDENCE_SINGLE_SOURCE),
    ]
    return _panel("diagnostics", "Data Diagnostics", fields, conf, warnings)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _panel(
    section: str,
    label: str,
    fields: list[dict[str, Any]],
    confidence_summary: str,
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "section": section,
        "label": label,
        "fields": fields,
        "confidence_summary": confidence_summary,
        "warnings": warnings,
        "field_count": len(fields),
        "schema_version": _SECTION_SCHEMA_VERSION,
    }


def _field(
    name: str,
    label: str,
    value: Any,
    source: str = "unknown",
    confidence: str = CONFIDENCE_NO_DATA,
    retrieved_at: str | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    display = str(value) if value is not None else "—"
    return {
        "name": name,
        "label": label,
        "value": display,
        "source": source,
        "confidence": confidence,
        "retrieved_at": retrieved_at,
        "notes": notes or [],
    }


def _first_source(sources: list[str]) -> str:
    return sources[0] if sources else "unknown"


def _fmt_price(val: Any) -> str | None:
    try:
        return f"${float(val):.2f}" if val is not None else None
    except (TypeError, ValueError):
        return str(val) if val is not None else None


def _fmt_float(val: Any, decimals: int = 2) -> str | None:
    try:
        return f"{float(val):.{decimals}f}" if val is not None else None
    except (TypeError, ValueError):
        return str(val) if val is not None else None


def _fmt_int(val: Any) -> str | None:
    try:
        return f"{int(val):,}" if val is not None else None
    except (TypeError, ValueError):
        return str(val) if val is not None else None


def _fmt_bid_ask(bid: Any, ask: Any) -> str | None:
    if bid is None and ask is None:
        return None
    b = f"${float(bid):.2f}" if bid is not None else "—"
    a = f"${float(ask):.2f}" if ask is not None else "—"
    return f"{b} / {a}"
