"""Run finalization coordinator.

Patch 33B makes durable strategy state precede report snapshot/manifest
publication. This coordinator keeps that ordering in one place so a successful
market-analysis run cannot be advertised before row-store/history/journal
artifacts are at least attempted and measured.
"""

from __future__ import annotations

from typing import Any, Callable

from app import config


def persist_strategy_artifacts(
    *,
    run_context: Any,
    run_mode: str,
    normalized_strategy_results: dict[str, Any],
    tradier_snapshot: dict[str, Any],
    earnings_events: dict[str, dict[str, Any]],
    positions: list[dict[str, Any]],
    log_print: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    log = log_print or (lambda message: None)
    run_id = getattr(run_context, "run_id", None)
    run_date = str(getattr(run_context, "created_at", "") or "")[:10]
    status: dict[str, Any] = {
        "run_id": run_id,
        "required_failures": [],
        "optional_failures": [],
        "stages": [],
    }

    log(f"RUN_FINALIZATION stage=strategy_artifact_persistence_start run_id={run_id}")

    # Required semantic validation must happen before hot row-store persistence.
    try:
        from app.services.calendar_opportunity_projection_service import validate_calendar_canonical_rows
        calendar_rows = list(((normalized_strategy_results or {}).get("earnings_calendar") or {}).get("canonical_opportunities") or [])
        calendar_rows.extend(list(((normalized_strategy_results or {}).get("earnings_calendar") or {}).get("active_rows") or []))
        semantic_validation = validate_calendar_canonical_rows([row for row in calendar_rows if isinstance(row, dict)])
        tradier_snapshot["_calendar_semantic_validation"] = semantic_validation
        status["calendar_semantic_validation"] = semantic_validation
        status["stages"].append("calendar_semantic_validation")
        if semantic_validation.get("invariant_violations"):
            status["required_failures"].append({
                "stage": "calendar_semantic_validation",
                "error": "canonical calendar row invariant violation",
                "violation_count": semantic_validation.get("violation_count", 0),
                "violations": semantic_validation.get("invariant_violations", [])[:10],
            })
            log(
                "CALENDAR_SEMANTIC_VALIDATION "
                f"checked={semantic_validation.get('checked_rows', 0)} "
                f"violations={semantic_validation.get('violation_count', 0)} "
                f"codes={[v.get('code') for v in (semantic_validation.get('invariant_violations') or [])[:10]]}"
            )
        else:
            log(f"CALENDAR_SEMANTIC_VALIDATION checked={semantic_validation.get('checked_rows', 0)} violations=0")
    except Exception as exc:
        status["required_failures"].append({"stage": "calendar_semantic_validation", "error": str(exc)[:240]})
        log(f"CALENDAR_SEMANTIC_VALIDATION ERROR: {exc}")

    # 4. Persist Strategy Row Repository (required for hot APIs).
    try:
        if any(failure.get("stage") == "calendar_semantic_validation" for failure in status["required_failures"]):
            raise RuntimeError("calendar semantic validation failed before row-store persistence")
        from app.services.strategy_row_repository import StrategyRowRepository
        strategy_row_write = StrategyRowRepository().write_run(run_id, normalized_strategy_results)
        tradier_snapshot["_strategy_row_store"] = strategy_row_write
        status["strategy_row_store"] = strategy_row_write
        status["stages"].append("strategy_row_store")
        log(
            "StrategyRowRepository: wrote "
            f"{strategy_row_write.get('write_count', 0)} row(s) "
            f"{strategy_row_write.get('by_strategy', {})}"
        )
    except Exception as exc:
        status["required_failures"].append({"stage": "strategy_row_store", "error": str(exc)[:240]})
        log(f"StrategyRowRepository ERROR: {exc}")

    # 5. Persist opportunity history (required for lifecycle continuity when enabled).
    try:
        if getattr(config, "OPPORTUNITY_HISTORY_ENABLED", True):
            from app.db.strategy_opportunity_history import write_run as _write_opp_hist
            _hist_result = _write_opp_hist(
                run_id=run_id,
                strategy_results=normalized_strategy_results,
                run_date=run_date,
            )
            tradier_snapshot["_opportunity_history_write"] = _hist_result
            status["opportunity_history"] = _hist_result
            status["stages"].append("opportunity_history")
            log(
                f"OpportunityHistory: run_id={run_id} "
                f"rows_written={_hist_result.get('rows_written', 0)} "
                f"first_observations={_hist_result.get('first_observations', 0)} "
                f"updated_opportunities={_hist_result.get('rows_written', 0) - _hist_result.get('first_observations', 0)} "
                f"stage_transitions={_hist_result.get('stage_transitions', 0)} "
                f"verdict_transitions={_hist_result.get('verdict_transitions', 0)}"
            )
    except Exception as exc:
        status["required_failures"].append({"stage": "opportunity_history", "error": str(exc)[:240]})
        log(f"OpportunityHistory ERROR: {exc}")

    # 6. Persist observation journal (required scanner evidence, but non-blocking for older DBs).
    try:
        from app.db.strategy_observations import write_run as _write_obs_run
        from app.services.strategy_observation_journal_service import (
            build_observations_from_strategy_results as _build_obs,
        )
        _obs = _build_obs(normalized_strategy_results, run_id, run_date)
        _written = _write_obs_run(run_id, run_date, _obs)
        tradier_snapshot["_journal_write_status"] = {
            "status": "ok",
            "observations_written": _written,
            "total_built": len(_obs),
        }
        status["observation_journal"] = tradier_snapshot["_journal_write_status"]
        status["stages"].append("observation_journal")
        log(f"StrategyObservationJournal: wrote {_written} observation(s) for run {run_id}")
    except Exception as exc:
        status["optional_failures"].append({"stage": "observation_journal", "error": str(exc)[:240]})
        tradier_snapshot["_journal_write_status"] = {"status": "error", "error": str(exc)[:200]}
        log(f"StrategyObservationJournal: write failed (non-fatal): {exc}")

    # 7. Pipeline provenance.
    try:
        from app.providers.earnings_provider import configured_provider_names as _cpn
        from app.services.pipeline_provenance_service import wire_pipeline_provenance
        _prov_summary = wire_pipeline_provenance(
            run_id=run_id,
            strategy_id="earnings_calendar",
            tradier_snapshot=tradier_snapshot,
            earnings_events=earnings_events,
            positions=positions,
            configured_providers=_cpn(),
            log_print=log,
            db_enabled=getattr(config, "DATA_CONFIDENCE_ENABLED", True),
        )
        tradier_snapshot["_pipeline_provenance_summary"] = _prov_summary
        status["pipeline_provenance"] = _prov_summary
        status["stages"].append("pipeline_provenance")
    except Exception as exc:
        status["optional_failures"].append({"stage": "pipeline_provenance", "error": str(exc)[:240]})
        log(f"PipelineProvenance: write failed (non-fatal): {exc}")

    # 8. Data confidence validation.
    try:
        from app.db.data_confidence_run_reports import write_run_report as _write_run_report
        from app.services.automated_data_validation_service import run_data_confidence_validation
        _all_rows = []
        for _sid, _sresult in (normalized_strategy_results or {}).items():
            _all_rows.extend(list((_sresult or {}).get("rows") or (_sresult or {}).get("items") or []))
        if getattr(config, "DATA_CONFIDENCE_VALIDATION_LOG_ENABLED", True):
            _suite_result = run_data_confidence_validation(
                strategy_rows=_all_rows[:200],
                strategy_id="pipeline",
                earnings_events={t: (e.get("event") or e) for t, e in earnings_events.items()},
                log_print=log,
            )
            _write_run_report(run_id, "pipeline", _suite_result)
            tradier_snapshot["_data_confidence_validation"] = _suite_result
            status["data_confidence_validation"] = {
                "failed": _suite_result.get("true_failures") or 0,
                "warned": _suite_result.get("total_warnings") or 0,
            }
            status["stages"].append("data_confidence_validation")
            if int(_suite_result.get("true_failures") or 0) > 0:
                status["required_failures"].append({
                    "stage": "data_confidence_validation",
                    "error": "hard data-confidence validation failures",
                    "failed": int(_suite_result.get("true_failures") or 0),
                })
    except Exception as exc:
        status["optional_failures"].append({"stage": "data_confidence_validation", "error": str(exc)[:240]})
        log(f"DataConfidenceRunReport: write failed (non-fatal): {exc}")

    # Opportunistic cleanup after writes.
    try:
        if getattr(config, "OPPORTUNITY_HISTORY_ENABLED", True):
            from app.db.strategy_opportunity_history import cleanup_old_observations as _hist_cleanup
            _hist_cleanup()
    except Exception:
        pass
    try:
        from app.db.strategy_observations import cleanup_old_observations as _obs_cleanup
        _obs_cleanup(config.STRATEGY_OBSERVATION_RETENTION_DAYS)
    except Exception:
        pass

    status["status"] = "failed" if status["required_failures"] else ("warning" if status["optional_failures"] else "ok")
    tradier_snapshot["_run_finalization"] = status
    log(
        f"RUN_FINALIZATION stage=strategy_artifact_persistence_complete run_id={run_id} "
        f"status={status['status']} required_failures={len(status['required_failures'])} "
        f"optional_failures={len(status['optional_failures'])}"
    )
    return status
