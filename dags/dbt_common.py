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


def _credential_exports() -> str:
    """Shell snippet exporting the dbt Databricks credentials, read fresh
    at task *execution* time via the `airflow variables get` CLI.

    Earlier attempts resolved these via BashOperator's `env` dict, either
    eagerly with Variable.get() (which bakes in whatever the Variables
    held at *DAG parse* time -- updating them afterwards silently had no
    effect until the DAG happened to be re-parsed) or via Jinja templates
    like `{{ var.value.get(...) }}` in the `env` field (which reliably
    rendered to empty strings on this MWAA environment's Airflow version
    for reasons not worth chasing further -- possibly related to the
    newer Task SDK execution model, going by the
    "Using Variable.get from airflow.models is deprecated" warning these
    DAGs also trip). Shelling out to the same `airflow` CLI that's
    already on PATH in every worker sidesteps both problems: it reads
    the Variables at the moment the command actually runs, through
    whichever mechanism is currently correct for this Airflow version,
    independent of our own Python imports or Jinja/dict templating.
    """
    exports = " ".join(
        f'export {env_var}="$(airflow variables get {airflow_var} 2>/dev/null)";'
        for env_var, airflow_var in _DBT_CREDENTIAL_VARS.items()
    )
    return exports + " "


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
    <project-dir>/logs and <project-dir>/target, and exports the
    Databricks credentials (see `_credential_exports`) right before
    invoking dbt.

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
    # TEMP diagnostic: confirms the `airflow variables get`-based exports
    # above actually populated these vars before handing off to dbt.
    # Remove once confirmed working. Avoid `${#VAR}` here (bash
    # string-length syntax) -- bash_command is Jinja-templated by
    # Airflow, and the literal `{#` is parsed as a Jinja comment tag.
    diagnostic_echo = (
        'echo "DBT_ENV_CHECK: catalog=[$DBT_DATABRICKS_CATALOG] '
        "schema=[$DBT_DATABRICKS_SCHEMA] "
        "host_set=${DBT_DATABRICKS_HOST:+yes} token_set=${DBT_DATABRICKS_TOKEN:+yes} "
        'http_path_set=${DBT_DATABRICKS_HTTP_PATH:+yes}"; '
    )
    return (
        f"{_credential_exports()}"
        f"{diagnostic_echo}"
        f"{dbt_invocation}; "
        f"RC=$?; "
        f'if [ "$RC" -ne 0 ]; then '
        f'echo "--- dbt exited with $RC. Tailing {log_file}: ---"; '
        f'tail -n 200 "{log_file}" 2>&1; '
        f"fi; "
        f"exit $RC"
    )
