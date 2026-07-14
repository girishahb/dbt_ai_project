"""
Dispatcher Lambda
=================
Sits between the EventBridge "DbtTaskFailed" event (published by
dags/dbt_common.py's on_failure_callback) and the LangGraph self-heal agent's
Fargate task. Deliberately does almost nothing itself:

1. Circuit breaker -- atomically claims a DynamoDB item keyed by
   dag_id+task_id+date. If it's already claimed (an attempt already ran
   today), skip starting the agent and just post a Slack heads-up instead --
   this is what stops a fix that doesn't actually work from re-triggering
   itself into a fix/fail/re-fix loop.
2. Otherwise, start one Fargate task (RunTask) with the failure context
   (dag_id/task_id/run_id/try_number/log_url) passed in as container
   environment overrides, and return.

All the actual log-fetching, diagnosis, patching, and PR/merge logic lives
in the agent/ container -- this function's only job is "should we start it,
and if so, start it."
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone

import boto3

dynamodb = boto3.client("dynamodb")
ecs = boto3.client("ecs")
secretsmanager = boto3.client("secretsmanager")

TABLE_NAME = os.environ["CIRCUIT_BREAKER_TABLE_NAME"]
CLUSTER_ARN = os.environ["ECS_CLUSTER_ARN"]
TASK_DEFINITION_ARN = os.environ["ECS_TASK_DEFINITION_ARN"]
CONTAINER_NAME = os.environ.get("ECS_CONTAINER_NAME", "agent")
SUBNET_IDS = os.environ["SUBNET_IDS"].split(",")
SECURITY_GROUP_IDS = os.environ["SECURITY_GROUP_IDS"].split(",")
SLACK_WEBHOOK_SECRET_ARN = os.environ.get("SLACK_WEBHOOK_SECRET_ARN")
TTL_HOURS = int(os.environ.get("CIRCUIT_BREAKER_TTL_HOURS", "48"))


def _claim_attempt(dag_id: str, task_id: str) -> bool:
    """Atomically claim today's attempt slot. Returns True if claimed (i.e.
    this is the first attempt today and the agent should run), False if
    someone/something already claimed it."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    attempt_key = f"{dag_id}#{task_id}#{today}"
    expires_at = int((datetime.now(timezone.utc) + timedelta(hours=TTL_HOURS)).timestamp())

    try:
        dynamodb.put_item(
            TableName=TABLE_NAME,
            Item={
                "attempt_key": {"S": attempt_key},
                "claimed_at": {"S": datetime.now(timezone.utc).isoformat()},
                "expires_at": {"N": str(expires_at)},
            },
            ConditionExpression="attribute_not_exists(attempt_key)",
        )
        return True
    except dynamodb.exceptions.ConditionalCheckFailedException:
        return False


def _notify_slack(message: str) -> None:
    if not SLACK_WEBHOOK_SECRET_ARN:
        print(f"(no Slack webhook configured) {message}")
        return
    try:
        webhook_url = secretsmanager.get_secret_value(SecretId=SLACK_WEBHOOK_SECRET_ARN)["SecretString"]
        req = urllib.request.Request(
            webhook_url,
            data=json.dumps({"text": message}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as exc:  # noqa: BLE001 - notification failure shouldn't fail the invocation
        print(f"could not post to Slack: {exc}")


def _start_agent_task(detail: dict) -> str:
    response = ecs.run_task(
        cluster=CLUSTER_ARN,
        taskDefinition=TASK_DEFINITION_ARN,
        launchType="FARGATE",
        count=1,
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": SUBNET_IDS,
                "securityGroups": SECURITY_GROUP_IDS,
                # DISABLED because these are the same private subnets MWAA
                # itself runs in -- outbound internet (Databricks/GitHub/
                # Bedrock) goes through the subnet's NAT gateway via its
                # route table, not through a public IP on the task's ENI.
                # A public subnet setup would need "ENABLED" instead.
                "assignPublicIp": "DISABLED",
            }
        },
        overrides={
            "containerOverrides": [
                {
                    "name": CONTAINER_NAME,
                    "environment": [
                        {"name": "FAILURE_DAG_ID", "value": detail["dag_id"]},
                        {"name": "FAILURE_TASK_ID", "value": detail["task_id"]},
                        {"name": "FAILURE_RUN_ID", "value": detail["run_id"]},
                        {"name": "FAILURE_TRY_NUMBER", "value": str(detail.get("try_number", 1))},
                        {"name": "FAILURE_LOG_URL", "value": detail.get("log_url", "")},
                    ],
                }
            ]
        },
    )
    if response.get("failures"):
        raise RuntimeError(f"ECS RunTask failures: {response['failures']}")
    return response["tasks"][0]["taskArn"]


def handler(event: dict, _context) -> dict:
    detail = event["detail"]
    dag_id, task_id = detail["dag_id"], detail["task_id"]

    if not _claim_attempt(dag_id, task_id):
        _notify_slack(
            f":warning: `{dag_id}` / `{task_id}` failed again, but an auto-fix attempt "
            "already ran today -- skipping to avoid a fix/fail/re-fix loop. Needs a human look."
        )
        return {"started": False, "reason": "circuit_breaker_already_claimed"}

    task_arn = _start_agent_task(detail)
    _notify_slack(f":robot_face: `{dag_id}` / `{task_id}` failed -- self-heal agent started ({task_arn}).")
    return {"started": True, "task_arn": task_arn}
