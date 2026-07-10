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
import tempfile

from airflow.models import Variable

DAGS_DIR = os.path.dirname(os.path.abspath(__file__))


def _find_dbt_project_root(start_dir: str) -> str:
    """Walk upward from start_dir looking for dbt_project.yml.

    Deployed layout can vary (dbt_project.yml sitting directly alongside the
    DAG files in dags/, vs. one level up with the DAGs in a dags/ subfolder)
    depending on exactly how the project was synced to S3 -- searching
    upward means this works either way instead of hardcoding one assumption.
    Falls back to the DAG file's own directory if dbt_project.yml can't be
    found nearby at all (e.g. it genuinely hasn't been synced yet).
    """
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

DBT_PROJECT_DIR = Variable.get("dbt_project_dir", default_var=PROJECT_ROOT)
DBT_PROFILES_DIR = Variable.get(
    "dbt_profiles_dir", default_var=os.path.join(PROJECT_ROOT, "profiles")
)

# In MWAA this should point at the isolated virtualenv's dbt binary created
# by mwaa/startup.sh, so dbt-core's dependencies don't conflict with
# Airflow's own pinned packages. Auto-detects that venv if present (so this
# works out of the box on MWAA without remembering to set an Airflow
# Variable) and otherwise falls back to whatever `dbt` resolves to on PATH,
# which is fine for local development where dbt is installed directly.
_MWAA_DBT_VENV_BIN = "/usr/local/airflow/dbt_venv/bin/dbt"
_dbt_bin_default = _MWAA_DBT_VENV_BIN if os.path.isfile(_MWAA_DBT_VENV_BIN) else "dbt"
DBT_BIN = Variable.get("dbt_bin_path", default_var=_dbt_bin_default)

DBT_TARGET = Variable.get("dbt_target", default_var="prod")

# dbt always needs to write logs/ and target/ (compiled SQL, manifest.json,
# run_results.json) under wherever these paths point. In MWAA --project-dir
# is the S3-synced dags/ folder, which is root-owned and read-only to the
# airflow user that actually runs tasks -- dbt fails almost instantly (before
# it can log anything useful) trying to create either directory there. Point
# both at a writable tmp dir instead, completely independent of --project-dir.
_DBT_ARTIFACTS_DIR = os.path.join(tempfile.gettempdir(), "dbt_ai_project")
DBT_LOG_PATH = Variable.get(
    "dbt_log_path", default_var=os.path.join(_DBT_ARTIFACTS_DIR, "logs")
)
DBT_TARGET_PATH = Variable.get(
    "dbt_target_path", default_var=os.path.join(_DBT_ARTIFACTS_DIR, "target")
)

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
    """Build the *additional* environment variables for a dbt subprocess call.

    Returns Jinja template strings (`{{ var.value.get(...) }}`), not
    resolved values, and deliberately only for the handful of DBT_*
    credential vars rather than a copy of the full os.environ:

    - `env` is a templated field on BashOperator, so Airflow re-renders
      these Jinja expressions at *task execution* time, reading whatever
      the Airflow Variables currently hold. Returning already-resolved
      Variable.get(...) values here instead would bake in whatever they
      were at *DAG parse* time (this module is imported once by the DAG
      file processor and the resulting task definitions get cached/
      serialized) -- updating an Airflow Variable would then silently not
      take effect until Airflow happened to re-parse the DAG file, which
      caused exactly this: stale/empty credentials being used run after
      run despite the Variables being fixed in the UI.
    - Only including these specific keys (vs. the full environment) avoids
      a separate problem: Airflow/Jinja treats any templated string value
      ending in ".sh"/".bash" as a path to a template *file* to load
      rather than a literal string, and MWAA's own environment includes
      vars like MWAA__CORE__STARTUP_SCRIPT_PATH=.../startup.sh, so merging
      the full environment here causes a
      `TemplateNotFound: '.../startup.sh' not found in search path` error.

    Use this together with `append_env=True` on BashOperator: Airflow then
    inherits the full parent environment at execution time and only
    overlays these few (freshly rendered) keys on top.
    """
    return {
        env_var: f"{{{{ var.value.get('{airflow_var}', '') }}}}"
        for env_var, airflow_var in _DBT_CREDENTIAL_VARS.items()
    }


DEFAULT_DBT_ARGS = {
    "owner": "data-engineering",
    "retries": 1,
}


def dbt_command(subcommand: str, select: str) -> str:
    """Build a dbt CLI invocation with the subcommand first.

    `--project-dir`/`--profiles-dir` must come *after* the subcommand
    (`dbt run --project-dir ...`) -- putting them before it
    (`dbt --project-dir ... run`) raises `Error: No such option
    '--project-dir'` on this dbt version, even though it looks like it
    should be a valid global flag position.

    Also redirects dbt's logs/target dirs to DBT_LOG_PATH/DBT_TARGET_PATH
    (see module docstring above) instead of letting them default to
    <project-dir>/logs and <project-dir>/target.

    On failure, also tails dbt's own log file. dbt routes most of its
    detailed logging to <log-path>/dbt.log rather than the console --
    if it fails early (e.g. profile/connection validation) the console
    output can be completely empty even though the real error is
    sitting in that file, which otherwise makes CloudWatch task logs
    useless for diagnosing the failure.
    """
    dbt_invocation = (
        f'"{DBT_BIN}" {subcommand} --select {select} --target {DBT_TARGET} '
        f'--project-dir "{DBT_PROJECT_DIR}" --profiles-dir "{DBT_PROFILES_DIR}" '
        f'--log-path "{DBT_LOG_PATH}" --target-path "{DBT_TARGET_PATH}"'
    )
    log_file = os.path.join(DBT_LOG_PATH, "dbt.log")
    # TEMP diagnostic: prints what the shell actually sees for these vars
    # (lengths only for host/token to avoid leaking secrets into logs) to
    # pin down whether get_dbt_env()'s rendered values are actually making
    # it into the dbt subprocess's environment. Remove once catalog/host
    # resolve correctly.
    diagnostic_echo = (
        'echo "DBT_ENV_CHECK: catalog=[$DBT_DATABRICKS_CATALOG] '
        "schema=[$DBT_DATABRICKS_SCHEMA] "
        'host_len=${#DBT_DATABRICKS_HOST} token_len=${#DBT_DATABRICKS_TOKEN} '
        'http_path_len=${#DBT_DATABRICKS_HTTP_PATH}"; '
    )
    return (
        f"{diagnostic_echo}"
        f"{dbt_invocation}; "
        f"RC=$?; "
        f'if [ "$RC" -ne 0 ]; then '
        f'echo "--- dbt exited with $RC. Tailing {log_file}: ---"; '
        f'tail -n 200 "{log_file}" 2>&1; '
        f"fi; "
        f"exit $RC"
    )
