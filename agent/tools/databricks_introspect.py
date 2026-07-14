"""
Read-only Databricks schema introspection, used by the propose_fix node so
the LLM sees the *actual* upstream column names/types instead of guessing
from the error text alone (the whole point of a "missing column" fix is
knowing exactly what the real column is called).
"""
from __future__ import annotations

import os

from databricks import sql


def _connect():
    return sql.connect(
        server_hostname=os.environ["DBT_DATABRICKS_HOST"],
        http_path=os.environ["DBT_DATABRICKS_HTTP_PATH"],
        access_token=os.environ.get("DBT_DATABRICKS_CI_TOKEN") or os.environ["DBT_DATABRICKS_TOKEN"],
    )


def describe_table(catalog: str, schema: str, table: str) -> str:
    """Returns DESCRIBE TABLE output as a compact "col_name: type" listing.

    Never raises out of this function -- an introspection failure (typo'd
    schema, table genuinely doesn't exist yet, transient connectivity) is
    valuable context for the LLM too ("this table doesn't exist"), not a
    reason to crash the whole graph run.
    """
    try:
        with _connect() as conn, conn.cursor() as cursor:
            cursor.execute(f"DESCRIBE TABLE {catalog}.{schema}.{table}")
            rows = cursor.fetchall()
            lines = [
                f"{row.col_name}: {row.data_type}"
                for row in rows
                if row.col_name and not row.col_name.startswith("#")
            ]
            return "\n".join(lines) if lines else "(table has no columns / does not exist)"
    except Exception as exc:  # noqa: BLE001
        return f"(could not describe {catalog}.{schema}.{table}: {exc})"
