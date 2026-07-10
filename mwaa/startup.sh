#!/bin/sh
# MWAA "startup script" -- runs on every Airflow container (scheduler,
# worker, webserver) before requirements.txt is installed and before the
# Airflow process starts.
# https://docs.aws.amazon.com/mwaa/latest/userguide/using-startup-script.html
#
# dbt-core pulls in specific versions of click/jinja2/pydantic/etc. that
# frequently conflict with the versions MWAA pins for Airflow itself.
# Rather than fighting that in the environment's requirements.txt, dbt is
# installed into its own virtualenv here, isolated from Airflow's Python
# environment. The DAGs invoke this venv's dbt binary directly via the
# `dbt_bin_path` Airflow Variable (see dags/dbt_common.py).
#
# Upload this file to S3 and reference it as the environment's "Startup
# script file" (with its version ID) in the MWAA console/Terraform/CDK.

set -e

DBT_VENV_DIR="/usr/local/airflow/dbt_venv"

# Only build the venv on schedulers/workers -- the webserver never runs dbt.
if [ "${MWAA_AIRFLOW_COMPONENT}" != "webserver" ]; then
  if [ ! -f "${DBT_VENV_DIR}/bin/dbt" ]; then
    python3 -m venv "${DBT_VENV_DIR}"
  fi

  "${DBT_VENV_DIR}/bin/pip" install --no-cache-dir --upgrade pip
  "${DBT_VENV_DIR}/bin/pip" install --no-cache-dir \
    dbt-core==1.11.11 \
    dbt-databricks==1.12.1
fi
