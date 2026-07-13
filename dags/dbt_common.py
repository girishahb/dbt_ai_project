"""
Shared configuration and helpers for the dbt Airflow DAGs (dbt_silver_dag.py,
dbt_gold_dag.py). No DAGs are defined here -- Airflow's DAG file processor
will parse this module (since it lives in the dags/ folder) but skip it
because it contains no DAG object.

On Amazon MWAA, Databricks credentials are expected from Airflow
configuration options (e.g. dbt.databricks_host), which MWAA injects as
AIRFLOW__DBT__* environment variables on every worker. Local runs can use
plain DBT_DATABRICKS_* env vars instead.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from typing import Callable

DAGS_DIR = os.path.dirname(os.path.abspath(__file__))


def _find_dbt_project_root(start_dir: str) -> str:
    """Walk upward from start_dir looking for dbt_project.yml."""
    current = start_dir
    for _ in range(5):
        if os.path.isfile(os.path.join(current, "dbt_project.yml")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return start_dir


PROJECT_ROOT = _find_dbt_project_root(DAGS_DIR)
DBT_PROJECT_DIR = PROJECT_ROOT
DBT_PROFILES_DIR = os.path.join(PROJECT_ROOT, "profiles")

_MWAA_DBT_VENV_BIN = "/usr/local/airflow/dbt_venv/bin/dbt"
DBT_BIN = _MWAA_DBT_VENV_BIN if os.path.isfile(_MWAA_DBT_VENV_BIN) else "dbt"
DBT_TARGET = os.environ.get("DBT_TARGET", "prod")

# Writable dirs -- MWAA's synced dags/ folder is root-owned/read-only.
_DBT_ARTIFACTS_DIR = os.path.join(tempfile.gettempdir(), "dbt_ai_project")
DBT_LOG_PATH = os.path.join(_DBT_ARTIFACTS_DIR, "logs")
DBT_TARGET_PATH = os.path.join(_DBT_ARTIFACTS_DIR, "target")

# env var for profiles.yml -> MWAA Airflow configuration option key
# (dbt.databricks_host is injected as AIRFLOW__DBT__DATABRICKS_HOST).
_DBT_CREDENTIAL_CONFIG = {
    "DBT_DATABRICKS_HOST": "dbt.databricks_host",
    "DBT_DATABRICKS_HTTP_PATH": "dbt.databricks_http_path",
    "DBT_DATABRICKS_TOKEN": "dbt.databricks_token",
    "DBT_DATABRICKS_CATALOG": "dbt.databricks_catalog",
    "DBT_DATABRICKS_SCHEMA": "dbt.databricks_schema",
}

DEFAULT_DBT_ARGS = {
    "owner": "data-engineering",
    "retries": 1,
}


def _mwaa_config_env_name(config_key: str) -> str:
    """dbt.databricks_host -> AIRFLOW__DBT__DATABRICKS_HOST"""
    section, key = config_key.split(".", 1)
    return f"AIRFLOW__{section.upper()}__{key.upper()}"


def _load_dbt_credentials() -> dict[str, str]:
    """Resolve Databricks credentials for the dbt subprocess.

    Prefers MWAA Airflow configuration options (AIRFLOW__DBT__*), then
    plain DBT_DATABRICKS_* process env vars (local / .env).
    """
    creds: dict[str, str] = {}
    for env_var, mwaa_config_key in _DBT_CREDENTIAL_CONFIG.items():
        mwaa_env = _mwaa_config_env_name(mwaa_config_key)
        value = os.environ.get(mwaa_env) or os.environ.get(env_var) or ""
        creds[env_var] = value.strip()
    return creds


def make_dbt_callable(subcommand: str, select: str) -> Callable:
    """Return a PythonOperator callable that runs a dbt subcommand."""

    def _run_dbt(**_context) -> None:
        creds = _load_dbt_credentials()
        missing = [k for k, v in creds.items() if not v]
        if missing:
            config_keys = ", ".join(_DBT_CREDENTIAL_CONFIG.values())
            raise RuntimeError(
                "Missing Databricks credentials: "
                + ", ".join(missing)
                + ". On MWAA set Airflow configuration options: "
                + config_keys
                + ". Locally export the matching DBT_DATABRICKS_* env vars."
            )

        os.makedirs(DBT_LOG_PATH, exist_ok=True)
        os.makedirs(DBT_TARGET_PATH, exist_ok=True)

        env = os.environ.copy()
        env.update(creds)

        cmd = [
            DBT_BIN,
            subcommand,
            "--select",
            select,
            "--target",
            DBT_TARGET,
            "--project-dir",
            DBT_PROJECT_DIR,
            "--profiles-dir",
            DBT_PROFILES_DIR,
            "--log-path",
            DBT_LOG_PATH,
            "--target-path",
            DBT_TARGET_PATH,
        ]
        result = subprocess.run(cmd, env=env, text=True, capture_output=False)

        if result.returncode != 0:
            log_file = os.path.join(DBT_LOG_PATH, "dbt.log")
            print(f"--- dbt exited with {result.returncode}. Tailing {log_file}: ---")
            try:
                with open(log_file, encoding="utf-8", errors="replace") as fh:
                    print("".join(fh.readlines()[-200:]))
            except OSError as exc:
                print(f"(could not read dbt.log: {exc})")
            raise RuntimeError(f"dbt {subcommand} failed with exit code {result.returncode}")

    _run_dbt.__name__ = f"dbt_{subcommand}_{select}"
    return _run_dbt
