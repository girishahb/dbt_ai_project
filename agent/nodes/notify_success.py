from agent.state import SelfHealState
from agent.tools import slack


def notify_success(state: SelfHealState) -> dict:
    message = (
        f":white_check_mark: Self-heal agent fixed `{state['dag_id']}` / `{state['task_id']}` automatically.\n"
        f"PR: {state.get('pr_url')}\n"
        f"Re-run of `{state['dag_id']}` succeeded (run `{state.get('retrigger_run_id')}`)."
    )
    slack.notify(message)
    return {"final_status": "fixed_and_verified", "final_message": message}
