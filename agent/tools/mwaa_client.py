"""
Thin wrapper around the Amazon MWAA-hosted Airflow REST API.

MWAA doesn't expose the Airflow webserver on the open internet with normal
Airflow auth -- instead you exchange an IAM-signed `create_web_login_token`
call for a short-lived Airflow session cookie, then talk to the *same*
REST API any self-hosted Airflow exposes
(https://airflow.apache.org/docs/apache-airflow/stable/stable-rest-api-ref.html).
That's what every method below does: get a fresh session, then one normal
REST call.

Falls back to scanning CloudWatch Logs directly for `fetch_task_log` if the
REST call fails for any reason (e.g. log already rotated out of the
webserver's view) -- MWAA ships every task log to a
`airflow-<environment>-task` log group as well, so the log text is
recoverable even then.
"""
from __future__ import annotations

import time

import boto3
import requests

_SESSION_TIMEOUT_S = 15


class MwaaClient:
    def __init__(self, environment_name: str, region: str | None = None):
        self.environment_name = environment_name
        self._mwaa = boto3.client("mwaa", region_name=region)
        self._logs = boto3.client("logs", region_name=region)

    def _web_session(self) -> tuple[str, str]:
        """Returns (web_server_hostname, session_cookie)."""
        resp = self._mwaa.create_web_login_token(Name=self.environment_name)
        hostname = resp["WebServerHostname"]
        login_resp = requests.post(
            f"https://{hostname}/aws_mwaa/login",
            data={"token": resp["WebToken"]},
            timeout=_SESSION_TIMEOUT_S,
        )
        login_resp.raise_for_status()
        return hostname, login_resp.cookies["session"]

    def fetch_task_log(self, dag_id: str, run_id: str, task_id: str, try_number: int) -> str:
        try:
            hostname, cookie = self._web_session()
            url = (
                f"https://{hostname}/api/v1/dags/{dag_id}/dagRuns/{run_id}"
                f"/taskInstances/{task_id}/logs/{try_number}"
            )
            resp = requests.get(
                url,
                cookies={"session": cookie},
                headers={"Accept": "application/json"},
                timeout=_SESSION_TIMEOUT_S,
            )
            resp.raise_for_status()
            content = resp.json().get("content", "")
            if content:
                return content
        except Exception as exc:  # noqa: BLE001 - fall through to CloudWatch
            print(f"MWAA REST API log fetch failed ({exc}), falling back to CloudWatch Logs")

        return self._fetch_task_log_from_cloudwatch(dag_id, run_id, task_id, try_number)

    def _fetch_task_log_from_cloudwatch(
        self, dag_id: str, run_id: str, task_id: str, try_number: int
    ) -> str:
        log_group = f"airflow-{self.environment_name}-task"
        # Default MWAA/Airflow 2.x log filename template:
        # dag_id={dag}/run_id={run}/task_id={task}/attempt={try}.log
        stream_prefix = f"dag_id={dag_id}/run_id={run_id}/task_id={task_id}/attempt={try_number}"
        streams = self._logs.describe_log_streams(
            logGroupName=log_group, logStreamNamePrefix=stream_prefix
        ).get("logStreams", [])
        if not streams:
            return f"(no CloudWatch log stream found with prefix {stream_prefix!r} in {log_group})"

        lines: list[str] = []
        next_token = None
        while True:
            kwargs = {
                "logGroupName": log_group,
                "logStreamName": streams[0]["logStreamName"],
                "startFromHead": True,
            }
            if next_token:
                kwargs["nextToken"] = next_token
            resp = self._logs.get_log_events(**kwargs)
            lines.extend(event["message"] for event in resp["events"])
            if not resp["events"] or resp.get("nextForwardToken") == next_token:
                break
            next_token = resp.get("nextForwardToken")
        return "\n".join(lines)

    def trigger_dag_run(self, dag_id: str) -> str:
        """POSTs a new manual dagRun. Returns the new run's dag_run_id."""
        hostname, cookie = self._web_session()
        resp = requests.post(
            f"https://{hostname}/api/v1/dags/{dag_id}/dagRuns",
            cookies={"session": cookie},
            json={"conf": {}},
            timeout=_SESSION_TIMEOUT_S,
        )
        resp.raise_for_status()
        return resp.json()["dag_run_id"]

    def get_dag_run_state(self, dag_id: str, run_id: str) -> str:
        hostname, cookie = self._web_session()
        resp = requests.get(
            f"https://{hostname}/api/v1/dags/{dag_id}/dagRuns/{run_id}",
            cookies={"session": cookie},
            timeout=_SESSION_TIMEOUT_S,
        )
        resp.raise_for_status()
        return resp.json()["state"]

    def wait_for_dag_run(self, dag_id: str, run_id: str, timeout_s: int, poll_interval_s: int) -> str:
        """Polls until the run leaves queued/running, or timeout_s elapses.
        Returns the final observed state (may still be 'running' on timeout)."""
        deadline = time.monotonic() + timeout_s
        state = "queued"
        while time.monotonic() < deadline:
            state = self.get_dag_run_state(dag_id, run_id)
            if state not in ("queued", "running"):
                return state
            time.sleep(poll_interval_s)
        return state
