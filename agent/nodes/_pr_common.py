from agent.state import SelfHealState
from agent.tools import github_client, repo_tools


def push_and_open_pr(state: SelfHealState) -> tuple[int, str]:
    repo_tools.push(state["repo_path"], state["branch_name"])
    title = f"self-heal: fix {state['affected_model']} ({state['error_type']})"
    body = (
        f"**Triggered by:** Airflow DAG `{state['dag_id']}` / task `{state['task_id']}` (run `{state['run_id']}`)\n"
        f"**Log:** {state.get('log_url') or '(n/a)'}\n\n"
        f"**Root cause:** {state.get('classification_reasoning', '')}\n\n"
        f"**Fix:** {state.get('fix_reasoning', '')}\n\n"
        f"**Validation (`dbt build --target ci`):**\n```\n{state.get('validation_output', '')[-3000:]}\n```\n\n"
        "_Opened automatically by the dbt self-heal agent. See agent/README.md._"
    )
    return github_client.open_pull_request(state["branch_name"], title, body)
