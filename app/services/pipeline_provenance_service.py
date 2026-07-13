"""
ASA Patch 32B — Pipeline Provenance Wiring Service

Creates FieldProvenanceRecord instances for the 35+ fields listed in the
Patch 32B spec and batch-writes them to the data_provenance SQLite table.

This service is called once per run, after all provider data has been
collected, and is entirely read-only from the perspective of providers:
it builds provenance from already-fetched data structures.

All functions swallow errors — provenance is observability, not a
correctness requirement for the pipeline.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from app.models.patch32a_provenance import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_UNKNOWN,
    SOURCE_TYPE_CALCULATED,
    SOURCE_TYPE_MISSING,
    SOURCE_TYPE_PROVIDER,
    STATUS_AVAILABLE,
    STATUS_MISSING,
    STATUS_UNSUPPORTED,
    FieldProvenanceRecord,
    ProviderValueRecord,
)

_SCHEMA_VERSION = "32B.v1"
_PRIMARY_OPTIONS_PROVIDER = "tradier"
_PRIMARY_MARKET_PROVIDER = "tradier"

# Fields that Alpha Vantage's earnings calendar cannot supply
_AV_UNSUPPORTED_FIELDS = frozenset({"earnings.session", "earnings.time"})


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _f(val: Any) -> float | None:
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


# ─── Market data provenance ────────────────────────────────────────────────────

def build_market_provenance(
    ticker: str,
    quote: dict[str, Any],
    source: str = _PRIMARY_MARKET_PROVIDER,
    observed_at: str | None = None,
) -> dict[str, "FieldProvenanceRecord"]:
    """Create provenance records for market quote fields.

    Returns dict keyed by field_id: market.last_price, .bid, .ask, .mid, .quote_timestamp
    """
    ts = observed_at or _utcnow()
    q = quote or {}
    results: dict[str, FieldProvenanceRecord] = {}

    for field_id, keys in [
        ("market.last_price", ["last", "last_price", "close", "mark"]),
        ("market.bid", ["bid"]),
        ("market.ask", ["ask"]),
        ("market.quote_timestamp", ["quote_date", "trade_date", "timestamp"]),
    ]:
        value = next((q.get(k) for k in keys if q.get(k) is not None), None)
        if value is not None:
            pv = ProviderValueRecord.available(source, value, observed_at=ts, is_selected=True)
            results[field_id] = FieldProvenanceRecord(
                field_id=field_id,
                selected_value=value,
                selected_provider=source,
                selected_source_type=SOURCE_TYPE_PROVIDER,
                selected_at=ts,
                observed_at=ts,
                freshness_timestamp=ts,
                confidence_level=CONFIDENCE_LOW,
                confidence_reason="single_provider",
                selection_reason=f"only_available_provider={source}",
                provider_values=[pv],
                schema_version=_SCHEMA_VERSION,
            )
        else:
            pv = ProviderValueRecord.missing(source, observed_at=ts)
            results[field_id] = FieldProvenanceRecord(
                field_id=field_id,
                selected_value=None,
                selected_provider=source,
                selected_source_type=SOURCE_TYPE_MISSING,
                observed_at=ts,
                confidence_level=CONFIDENCE_UNKNOWN,
                confidence_reason="no_provider_returned_value",
                provider_values=[pv],
                schema_version=_SCHEMA_VERSION,
            )

    # Derived mid-price
    bid = _f(q.get("bid"))
    ask = _f(q.get("ask"))
    if bid is not None and ask is not None:
        mid = round((bid + ask) / 2, 4)
        results["market.mid"] = FieldProvenanceRecord(
            field_id="market.mid",
            selected_value=mid,
            selected_provider="calculated",
            selected_source_type=SOURCE_TYPE_CALCULATED,
            selected_at=ts,
            observed_at=ts,
            confidence_level=CONFIDENCE_LOW,
            confidence_reason="calculated_from_single_provider_bid_ask",
            is_calculated=True,
            calculation_method="(bid + ask) / 2",
            provider_values=[
                ProviderValueRecord.available(source, {"bid": bid, "ask": ask}, observed_at=ts, is_selected=True),
            ],
            schema_version=_SCHEMA_VERSION,
        )
    else:
        results["market.mid"] = FieldProvenanceRecord(
            field_id="market.mid",
            selected_source_type=SOURCE_TYPE_MISSING,
            confidence_level=CONFIDENCE_UNKNOWN,
            confidence_reason="bid_or_ask_missing",
            schema_version=_SCHEMA_VERSION,
        )

    return results


# ─── Options provenance ────────────────────────────────────────────────────────

def build_options_leg_provenance(
    ticker: str,
    expiration: str,
    option_type: str,
    strike: float | None,
    leg_data: dict[str, Any],
    source: str = _PRIMARY_OPTIONS_PROVIDER,
    observed_at: str | None = None,
) -> dict[str, "FieldProvenanceRecord"]:
    """Create provenance records for options leg fields.

    Returns dict keyed by field_id: options.bid, .ask, .mid, .last, .volume,
    .open_interest, .iv, .delta, .gamma, .theta, .vega, .rho, .quote_timestamp
    """
    ts = observed_at or _utcnow()
    d = leg_data or {}
    results: dict[str, FieldProvenanceRecord] = {}

    # Direct provider fields
    scalar_fields: list[tuple[str, list[str]]] = [
        ("options.bid", ["bid"]),
        ("options.ask", ["ask"]),
        ("options.last", ["last"]),
        ("options.volume", ["volume"]),
        ("options.open_interest", ["open_interest", "open_int"]),
        ("options.quote_timestamp", ["trade_date", "quote_date", "timestamp"]),
    ]
    for field_id, keys in scalar_fields:
        value = next((d.get(k) for k in keys if d.get(k) is not None), None)
        pv = ProviderValueRecord.available(source, value, observed_at=ts, is_selected=True) if value is not None else ProviderValueRecord.missing(source, observed_at=ts)
        results[field_id] = FieldProvenanceRecord(
            field_id=field_id,
            selected_value=value,
            selected_provider=source if value is not None else "none",
            selected_source_type=SOURCE_TYPE_PROVIDER if value is not None else SOURCE_TYPE_MISSING,
            selected_at=ts if value is not None else None,
            observed_at=ts,
            freshness_timestamp=ts if value is not None else None,
            confidence_level=CONFIDENCE_LOW if value is not None else CONFIDENCE_UNKNOWN,
            confidence_reason="single_provider" if value is not None else "no_provider_returned_value",
            selection_reason=f"only_available_provider={source}" if value is not None else "",
            provider_values=[pv],
            schema_version=_SCHEMA_VERSION,
        )

    # Derived mid-price
    bid = _f(d.get("bid"))
    ask = _f(d.get("ask"))
    if bid is not None and ask is not None:
        mid = round((bid + ask) / 2, 4)
        results["options.mid"] = FieldProvenanceRecord(
            field_id="options.mid",
            selected_value=mid,
            selected_provider="calculated",
            selected_source_type=SOURCE_TYPE_CALCULATED,
            selected_at=ts,
            observed_at=ts,
            confidence_level=CONFIDENCE_LOW,
            confidence_reason="calculated_from_single_provider",
            is_calculated=True,
            calculation_method="(bid + ask) / 2",
            provider_values=[ProviderValueRecord.available(source, {"bid": bid, "ask": ask}, observed_at=ts, is_selected=True)],
            schema_version=_SCHEMA_VERSION,
        )
    else:
        results["options.mid"] = FieldProvenanceRecord(
            field_id="options.mid",
            selected_source_type=SOURCE_TYPE_MISSING,
            confidence_level=CONFIDENCE_UNKNOWN,
            confidence_reason="bid_or_ask_missing",
            schema_version=_SCHEMA_VERSION,
        )

    # Greeks and IV — sourced from Tradier chain data; marked CALCULATED when derived
    greek_fields: list[tuple[str, str, str]] = [
        ("options.iv", "implied_volatility", "iv"),
        ("options.delta", "delta", "delta"),
        ("options.gamma", "gamma", "gamma"),
        ("options.theta", "theta", "theta"),
        ("options.vega", "vega", "vega"),
        ("options.rho", "rho", "rho"),
    ]
    for field_id, key1, key2 in greek_fields:
        value = d.get(key1) if d.get(key1) is not None else d.get(key2)
        if value is not None:
            fval = _f(value)
            pv = ProviderValueRecord.available(source, fval, observed_at=ts, is_selected=True)
            results[field_id] = FieldProvenanceRecord(
                field_id=field_id,
                selected_value=fval,
                selected_provider=source,
                selected_source_type=SOURCE_TYPE_CALCULATED,
                selected_at=ts,
                observed_at=ts,
                freshness_timestamp=ts,
                confidence_level=CONFIDENCE_LOW,
                confidence_reason="single_provider_model_value",
                selection_reason=f"tradier_model={source}",
                is_calculated=True,
                calculation_method="EXACT_MODEL",
                provider_values=[pv],
                schema_version=_SCHEMA_VERSION,
            )
        else:
            results[field_id] = FieldProvenanceRecord(
                field_id=field_id,
                selected_source_type=SOURCE_TYPE_MISSING,
                confidence_level=CONFIDENCE_UNKNOWN,
                confidence_reason="no_provider_returned_value",
                is_calculated=True,
                calculation_method="EXACT_MODEL",
                schema_version=_SCHEMA_VERSION,
            )

    return results


# ─── Earnings provenance (pipeline wrapper) ────────────────────────────────────

def build_earnings_pipeline_provenance(
    ticker: str,
    earnings_event: dict[str, Any],
    configured_providers: list[str],
    observed_at: str | None = None,
) -> dict[str, "FieldProvenanceRecord"]:
    """Build provenance for earnings fields from the merged pipeline event.

    Uses the already-merged event (sources_seen, is_timestamp_confirmed, etc.)
    rather than re-calling the selection service with raw per-provider results,
    since the pipeline only retains the merged event at this point.
    """
    try:
        from app.services.earnings_selection_service import build_earnings_field_provenance

        provider_results: dict[str, dict[str, Any]] = {}
        ev = earnings_event or {}
        sources = list(ev.get("sources_seen") or ev.get("date_sources") or [])

        for src in sources:
            provider_results[src] = {
                "earnings_date": ev.get("earnings_date") or ev.get("date"),
                "session": ev.get("time_of_day") or ev.get("session_label"),
                "is_timestamp_confirmed": ev.get("is_timestamp_confirmed", False),
                "source": src,
            }

        # Mark AV as UNSUPPORTED for session (by design)
        for av_key in ("alphavantage", "alpha_vantage", "av"):
            if av_key in provider_results:
                prov = dict(provider_results[av_key])
                prov["session"] = None
                prov["is_timestamp_confirmed"] = False
                provider_results[av_key] = prov

        # Providers not in sources_seen: mark NOT_REQUESTED or MISSING
        ts = observed_at or _utcnow()
        result = build_earnings_field_provenance(ev, provider_results, ts)
        return result
    except Exception:
        ts = observed_at or _utcnow()
        return {
            "earnings.date": FieldProvenanceRecord(
                field_id="earnings.date",
                selected_source_type=SOURCE_TYPE_MISSING,
                confidence_level=CONFIDENCE_UNKNOWN,
                confidence_reason="provenance_build_failed",
                schema_version=_SCHEMA_VERSION,
            )
        }


# ─── Position provenance ───────────────────────────────────────────────────────

def build_position_provenance(
    ticker: str,
    position: dict[str, Any],
    observed_at: str | None = None,
) -> dict[str, "FieldProvenanceRecord"]:
    """Create provenance for position fields sourced from Robinhood."""
    ts = observed_at or _utcnow()
    pos = position or {}
    results: dict[str, FieldProvenanceRecord] = {}

    position_fields: list[tuple[str, list[str]]] = [
        ("position.current_price", ["current_price", "last_price", "price"]),
        ("position.average_buy_price", ["average_buy_price", "avg_buy_price"]),
        ("position.quantity", ["quantity", "shares"]),
        ("position.market_value", ["market_value", "equity", "current_value"]),
        ("position.unrealized_pnl", ["unrealized_pnl", "equity_change_amount"]),
    ]

    for field_id, keys in position_fields:
        value = next((pos.get(k) for k in keys if pos.get(k) is not None), None)
        source = "robinhood"
        if value is not None:
            pv = ProviderValueRecord.available(source, value, observed_at=ts, is_selected=True)
            results[field_id] = FieldProvenanceRecord(
                field_id=field_id,
                selected_value=value,
                selected_provider=source,
                selected_source_type=SOURCE_TYPE_PROVIDER,
                selected_at=ts,
                observed_at=ts,
                freshness_timestamp=ts,
                confidence_level=CONFIDENCE_LOW,
                confidence_reason="single_provider",
                selection_reason="preferred_broker_source",
                provider_values=[pv],
                schema_version=_SCHEMA_VERSION,
            )
        else:
            results[field_id] = FieldProvenanceRecord(
                field_id=field_id,
                selected_source_type=SOURCE_TYPE_MISSING,
                confidence_level=CONFIDENCE_UNKNOWN,
                confidence_reason="not_available_from_robinhood",
                provider_values=[ProviderValueRecord.missing(source, observed_at=ts)],
                schema_version=_SCHEMA_VERSION,
            )

    return results


# ─── Pipeline wiring ───────────────────────────────────────────────────────────

def wire_pipeline_provenance(
    run_id: str,
    strategy_id: str,
    tradier_snapshot: dict[str, Any],
    earnings_events: dict[str, dict[str, Any]],
    positions: list[dict[str, Any]],
    configured_providers: list[str],
    log_print: Callable[[str], None] | None = None,
    db_enabled: bool = True,
) -> dict[str, Any]:
    """Wire provenance for all major field categories into the pipeline.

    Reads already-fetched data from tradier_snapshot and earnings_events.
    Batch-writes to data_provenance DB. Returns a summary dict.

    This function is non-blocking and error-safe: any failure is logged but
    never interrupts the pipeline.
    """
    log = log_print or (lambda msg: print(msg, flush=True))
    total_written = 0
    total_errors = 0
    observed_at = datetime.now(timezone.utc).isoformat()

    batch_records: list[dict[str, Any]] = []

    try:
        # --- Market + options provenance from tradier_snapshot ---
        for ticker, snap in (tradier_snapshot or {}).items():
            if str(ticker).startswith("_") or not isinstance(snap, dict):
                continue

            quote = snap.get("quote") or {}
            if quote:
                market_prov = build_market_provenance(ticker, quote, observed_at=observed_at)
                for field_id, rec in market_prov.items():
                    batch_records.append({
                        "run_id": run_id,
                        "strategy_id": strategy_id,
                        "row_id": f"{ticker}:market",
                        "ticker": ticker,
                        "field_id": field_id,
                        "selected_value": str(rec.selected_value) if rec.selected_value is not None else None,
                        "selected_provider": rec.selected_provider,
                        "confidence_level": rec.confidence_level,
                        "provenance": rec,
                    })

            # Options chain legs
            chains = snap.get("chains") or snap.get("option_chains") or []
            if isinstance(chains, dict):
                chains = list(chains.values())
            for exp_data in (chains or []):
                if not isinstance(exp_data, dict):
                    continue
                expiration = str(exp_data.get("expiration_date") or exp_data.get("expiration") or "")
                for option_type in ("calls", "puts"):
                    for leg in (exp_data.get(option_type) or []):
                        if not isinstance(leg, dict):
                            continue
                        strike = _f(leg.get("strike") or leg.get("strike_price"))
                        row_id = f"{ticker}:{expiration}:{option_type[0].upper()}:{strike}"
                        leg_prov = build_options_leg_provenance(
                            ticker, expiration, option_type.rstrip("s"),
                            strike, leg, observed_at=observed_at,
                        )
                        for field_id, rec in leg_prov.items():
                            batch_records.append({
                                "run_id": run_id,
                                "strategy_id": strategy_id,
                                "row_id": row_id,
                                "ticker": ticker,
                                "field_id": field_id,
                                "selected_value": str(rec.selected_value) if rec.selected_value is not None else None,
                                "selected_provider": rec.selected_provider,
                                "confidence_level": rec.confidence_level,
                                "provenance": rec,
                            })

        # --- Earnings provenance ---
        for ticker, ev_wrapper in (earnings_events or {}).items():
            ev = ev_wrapper if isinstance(ev_wrapper, dict) else {}
            if not ev.get("has_data"):
                # Try event field directly (some callers pass the event directly)
                if not ev.get("earnings_date") and not ev.get("date"):
                    continue
            actual_ev = ev.get("event") or ev if ev.get("earnings_date") or ev.get("date") else {}
            if not actual_ev:
                continue
            earnings_prov = build_earnings_pipeline_provenance(
                ticker, actual_ev, configured_providers, observed_at=observed_at,
            )
            for field_id, rec in earnings_prov.items():
                batch_records.append({
                    "run_id": run_id,
                    "strategy_id": strategy_id,
                    "row_id": f"{ticker}:earnings",
                    "ticker": ticker,
                    "field_id": field_id,
                    "selected_value": str(rec.selected_value) if rec.selected_value is not None else None,
                    "selected_provider": rec.selected_provider,
                    "confidence_level": rec.confidence_level,
                    "provenance": rec,
                })

        # --- Position provenance ---
        for pos in (positions or []):
            ticker = str((pos or {}).get("ticker") or "").upper().strip()
            if not ticker:
                continue
            pos_prov = build_position_provenance(ticker, pos, observed_at=observed_at)
            for field_id, rec in pos_prov.items():
                batch_records.append({
                    "run_id": run_id,
                    "strategy_id": strategy_id,
                    "row_id": f"{ticker}:position",
                    "ticker": ticker,
                    "field_id": field_id,
                    "selected_value": str(rec.selected_value) if rec.selected_value is not None else None,
                    "selected_provider": rec.selected_provider,
                    "confidence_level": rec.confidence_level,
                    "provenance": rec,
                })

        log(f"PipelineProvenance: built {len(batch_records)} provenance record(s) for run {run_id}")

        if db_enabled and batch_records:
            try:
                from app.db.data_provenance import write_provenance_batch_list
                import json as _json

                db_rows = []
                for rec in batch_records:
                    prov_obj = rec.get("provenance")
                    prov_json = ""
                    if prov_obj is not None:
                        try:
                            prov_json = _json.dumps(prov_obj.to_dict(), default=str)
                        except Exception:
                            prov_json = ""
                    db_rows.append({
                        "run_id": rec["run_id"],
                        "strategy_id": rec["strategy_id"],
                        "row_id": rec["row_id"],
                        "ticker": rec.get("ticker"),
                        "field_id": rec["field_id"],
                        "selected_value": rec.get("selected_value"),
                        "selected_provider": rec.get("selected_provider"),
                        "confidence_level": rec.get("confidence_level"),
                        "provenance_json": prov_json,
                    })
                total_written = write_provenance_batch_list(db_rows)
                log(f"PipelineProvenance: persisted {total_written} record(s) to data_provenance DB")
            except Exception as exc:
                log(f"PipelineProvenance: DB write failed (non-fatal): {exc}")
                total_errors += 1

    except Exception as exc:
        log(f"PipelineProvenance: unexpected error (non-fatal): {exc}")
        total_errors += 1

    return {
        "records_built": len(batch_records),
        "records_written": total_written,
        "errors": total_errors,
        "schema_version": _SCHEMA_VERSION,
    }
