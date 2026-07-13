"""
Shared configuration and helpers for the dbt Airflow DAGs (dbt_silver_dag.py,
dbt_gold_dag.py). No DAGs are defined here -- Airflow's DAG file processor
will parse this module (since it lives in the dags/ folder) but skip it
because it contains no DAG object.

Designed to run unmodified both locally and in Amazon MWAA:
  - Project paths are resolved relative to this file's location.
  - Databricks credentials are read at *task execution* time via
    airflow.sdk.Variable (Airflow 3), falling back to process environment
    variables (so MWAA console env vars also work). Values are never baked
    in at DAG parse time.
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

# Maps the env var dbt's profiles.yml expects -> the Airflow Variable key.
_DBT_CREDENTIAL_VARS = {
    "DBT_DATABRICKS_HOST": "dbt_databricks_host",
    "DBT_DATABRICKS_HTTP_PATH": "dbt_databricks_http_path",
    "DBT_DATABRICKS_TOKEN": "dbt_databricks_token",
    "DBT_DATABRICKS_CATALOG": "dbt_databricks_catalog",
    "DBT_DATABRICKS_SCHEMA": "dbt_databricks_schema",
}

DEFAULT_DBT_ARGS = {
    "owner": "data-engineering",
    "retries": 1,
}


def _get_variable(key: str) -> tuple[str | None, str]:
    """Try to read an Airflow Variable. Returns (value_or_None, source_note)."""
    # 1) Airflow's env-var backend: AIRFLOW_VAR_<KEY> (uppercase)
    env_key = f"AIRFLOW_VAR_{key.upper()}"
    if env_key in os.environ and os.environ[env_key] != "":
        return os.environ[env_key], f"env:{env_key}"

    # 2) Task SDK (Airflow 3) -- may fail on some MWAA worker setups
    try:
        from airflow.sdk import Variable as SdkVariable

        try:
            value = SdkVariable.get(key)
            if value is not None and str(value) != "":
                return str(value), "airflow.sdk.Variable"
        except Exception as exc:  # VARIABLE_NOT_FOUND or supervisor errors
            sdk_err = f"{type(exc).__name__}: {exc}"
        else:
            sdk_err = "empty"
    except Exception as exc:
        sdk_err = f"import/get failed: {type(exc).__name__}: {exc}"

    # 3) Legacy models API (usually no DB access on Airflow 3 workers)
    try:
        from airflow.models import Variable as ModelsVariable

        value = ModelsVariable.get(key)
        if value is not None and str(value) != "":
            return str(value), "airflow.models.Variable"
    except Exception:
        pass

    return None, f"not found (sdk: {sdk_err})"


def _load_dbt_credentials() -> dict[str, str]:
    """Resolve Databricks credentials.

    Order (first hit wins):
      1. Process env vars DBT_DATABRICKS_* (MWAA console environment variables)
      2. AIRFLOW_VAR_<key> / airflow.sdk.Variable / airflow.models.Variable

    On this MWAA Airflow 3 environment, Admin → Variables are visible in the
    UI but Variable.get from workers often returns nothing (Task SDK /
    supervisor). Setting the same values as MWAA environment variables is
    the reliable path.
    """
    creds: dict[str, str] = {}
    sources: list[str] = []
    for env_var, airflow_var in _DBT_CREDENTIAL_VARS.items():
        if os.environ.get(env_var):
            creds[env_var] = os.environ[env_var].strip()
            sources.append(f"{env_var}=env")
            continue

        value, source = _get_variable(airflow_var)
        if value:
            creds[env_var] = value.strip()
            sources.append(f"{env_var}={source}")
        else:
            creds[env_var] = ""
            sources.append(f"{env_var}=MISSING ({source})")

    print("DBT_CRED_SOURCES: " + "; ".join(sources))
    return creds


def make_dbt_callable(subcommand: str, select: str) -> Callable:
    """Return a PythonOperator callable that runs a dbt subcommand.

    Credentials are loaded inside the callable (task execution time), not
    when the DAG file is parsed.
    """

    def _run_dbt(**_context) -> None:
        creds = _load_dbt_credentials()
        catalog = creds.get("DBT_DATABRICKS_CATALOG", "")
        schema = creds.get("DBT_DATABRICKS_SCHEMA", "")
        print(
            "DBT_ENV_CHECK: "
            f"catalog=[{catalog}] schema=[{schema}] "
            f"host_set={'yes' if creds.get('DBT_DATABRICKS_HOST') else 'no'} "
            f"token_set={'yes' if creds.get('DBT_DATABRICKS_TOKEN') else 'no'} "
            f"http_path_set={'yes' if creds.get('DBT_DATABRICKS_HTTP_PATH') else 'no'}"
        )

        missing = [k for k, v in creds.items() if not v]
        if missing:
            raise RuntimeError(
                "Missing Databricks credentials: "
                + ", ".join(missing)
                + ". Admin → Variables are not readable from MWAA workers on "
                "this Airflow 3 setup. Set these as MWAA environment variables "
                "in the AWS console (Environment → Edit → Environment variables): "
                + ", ".join(_DBT_CREDENTIAL_VARS.keys())
                + ". Then wait for the environment update to finish and re-run."
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
        print("Running:", " ".join(cmd))
        result = subprocess.run(cmd, env=env, text=True, capture_output=False)

        if result.returncode != 0:
            log_file = os.path.join(DBT_LOG_PATH, "dbt.log")
            print(f"--- dbt exited with {result.returncode}. Tailing {log_file}: ---")
            try:
                with open(log_file, encoding="utf-8", errors="replace") as fh:
                    lines = fh.readlines()
                print("".join(lines[-200:]))
            except OSError as exc:
                print(f"(could not read dbt.log: {exc})")
            raise RuntimeError(f"dbt {subcommand} failed with exit code {result.returncode}")

    _run_dbt.__name__ = f"dbt_{subcommand}_{select}"
    return _run_dbt
