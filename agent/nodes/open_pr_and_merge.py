from agent import config
from agent.nodes._pr_common import push_and_open_pr
from agent.state import SelfHealState
from agent.tools import github_client, slack


def open_pr_and_merge(state: SelfHealState) -> dict:
    pr_number, pr_url = push_and_open_pr(state)
    slack.notify(
        f":memo: Self-heal agent opened PR #{pr_number} for `{state['dag_id']}` / `{state['task_id']}` "
        f"and will auto-merge it once `{config.REQUIRED_CHECK_NAME}` passes: {pr_url}"
    )

    # This is the hard backstop: even though risk_gate already said
    # "low_risk", we still refuse to merge unless GitHub itself reports the
    # required dbt_ci check green on this exact commit. If the risk_gate
    # logic ever has a bug, this is what stops a bad merge, not just the
    # agent's own say-so.
    check_passed = github_client.wait_for_required_check(
        pr_number, config.REQUIRED_CHECK_NAME, config.CI_CHECK_POLL_TIMEOUT_S, config.CI_CHECK_POLL_INTERVAL_S
    )
    if not check_passed:
        github_client.add_label(pr_number, "needs-human-review")
        github_client.request_human_review_comment(
            pr_number,
            f"Classified low-risk, but the required `{config.REQUIRED_CHECK_NAME}` check "
            "did not pass (or timed out) on this PR's head commit.",
        )
        return {
            "pr_number": pr_number,
            "pr_url": pr_url,
            "merged": False,
            "final_status": "pr_open_needs_review",
            "final_message": f"PR #{pr_number} opened but the required check didn't pass in time -- needs a human.",
        }

    merged = github_client.merge_pull_request(
        pr_number, f"self-heal: fix {state['affected_model']} ({state['error_type']}) [#{pr_number}]"
    )
    if merged:
        slack.notify(
            f":twisted_rightwards_arrows: Auto-merged PR #{pr_number} -- waiting for redeploy, then "
            f"re-running `{state['dag_id']}` to confirm the fix actually worked."
        )
    return {"pr_number": pr_number, "pr_url": pr_url, "merged": merged}
