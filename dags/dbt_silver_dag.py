"""
dbt_silver
==========
Builds and tests the silver layer of the dbt_ai_project medallion pipeline
(cleaned/conformed Bakehouse sales + media tables), then triggers dbt_gold
so the gold marts always run on top of freshly rebuilt silver data.

Runs on Amazon MWAA. See dbt_common.py for how project paths and Databricks
credentials are resolved, and mwaa/startup.sh for the recommended isolated
dbt virtualenv that avoids dependency conflicts with Airflow's own packages.
"""
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.trigger_dagrun import TriggerDagRunOperator

from dbt_common import DBT_CMD_PREFIX, DBT_TARGET, DEFAULT_DBT_ARGS, get_dbt_env

default_args = {
    **DEFAULT_DBT_ARGS,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="dbt_silver",
    description="Build + test the dbt silver layer (Bakehouse sales/media data), then trigger dbt_gold.",
    default_args=default_args,
    schedule="0 6 * * *",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    max_active_runs=1,
    tags=["dbt", "silver", "bakehouse"],
) as dag:

    dbt_run_silver = BashOperator(
        task_id="dbt_run_silver",
        bash_command=f"{DBT_CMD_PREFIX} run --select silver --target {DBT_TARGET}",
        env=get_dbt_env(),
    )

    dbt_test_silver = BashOperator(
        task_id="dbt_test_silver",
        bash_command=f"{DBT_CMD_PREFIX} test --select silver --target {DBT_TARGET}",
        env=get_dbt_env(),
    )

    trigger_dbt_gold = TriggerDagRunOperator(
        task_id="trigger_dbt_gold",
        trigger_dag_id="dbt_gold",
        wait_for_completion=False,
    )

    dbt_run_silver >> dbt_test_silver >> trigger_dbt_gold
