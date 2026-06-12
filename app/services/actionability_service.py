"""Keep diagnostic signal quality separate from final trade actionability."""

from __future__ import annotations

from typing import Any


def attach_actionability(row: dict[str, Any]) -> dict[str, Any]:
    output = dict(row)
    verdict = str(output.get("final_verdict") or output.get("verdict") or output.get("action") or "").upper()
    signal = _score(output.get("signal_score"), output.get("setup_quality_score"), output.get("score"), output.get("ranking_score"))
    if any(token in verdict for token in ("FAIL", "BLOCKED", "AVOID", "DO NOT ADD", "DATA UNAVAILABLE")):
        actionability = 0.0
        reason = "hard fail"
    elif "WATCH" in verdict or "RESEARCH" in verdict or "SKIPPED" in verdict:
        actionability = min(signal, 45.0)
        reason = "watch or research only"
    else:
        actionability = _score(output.get("actionability_score"), output.get("priority"), signal)
        reason = "actionable" if actionability > 0 else "not actionable"
    output["signal_score"] = signal
    output["actionability_score"] = actionability
    output["actionability_reason"] = reason
    return output


def attach_actionability_to_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [attach_actionability(row) for row in rows or [] if isinstance(row, dict)]


def _score(*values: Any) -> float:
    for value in values:
        try:
            return max(0.0, min(100.0, float(value)))
        except (TypeError, ValueError):
            continue
    return 0.0
