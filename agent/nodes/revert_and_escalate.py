from agent import config
from agent.state import SelfHealState
from agent.tools import github_client, repo_tools, slack


def revert_and_escalate(state: SelfHealState) -> dict:
    """Only reached after a merge actually happened but the DAG still
    failed on re-run (or the redeploy itself failed) -- leaving a broken
    `main` is worse than an automatic revert, so this reverts first and
    escalates second, rather than just escalating and leaving main broken
    until a human gets to it."""
    if not state.get("merged"):
        message = f":rotating_light: {state.get('final_message', 'Post-merge verification failed before a merge even completed.')}"
        slack.notify(message)
        return {"final_status": "escalated_reverted_after_remerge_failure", "final_message": message}

    try:
        merge_sha = github_client.get_merge_commit_sha(state["pr_number"])
        revert_branch = f"auto-revert/{state['pr_number']}"

        repo_tools.checkout(state["repo_path"], config.GITHUB_DEFAULT_BRANCH)
        repo_tools.pull(state["repo_path"], config.GITHUB_DEFAULT_BRANCH)
        repo_tools.create_branch(state["repo_path"], revert_branch)
        repo_tools.revert_commit(state["repo_path"], merge_sha)
        repo_tools.push(state["repo_path"], revert_branch)

        pr_number, pr_url = github_client.open_pull_request(
            revert_branch,
            f"Revert self-heal fix for {state['affected_model']} (did not actually fix it)",
            f"Automated revert: `{state['dag_id']}` still failed after merging #{state['pr_number']} "
            f"(retrigger run `{state.get('retrigger_run_id')}`). Reverting to the last known-good state.",
        )
        check_passed = github_client.wait_for_required_check(
            pr_number, config.REQUIRED_CHECK_NAME, config.CI_CHECK_POLL_TIMEOUT_S, config.CI_CHECK_POLL_INTERVAL_S
        )
        if check_passed:
            github_client.merge_pull_request(pr_number, f"Revert self-heal fix [#{pr_number}]")
            message = (
                f":leftwards_arrow_with_hook: Reverted the self-heal fix (PR #{pr_number}) -- `{state['dag_id']}` "
                "still failed after merging. Needs a human to actually diagnose this one."
            )
        else:
            github_client.add_label(pr_number, "needs-human-review")
            message = (
                f":rotating_light: Fix didn't work AND the auto-revert PR #{pr_number} couldn't be auto-merged "
                "(required check didn't pass). Needs urgent human attention -- main may still be broken."
            )
    except Exception as exc:  # noqa: BLE001 - this is the last line of defense, must not raise
        message = (
            f":rotating_light: Fix didn't work and the auto-revert itself failed ({exc}). "
            "Needs urgent human attention -- main may still be broken."
        )

    slack.notify(message)
    return {"final_status": "escalated_reverted_after_remerge_failure", "final_message": message}
