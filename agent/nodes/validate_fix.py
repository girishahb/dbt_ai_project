from agent.state import SelfHealState
from agent.tools.dbt_runner import build_and_test


def validate_fix(state: SelfHealState) -> dict:
    result = build_and_test(state["repo_path"], state["affected_model"])
    updates: dict = {"validation_output": result.output, "validation_passed": result.success}

    if not result.success:
        retry_count = state.get("retry_count", 0) + 1
        summary = f"Attempt {retry_count}: `dbt build --target ci` still failed:\n{result.output[-800:]}"
        updates["retry_count"] = retry_count
        updates["previous_attempts"] = state.get("previous_attempts", []) + [summary]

    return updates
