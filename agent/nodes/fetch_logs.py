from agent import config
from agent.state import SelfHealState
from agent.tools.mwaa_client import MwaaClient


def fetch_logs(state: SelfHealState) -> dict:
    client = MwaaClient(config.MWAA_ENVIRONMENT_NAME)
    log_text = client.fetch_task_log(
        dag_id=state["dag_id"],
        run_id=state["run_id"],
        task_id=state["task_id"],
        try_number=state["try_number"],
    )
    # dbt's own error output is almost always in the last ~300 lines --
    # keeping the prompt small and on-topic matters more here than
    # completeness, and Airflow task logs include a lot of unrelated
    # scheduler/heartbeat noise before the actual dbt invocation.
    trimmed = "\n".join(log_text.splitlines()[-300:])
    return {"log_text": trimmed}
