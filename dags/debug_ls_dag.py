"""
Temporary diagnostic DAG -- inspects what actually landed in the MWAA
dags/ folder sync on the worker filesystem, to debug why
dags/profiles/profiles.yml isn't showing up even though it's confirmed
present in S3. Safe to delete once the sync issue is resolved.
"""
from datetime import datetime

from airflow import DAG
from airflow.operators.bash import BashOperator

with DAG(
    dag_id="debug_ls",
    description="TEMP: inspect the synced dags/ folder contents on the worker filesystem.",
    schedule=None,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["debug"],
) as dag:

    ls_dags = BashOperator(
        task_id="ls_dags",
        bash_command=(
            "echo '--- top-level /usr/local/airflow/dags/ ---'; "
            "ls -la /usr/local/airflow/dags/ 2>&1; "
            "echo '--- recursive find (maxdepth 4) ---'; "
            "find /usr/local/airflow/dags -maxdepth 4 2>&1; "
            "echo '--- profiles dir check ---'; "
            "ls -la /usr/local/airflow/dags/profiles 2>&1; "
            "echo '--- dbt venv check ---'; "
            "ls -la /usr/local/airflow/dbt_venv/bin/ 2>&1; "
            "true"
        ),
    )
