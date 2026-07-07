"""Read-only scan-coverage summary built from latest cached run state."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app import config
from app.services.report_snapshot_service import ReportSnapshotRepository


# Configurable thresholds for "demo quality" classification.
_FF_EVALUATED_MIN = 8
_EARNINGS_CANDIDATES_MIN = 7  # > 6
_FF_DEV_CAP_MAX = 0           # 0 means no dev cap applied


def build_scan_coverage() -> dict[str, Any]:
    """Return scan-coverage summary; reads snapshot only, no provider calls."""
    checked_at = datetime.now(timezone.utc).isoformat()
    repo = ReportSnapshotRepository(log_print=lambda _: None)
    snapshot = repo.latest_success(include_full=True)
    if not snapshot:
        return {
            "status": "no_data",
            "checked_at": checked_at,
            "run_id": None,
            "generated_at": None,
            "app_mode": config.APP_MODE,
            "run_mode": None,
            "universe_discovery_enabled": True,
            "core_universe_source": "S&P 500 + Russell supplement",
            "ff_universe": 0,
            "ff_evaluated": 0,
            "ff_skipped_dev_cap": 0,
            "ff_skipped_provider_budget": 0,
            "ff_chain_sets": 0,
            "earnings_candidates_returned": 0,
            "earnings_candidates_checked": 0,
            "skew_universe_cap": int(getattr(config, "SKEW_UNIVERSE_MAX_CANDIDATES", 50) or 50),
            "skew_candidates_evaluated": 0,
            "is_demo_quality_scan": False,
            "demo_quality_blockers": ["No completed run available."],
            "coverage_mode_label": "Limited scan",
            "provider_calls_triggered": False,
        }

    summary = repo.load_summary(snapshot, full=True)
    report = (summary.get("report_data") or {}) if isinstance(summary, dict) else {}
    tradier = (report.get("tradier_snapshot") or {}) if isinstance(report, dict) else {}
    pipeline = (tradier.get("_pipeline_status") or {}) if isinstance(tradier, dict) else {}
    ff = (tradier.get("_forward_factor_strategy") or {}) if isinstance(tradier, dict) else {}
    ff_stage = (ff.get("stage_counts") or (ff.get("summary") or {}).get("stage_counts") or {}) if isinstance(ff, dict) else {}
    earnings_quality = (tradier.get("_earnings_discovery_quality") or {}) if isinstance(tradier, dict) else {}
    skew = (tradier.get("_skew_momentum_vertical_strategy") or (tradier.get("_strategy_results") or {}).get("skew_momentum_vertical") or {}) if isinstance(tradier, dict) else {}

    run_mode = str(snapshot.get("mode") or pipeline.get("run_mode") or "unknown").lower()
    app_mode = str(config.APP_MODE or "unknown").lower()
    ff_universe = int(ff_stage.get("universe", 0) or len(ff.get("scanned_tickers") or []) or 0)
    ff_evaluated = int(ff_stage.get("cheap_evaluated", 0) or 0)
    ff_skipped_dev_cap = int(ff_stage.get("skipped_dev_cap", 0) or 0)
    ff_skipped_provider_budget = int(ff_stage.get("skipped_provider_budget", 0) or 0)
    ff_chain_sets = int(ff_stage.get("chain_sets", 0) or 0)
    earnings_passed = int(earnings_quality.get("passed_count", 0) or len(earnings_quality.get("passed_items") or earnings_quality.get("items") or []) or 0)
    earnings_checked = int(earnings_quality.get("checked_count", 0) or len(earnings_quality.get("items") or []) or 0)
    skew_universe_cap = int(getattr(config, "SKEW_UNIVERSE_MAX_CANDIDATES", 50) or 50)
    skew_evaluated = int((skew.get("summary") or {}).get("universe_size", 0) or skew.get("universe_size") or 0)

    is_stale = _is_run_stale(snapshot.get("completed_at"))

    blockers = _demo_quality_blockers(
        app_mode=app_mode,
        ff_skipped_dev_cap=ff_skipped_dev_cap,
        ff_evaluated=ff_evaluated,
        earnings_passed=earnings_passed,
        is_stale=is_stale,
    )
    is_demo_quality = not blockers

    return {
        "status": "ok",
        "checked_at": checked_at,
        "run_id": snapshot.get("run_id"),
        "generated_at": snapshot.get("completed_at"),
        "app_mode": app_mode,
        "run_mode": run_mode,
        "universe_discovery_enabled": True,
        "core_universe_source": "S&P 500 + Russell supplement",
        "ff_universe": ff_universe,
        "ff_evaluated": ff_evaluated,
        "ff_skipped_dev_cap": ff_skipped_dev_cap,
        "ff_skipped_provider_budget": ff_skipped_provider_budget,
        "ff_chain_sets": ff_chain_sets,
        "earnings_candidates_returned": earnings_passed,
        "earnings_candidates_checked": earnings_checked,
        "skew_universe_cap": skew_universe_cap,
        "skew_candidates_evaluated": skew_evaluated,
        "is_demo_quality_scan": is_demo_quality,
        "demo_quality_blockers": blockers,
        "coverage_mode_label": "Full production scan" if is_demo_quality else "Limited scan",
        "provider_calls_triggered": False,
    }


def _demo_quality_blockers(
    *,
    app_mode: str,
    ff_skipped_dev_cap: int,
    ff_evaluated: int,
    earnings_passed: int,
    is_stale: bool,
) -> list[str]:
    blockers: list[str] = []
    if app_mode not in ("prod", "production"):
        blockers.append(f"App mode is '{app_mode}', not 'prod'.")
    if ff_skipped_dev_cap > _FF_DEV_CAP_MAX:
        blockers.append(f"FF dev cap applied — {ff_skipped_dev_cap} symbols skipped by dev cap.")
    if ff_evaluated < _FF_EVALUATED_MIN:
        blockers.append(f"FF evaluated only {ff_evaluated} symbols (minimum {_FF_EVALUATED_MIN} for full scan).")
    if earnings_passed < _EARNINGS_CANDIDATES_MIN:
        blockers.append(f"Earnings candidates returned only {earnings_passed} (minimum {_EARNINGS_CANDIDATES_MIN} for full scan).")
    if is_stale:
        blockers.append("Latest scan data is stale (older than 23 hours).")
    return blockers


def _is_run_stale(completed_at: Any) -> bool:
    if not completed_at:
        return True
    try:
        dt = datetime.fromisoformat(str(completed_at).replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - dt).total_seconds()
        return age > 82800  # 23 hours
    except Exception:
        return True
