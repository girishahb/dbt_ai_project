from agent import config
from agent.state import SelfHealState
from agent.tools import github_client
from agent.tools.mwaa_client import MwaaClient


def retrigger_dag(state: SelfHealState) -> dict:
    # Confirm the redeploy (deploy-mwaa.yml, triggered by the merge landing
    # on main) actually finished before re-running the DAG -- otherwise
    # we'd just re-execute the same broken code that was already there and
    # wrongly conclude the fix failed.
    merge_sha = github_client.get_merge_commit_sha(state["pr_number"])
    deployed = github_client.wait_for_workflow_run_on_main(
        config.DEPLOY_WORKFLOW_FILE,
        after_sha=merge_sha,
        timeout_s=config.CI_CHECK_POLL_TIMEOUT_S,
        poll_interval_s=config.CI_CHECK_POLL_INTERVAL_S,
    )
    if not deployed:
        return {
            "retrigger_succeeded": False,
            "final_status": "escalated_reverted_after_remerge_failure",
            "final_message": (
                f"`{config.DEPLOY_WORKFLOW_FILE}` did not complete successfully after merging "
                f"PR #{state['pr_number']} -- treating this as a failed fix."
            ),
        }

    client = MwaaClient(config.MWAA_ENVIRONMENT_NAME)
    run_id = client.trigger_dag_run(state["dag_id"])
    final_state = client.wait_for_dag_run(
        state["dag_id"], run_id, config.CI_CHECK_POLL_TIMEOUT_S, config.CI_CHECK_POLL_INTERVAL_S
    )
    return {"retrigger_run_id": run_id, "retrigger_succeeded": final_state == "success"}
