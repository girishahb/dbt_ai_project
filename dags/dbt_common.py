"""
Shared configuration and helpers for the dbt Airflow DAGs (dbt_silver_dag.py,
dbt_gold_dag.py). No DAGs are defined here -- Airflow's DAG file processor
will parse this module (since it lives in the dags/ folder) but skip it
because it contains no DAG object.

Designed to run unmodified both locally and in Amazon MWAA:
  - DBT_PROJECT_DIR / DBT_PROFILES_DIR are resolved relative to this file's
    location, so no environment-specific absolute paths are hardcoded. This
    works whether the project is synced to MWAA as
    s3://<bucket>/dags/... -> /usr/local/airflow/dags/... or checked out
    locally at any path.
  - Databricks credentials and deployment paths are read from Airflow
    Variables first (so in MWAA they can be backed by the Secrets Manager
    secrets backend: https://docs.aws.amazon.com/mwaa/latest/userguide/connections-secrets-manager.html),
    falling back to whatever is already in the process environment so the
    same DAGs can be parsed/tested locally without an Airflow Variable store.
"""
import os

from airflow.models import Variable

DAGS_DIR = os.path.dirname(os.path.abspath(__file__))
# The dbt project root is the parent of dags/ (dbt_project.yml, models/,
# macros/, profiles/ all live one level up from this file).
PROJECT_ROOT = os.path.dirname(DAGS_DIR)

DBT_PROJECT_DIR = Variable.get("dbt_project_dir", default_var=PROJECT_ROOT)
DBT_PROFILES_DIR = Variable.get(
    "dbt_profiles_dir", default_var=os.path.join(PROJECT_ROOT, "profiles")
)

# In MWAA this should point at the isolated virtualenv's dbt binary created
# by mwaa/startup.sh (e.g. /usr/local/airflow/dbt_venv/bin/dbt) to avoid
# dependency conflicts between dbt-core and Airflow's own pinned packages.
# Defaults to whatever `dbt` resolves to on PATH, which is fine for local
# development where dbt is installed directly.
DBT_BIN = Variable.get("dbt_bin_path", default_var="dbt")

DBT_TARGET = Variable.get("dbt_target", default_var="prod")

# Maps the env var dbt's profiles.yml expects -> the Airflow Variable key
# it should be sourced from.
_DBT_CREDENTIAL_VARS = {
    "DBT_DATABRICKS_HOST": "dbt_databricks_host",
    "DBT_DATABRICKS_HTTP_PATH": "dbt_databricks_http_path",
    "DBT_DATABRICKS_TOKEN": "dbt_databricks_token",
    "DBT_DATABRICKS_CATALOG": "dbt_databricks_catalog",
    "DBT_DATABRICKS_SCHEMA": "dbt_databricks_schema",
}


def get_dbt_env() -> dict:
    """Build the environment for a dbt subprocess call.

    Starts from the current process environment (so PATH, HOME, etc. are
    preserved for BashOperator) and overlays Databricks connection details
    pulled from Airflow Variables, falling back to any same-named value
    already in the environment for local runs.
    """
    env = os.environ.copy()
    for env_var, airflow_var in _DBT_CREDENTIAL_VARS.items():
        env[env_var] = Variable.get(airflow_var, default_var=env.get(env_var, ""))
    return env


DEFAULT_DBT_ARGS = {
    "owner": "data-engineering",
    "retries": 1,
}

DBT_CMD_PREFIX = f'"{DBT_BIN}" --project-dir "{DBT_PROJECT_DIR}" --profiles-dir "{DBT_PROFILES_DIR}"'
