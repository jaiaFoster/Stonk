from datetime import date

from app.models.calendar_evolution_policy import load_calendar_evolution_policy
from app.services.calendar_decision_service import decide_calendar_opportunity


def test_pre_entry_monitor_is_not_final_entry_verdict():
    decision = decide_calendar_opportunity(
        {"entry_window_status": "MONITOR_PRE_WINDOW", "verdict": "PASS / OLD ENGINE"},
        lifecycle_stage="SURFACED",
        lifecycle_evaluation_state="STRUCTURE_COMPLETE",
        lifecycle_recommended_action="PREPARE",
        entry_evaluation_eligible=False,
        structure_available=True,
    )

    assert decision.trade_verdict == "NOT_EVALUATED"
    assert decision.recommended_action == "MONITOR"
    assert decision.entry_allowed is False


def test_calendar_policy_defaults_are_entry_window_specific():
    policy = load_calendar_evolution_policy()
    assert policy.is_surface_eligible(14)
    assert not policy.is_entry_allowed(14)
    assert policy.is_entry_allowed(12)
