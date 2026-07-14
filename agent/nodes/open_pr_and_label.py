from agent.nodes._pr_common import push_and_open_pr
from agent.state import SelfHealState
from agent.tools import github_client, slack


def open_pr_and_label(state: SelfHealState) -> dict:
    pr_number, pr_url = push_and_open_pr(state)
    reasons = "; ".join(state.get("risk_reasons", []))
    github_client.add_label(pr_number, "needs-human-review")
    github_client.request_human_review_comment(pr_number, f"Risk gate flagged this as needs-review: {reasons}")

    message = (
        f":large_orange_diamond: Self-heal agent fixed `{state['dag_id']}` / `{state['task_id']}` but the change "
        f"needs a human to merge it -- {reasons}.\nPR: {pr_url}"
    )
    slack.notify(message)

    return {
        "pr_number": pr_number,
        "pr_url": pr_url,
        "merged": False,
        "final_status": "pr_open_needs_review",
        "final_message": f"PR #{pr_number} opened and labeled needs-human-review ({reasons}).",
    }
