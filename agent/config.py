"""
Central config for the self-heal agent. Everything here is read from the
environment so the exact same image runs as the Fargate task, and locally
via `python -m agent.main` for testing (see agent/README.md).
"""
from __future__ import annotations

import os


def _env_list(name: str, default: str = "") -> list[str]:
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# --- Failure context (set per-invocation by the dispatcher Lambda's
# ECS RunTask containerOverrides -- see infra/lambda.tf / dispatcher/handler.py) ---
FAILURE_DAG_ID = os.environ.get("FAILURE_DAG_ID", "")
FAILURE_TASK_ID = os.environ.get("FAILURE_TASK_ID", "")
FAILURE_RUN_ID = os.environ.get("FAILURE_RUN_ID", "")
FAILURE_TRY_NUMBER = int(os.environ.get("FAILURE_TRY_NUMBER", "1"))
FAILURE_LOG_URL = os.environ.get("FAILURE_LOG_URL", "")

# --- MWAA ---
MWAA_ENVIRONMENT_NAME = os.environ.get("MWAA_ENVIRONMENT_NAME", "")

# --- Bedrock ---
BEDROCK_MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
)
BEDROCK_REGION = os.environ.get("AWS_REGION", "us-east-1")

# --- GitHub ---
GITHUB_OWNER = os.environ.get("GITHUB_OWNER", "girishahb")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "dbt_ai_project")
GITHUB_DEFAULT_BRANCH = os.environ.get("GITHUB_DEFAULT_BRANCH", "main")
GITHUB_APP_ID = os.environ.get("GITHUB_APP_ID", "")
# Secret values (private key / installation id / Databricks CI token /
# Slack webhook) are injected as env vars by the ECS task definition
# reading from Secrets Manager (see infra/ecs.tf) -- the agent code itself
# never calls Secrets Manager directly, keeping the "where secrets live" in
# one place (the task definition).
GITHUB_APP_PRIVATE_KEY = os.environ.get("GITHUB_APP_PRIVATE_KEY", "")
GITHUB_APP_INSTALLATION_ID = os.environ.get("GITHUB_APP_INSTALLATION_ID", "")
REQUIRED_CHECK_NAME = os.environ.get("REQUIRED_CHECK_NAME", "dbt-build")

# --- dbt / Databricks ---
DBT_PROJECT_SUBDIR = os.environ.get("DBT_PROJECT_SUBDIR", ".")
DBT_PROFILES_SUBDIR = os.environ.get("DBT_PROFILES_SUBDIR", "profiles")
DBT_CI_TARGET = os.environ.get("DBT_CI_TARGET", "ci")

# --- Guardrails ---
MAX_FIX_RETRIES = int(os.environ.get("MAX_FIX_RETRIES", "3"))
ALLOWED_WRITE_PREFIXES = tuple(_env_list("ALLOWED_WRITE_PREFIXES", "models/"))
LOW_RISK_MAX_FILES = int(os.environ.get("LOW_RISK_MAX_FILES", "2"))
LOW_RISK_MAX_LINES = int(os.environ.get("LOW_RISK_MAX_LINES", "80"))
LOW_RISK_ERROR_TYPES = tuple(
    _env_list("LOW_RISK_ERROR_TYPES", "missing_column,compile_error")
)
CI_CHECK_POLL_TIMEOUT_S = int(os.environ.get("CI_CHECK_POLL_TIMEOUT_S", "900"))
CI_CHECK_POLL_INTERVAL_S = int(os.environ.get("CI_CHECK_POLL_INTERVAL_S", "20"))
DEPLOY_WORKFLOW_FILE = os.environ.get("DEPLOY_WORKFLOW_FILE", "deploy-mwaa.yml")

WORKDIR = os.environ.get("AGENT_WORKDIR", "/tmp/self-heal-agent")
