from agent.state import SelfHealState
from agent.tools import slack

_DEFAULT_REASONS = {
    "data_quality": (
        "This looks like a data-quality test failure (bad/unexpected data), not a bug in the "
        "SQL -- the agent never attempts to fix these by loosening or removing tests."
    ),
    "unknown": "Could not confidently classify this failure as something safe to auto-fix.",
}


def escalate(state: SelfHealState) -> dict:
    reason = state.get("final_message") or _DEFAULT_REASONS.get(
        state.get("error_type", "unknown"),
        "Validation retries were exhausted without a fix that actually resolves the failure.",
    )
    final_status = state.get("final_status") or (
        "escalated_unfixable"
        if state.get("error_type") in ("data_quality", "unknown")
        else "escalated_validation_exhausted"
    )

    message = (
        f":rotating_light: Self-heal agent could NOT fix `{state.get('dag_id')}` / `{state.get('task_id')}` "
        f"(run `{state.get('run_id')}`) automatically.\n{reason}\nNeeds a human look."
    )
    slack.notify(message)
    return {"final_status": final_status, "final_message": reason}
