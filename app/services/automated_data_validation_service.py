"""
Sprint 28 — Epic G: Automated Data Validation Service

Provides three categories of validation:
1. Regression validation — checks that normalized values match expected contracts.
2. Provider validation — confirms provider responses meet schema expectations.
3. Cross-provider validation — detects when providers return inconsistent data.

All validations are read-only and stateless. They do NOT call providers; they
evaluate data that has already been fetched and normalized.

ValidationResult
----------------
Each check returns a ValidationResult with:
- passed: bool
- level: "error" | "warning" | "info"
- rule_id: short slug for filtering
- message: human-readable explanation
- field: the specific field that failed (when applicable)
- actual: the actual value seen
- expected: what was expected
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.models.data_provenance import PROVENANCE_SCHEMA_VERSION

LEVEL_ERROR = "error"
LEVEL_WARNING = "warning"
LEVEL_INFO = "info"


@dataclass(slots=True)
class ValidationResult:
    rule_id: str
    passed: bool
    level: str = LEVEL_INFO
    message: str = ""
    field: str | None = None
    actual: Any = None
    expected: Any = None
    schema_version: str = PROVENANCE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationReport:
    category: str
    subject: str  # e.g. ticker, strategy_id, provider name
    results: list[ValidationResult] = field(default_factory=list)
    schema_version: str = PROVENANCE_SCHEMA_VERSION

    def add(self, result: ValidationResult) -> "ValidationReport":
        self.results.append(result)
        return self

    @property
    def passed(self) -> bool:
        return all(r.passed or r.level != LEVEL_ERROR for r in self.results)

    @property
    def error_count(self) -> int:
        return sum(1 for r in self.results if not r.passed and r.level == LEVEL_ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for r in self.results if not r.passed and r.level == LEVEL_WARNING)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "subject": self.subject,
            "passed": self.passed,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "total_checks": len(self.results),
            "results": [r.to_dict() for r in self.results],
            "schema_version": self.schema_version,
        }


# ─── Provider response validation ─────────────────────────────────────────────

def validate_earnings_event(event: dict[str, Any], provider: str = "unknown") -> ValidationReport:
    """Validate a normalized earnings event against the expected schema."""
    report = ValidationReport(category="provider", subject=f"{provider}:earnings")
    e = event or {}

    _check(report, "earnings.date_present",
           bool(e.get("earnings_date") or e.get("date")),
           LEVEL_ERROR, "Earnings date is missing.", field="earnings_date",
           actual=e.get("earnings_date"))

    date_str = str(e.get("earnings_date") or e.get("date") or "")
    _check(report, "earnings.date_format",
           len(date_str) == 10 and date_str.count("-") == 2,
           LEVEL_ERROR, f"Earnings date {date_str!r} is not YYYY-MM-DD.", field="earnings_date",
           actual=date_str, expected="YYYY-MM-DD")

    sources = list(e.get("sources_seen") or e.get("date_sources") or [])
    _check(report, "earnings.source_present",
           bool(sources), LEVEL_WARNING,
           "No sources_seen recorded — provenance is unavailable.", field="sources_seen",
           actual=sources)

    _check(report, "earnings.confidence_present",
           bool(e.get("earnings_date_confidence") or e.get("date_confidence")),
           LEVEL_WARNING, "earnings_date_confidence is missing.", field="earnings_date_confidence",
           actual=e.get("earnings_date_confidence"))

    conflict = bool(e.get("earnings_source_conflict") or e.get("date_conflict"))
    if conflict:
        _check(report, "earnings.conflict_details_present",
               bool(e.get("earnings_conflict_details")),
               LEVEL_WARNING, "Conflict detected but no conflict_details provided.",
               field="earnings_conflict_details", actual=None)

    return report


def validate_options_leg(leg: dict[str, Any], provider: str = "unknown") -> ValidationReport:
    """Validate a single normalized options leg."""
    report = ValidationReport(category="provider", subject=f"{provider}:option_leg")
    l = leg or {}

    bid = _f(l.get("bid"))
    ask = _f(l.get("ask"))
    iv = _f(l.get("iv") or l.get("implied_volatility"))

    _check(report, "leg.bid_present", bid is not None, LEVEL_WARNING,
           "Bid price missing.", field="bid", actual=bid)
    _check(report, "leg.ask_present", ask is not None, LEVEL_WARNING,
           "Ask price missing.", field="ask", actual=ask)

    if bid is not None and ask is not None:
        _check(report, "leg.bid_lte_ask", bid <= ask + 0.001, LEVEL_ERROR,
               f"Bid ({bid}) > Ask ({ask}) — data inversion.", field="bid",
               actual=bid, expected=f"<= {ask}")

    if iv is not None:
        _check(report, "leg.iv_range", 0.001 <= iv <= 20.0, LEVEL_WARNING,
               f"IV {iv:.4f} is outside plausible range [0.001, 20.0].", field="iv",
               actual=iv, expected="0.001–20.0")

    _check(report, "leg.expiration_present",
           bool(l.get("expiration_date") or l.get("expiration")),
           LEVEL_ERROR, "Expiration date missing.", field="expiration_date")

    oi = _f(l.get("open_interest"))
    if oi is not None:
        _check(report, "leg.open_interest_nonneg", oi >= 0, LEVEL_WARNING,
               f"Negative open interest ({oi}).", field="open_interest", actual=oi)

    return report


def validate_quote(quote: dict[str, Any], ticker: str, provider: str = "unknown") -> ValidationReport:
    """Validate a normalized quote record."""
    report = ValidationReport(category="provider", subject=f"{provider}:{ticker}:quote")
    q = quote or {}

    last = _f(q.get("last") or q.get("last_price") or q.get("price"))
    _check(report, "quote.last_price_present", last is not None, LEVEL_ERROR,
           f"Last price missing for {ticker}.", field="last", actual=last)

    if last is not None:
        _check(report, "quote.last_price_positive", last > 0, LEVEL_ERROR,
               f"Last price {last} is not positive.", field="last", actual=last, expected="> 0")

    _check(report, "quote.retrieved_at_present",
           bool(q.get("retrieved_at") or q.get("fetched_at")),
           LEVEL_WARNING, "retrieved_at missing from quote.", field="retrieved_at")

    return report


# ─── Cross-provider validation ─────────────────────────────────────────────────

def cross_validate_earnings_dates(
    events_by_provider: dict[str, dict[str, Any]],
    ticker: str,
    tolerance_days: int = 0,
) -> ValidationReport:
    """Detect earnings date conflicts across providers for a single ticker."""
    report = ValidationReport(category="cross_provider", subject=f"earnings:{ticker}")
    if len(events_by_provider) < 2:
        _check(report, "cross.multi_source", False, LEVEL_INFO,
               "Only one provider — cross-provider validation skipped.", actual=len(events_by_provider))
        return report

    dates: dict[str, str | None] = {}
    for prov, ev in events_by_provider.items():
        dates[prov] = str(ev.get("earnings_date") or ev.get("date") or "")[:10] or None

    unique_dates = set(d for d in dates.values() if d)
    if len(unique_dates) <= 1:
        _check(report, "cross.date_agreement", True, LEVEL_INFO,
               f"All providers agree on earnings date for {ticker}.")
    else:
        _check(report, "cross.date_agreement", False, LEVEL_ERROR,
               f"Provider date conflict for {ticker}: " +
               ", ".join(f"{p}={d}" for p, d in sorted(dates.items())),
               field="earnings_date", actual=dates)

    return report


def cross_validate_iv(
    iv_by_provider: dict[str, float | None],
    ticker: str,
    expiration: str,
    tolerance_pct: float = 0.10,
) -> ValidationReport:
    """Detect IV disagreements across providers for a specific expiration."""
    report = ValidationReport(category="cross_provider", subject=f"iv:{ticker}:{expiration}")
    valid = {p: v for p, v in (iv_by_provider or {}).items() if v is not None}
    if len(valid) < 2:
        return report

    vals = list(valid.values())
    baseline = vals[0]
    for p, v in list(valid.items())[1:]:
        diff_pct = abs(v - baseline) / baseline if baseline else 0
        _check(report, f"cross.iv_agreement.{p}", diff_pct <= tolerance_pct, LEVEL_WARNING,
               f"IV from {p} ({v:.4f}) differs from baseline ({baseline:.4f}) by {diff_pct:.1%}",
               field="iv", actual=v, expected=f"within {tolerance_pct:.0%} of {baseline:.4f}")

    return report


# ─── Regression validation ─────────────────────────────────────────────────────

def validate_strategy_row_schema(row: dict[str, Any], strategy_id: str) -> ValidationReport:
    """Check that a strategy output row meets the minimum schema contract."""
    report = ValidationReport(category="regression", subject=f"{strategy_id}:{row.get('ticker', 'UNKNOWN')}")
    r = row or {}

    for fld in ("ticker", "action", "score"):
        _check(report, f"row.{fld}_present", bool(r.get(fld) is not None), LEVEL_ERROR,
               f"Required field {fld!r} missing from strategy row.", field=fld)

    score = _f(r.get("score"))
    if score is not None:
        _check(report, "row.score_range", 0 <= score <= 100, LEVEL_WARNING,
               f"Score {score} outside expected range [0, 100].", field="score", actual=score)

    if r.get("daily_opportunity"):
        do = r["daily_opportunity"]
        _check(report, "row.do_eligible_bool",
               isinstance(do.get("eligible"), bool),
               LEVEL_ERROR, "daily_opportunity.eligible must be bool.", field="daily_opportunity.eligible",
               actual=type(do.get("eligible")).__name__)

    return report


# ─── Patch 32A: DATA_CONFIDENCE_VALIDATION log ────────────────────────────────

def log_data_confidence_validation(
    suite_result: dict[str, Any],
    log_print: Any = None,
) -> str:
    """Emit a DATA_CONFIDENCE_VALIDATION log line from a run_validation_suite result.

    Format:
      DATA_CONFIDENCE_VALIDATION passed=N warned=N failed=N sample_size=N failure_codes=[...]

    Returns the formatted log line. Safe on any error.
    """
    try:
        log = log_print or print
        total = int(suite_result.get("total_reports") or 0)
        passed = int(suite_result.get("passed_reports") or 0)
        failed = int(suite_result.get("failed_reports") or 0)
        errors = int(suite_result.get("total_errors") or 0)
        warnings = int(suite_result.get("total_warnings") or 0)

        failure_codes: list[str] = []
        for report in (suite_result.get("reports") or [])[:50]:
            for result in (report.get("results") or []):
                if not result.get("passed"):
                    code = str(result.get("rule_id") or "unknown")
                    if code not in failure_codes:
                        failure_codes.append(code)

        line = (
            f"DATA_CONFIDENCE_VALIDATION "
            f"passed={passed} "
            f"warned={warnings} "
            f"failed={failed} "
            f"sample_size={total} "
            f"failure_codes={failure_codes!r}"
        )
        try:
            log(line, flush=True)
        except TypeError:
            log(line)
        return line
    except Exception:
        return "DATA_CONFIDENCE_VALIDATION error=log_failed"


def run_data_confidence_validation(
    strategy_rows: list[dict[str, Any]],
    strategy_id: str,
    earnings_events: dict[str, dict[str, Any]] | None = None,
    log_print: Any = None,
) -> dict[str, Any]:
    """Run the validation suite and emit the DATA_CONFIDENCE_VALIDATION log line.

    Combines run_validation_suite + log_data_confidence_validation for convenience.
    """
    result = run_validation_suite(strategy_rows, strategy_id, earnings_events)
    log_data_confidence_validation(result, log_print=log_print)
    return result


# ─── Validation suite runner ───────────────────────────────────────────────────

def run_validation_suite(
    strategy_rows: list[dict[str, Any]],
    strategy_id: str,
    earnings_events: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run the full validation suite on a batch of strategy rows.

    Returns a summary dict with pass/fail counts and all validation reports.
    """
    reports: list[ValidationReport] = []

    for row in strategy_rows:
        reports.append(validate_strategy_row_schema(row, strategy_id))

    for ticker, event in (earnings_events or {}).items():
        reports.append(validate_earnings_event(event))

    total = len(reports)
    passed = sum(1 for r in reports if r.passed)
    errors = sum(r.error_count for r in reports)
    warnings = sum(r.warning_count for r in reports)

    return {
        "strategy_id": strategy_id,
        "validation_passed": errors == 0,
        "total_reports": total,
        "passed_reports": passed,
        "failed_reports": total - passed,
        "total_errors": errors,
        "total_warnings": warnings,
        "reports": [r.to_dict() for r in reports[:50]],
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "provider_calls_triggered": False,
        "read_only": True,
    }


def _check(
    report: ValidationReport,
    rule_id: str,
    condition: bool,
    level: str = LEVEL_INFO,
    message: str = "",
    field: str | None = None,
    actual: Any = None,
    expected: Any = None,
) -> None:
    report.add(ValidationResult(
        rule_id=rule_id,
        passed=bool(condition),
        level=level if not condition else LEVEL_INFO,
        message=message if not condition else f"{rule_id} passed.",
        field=field,
        actual=actual,
        expected=expected,
    ))


def _f(val: Any) -> float | None:
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None
