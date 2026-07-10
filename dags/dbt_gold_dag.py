"""
dbt_gold
========
Builds and tests the gold layer of the dbt_ai_project medallion pipeline:
dimensions, the sales fact table, and the franchise performance / customer
LTV / product performance / review sentiment business marts.

This DAG has no schedule of its own -- it is triggered by dbt_silver once
silver models have been rebuilt, which guarantees gold always runs against
fresh silver data instead of racing a fixed clock offset. It can still be
run manually (e.g. for backfills or gold-only fixes) from the Airflow UI/CLI.
"""
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator

from dbt_common import DEFAULT_DBT_ARGS, dbt_command, get_dbt_env

default_args = {
    **DEFAULT_DBT_ARGS,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="dbt_gold",
    description="Build + test the dbt gold layer (Bakehouse business marts). Triggered by dbt_silver.",
    default_args=default_args,
    schedule=None,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    max_active_runs=1,
    tags=["dbt", "gold", "bakehouse"],
) as dag:

    dbt_run_gold = BashOperator(
        task_id="dbt_run_gold",
        bash_command=dbt_command("run", "gold"),
        env=get_dbt_env(),
        append_env=True,
    )

    dbt_test_gold = BashOperator(
        task_id="dbt_test_gold",
        bash_command=dbt_command("test", "gold"),
        env=get_dbt_env(),
        append_env=True,
    )

    dbt_run_gold >> dbt_test_gold
