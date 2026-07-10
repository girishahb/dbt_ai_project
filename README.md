# dbt_ai_project

A [dbt](https://www.getdbt.com/) project configured to run against Databricks (Unity Catalog / SQL Warehouse) using [`dbt-databricks`](https://github.com/databricks/dbt-databricks).

## Prerequisites

- Python 3.9+ (installed at `%LocalAppData%\Programs\Python\Python311` on this machine)
- Access to a Databricks workspace, SQL Warehouse (or cluster), and a personal access token

## Setup

1. Install dependencies:

   ```powershell
   python -m pip install -r requirements.txt
   ```

2. Configure your Databricks connection details. Copy `.env.example` to `.env` and fill in your values:

   ```powershell
   Copy-Item .env.example .env
   ```

   Then load them into your shell session:

   ```powershell
   Get-Content .env | ForEach-Object {
       if ($_ -match '^\s*([^#=]+)=(.*)$') {
           [System.Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim())
       }
   }
   ```

   The connection profile lives at `~/.dbt/profiles.yml` (already created) and reads these environment variables:

   - `DBT_DATABRICKS_HOST` — workspace hostname, no `https://` (e.g. `adb-....azuredatabricks.net`)
   - `DBT_DATABRICKS_HTTP_PATH` — SQL Warehouse/cluster HTTP path
   - `DBT_DATABRICKS_TOKEN` — personal access token or service principal token
   - `DBT_DATABRICKS_CATALOG` — Unity Catalog name (optional, defaults to `hive_metastore`)
   - `DBT_DATABRICKS_SCHEMA` — target schema (optional, defaults to `dbt_ai_project_dev`)

   Never commit `.env` or real credentials to git — `.env` is already git-ignored.

3. Verify the connection:

   ```powershell
   dbt debug
   ```

4. Run the example models:

   ```powershell
   dbt run
   dbt test
   ```

## Project structure

This project follows a medallion architecture on top of the Databricks "Bakehouse" sample dataset (`ai_project.default`):

- **Bronze** — raw source tables, declared in `models/sources.yml` (`sales_customers`, `sales_franchises`, `sales_suppliers`, `sales_transactions`, `media_customer_reviews`). `media_gold_reviews_chunked` is declared as a source but intentionally not built on — it's a pre-chunked RAG/vector-search artifact, not raw business data.
- **Silver** (`models/silver/`, views in the `ai_project.silver` schema) — cleaned, deduplicated, and validated:
  - `silver_customers`, `silver_franchises`, `silver_suppliers` — standardized dimensions
  - `silver_transactions` — validated line items; card numbers masked to the last 4 digits, rows flagged if `total_price` doesn't reconcile with `quantity * unit_price`
  - `silver_customer_reviews` — cleaned review text with a star rating parsed out of free text where present, bucketed into Positive/Neutral/Negative/Unrated
- **Gold** (`models/gold/`, tables in the `ai_project.gold` schema) — business marts:
  - `gold_dim_customers`, `gold_dim_suppliers`, `gold_dim_franchises`, `gold_fact_sales` — star schema for BI tools
  - `gold_franchise_performance` — monthly revenue/orders/AOV per franchise plus each franchise's top product
  - `gold_customer_ltv` — lifetime value, recency status (Active/At Risk/Churned), and value segment
  - `gold_product_performance` — chain-wide revenue mix and ranking by product
  - `gold_review_sentiment` — reputation scorecard per franchise from parsed review ratings

- `seeds/`, `snapshots/`, `macros/`, `analyses/`, `tests/` — standard dbt directories
- `macros/get_custom_schema.sql` — makes `+schema: silver` / `+schema: gold` land in `ai_project.silver` / `ai_project.gold` directly, rather than dbt's default `<target_schema>_<custom_schema>` naming
- `dbt_project.yml` — project configuration
- `requirements.txt` — pinned Python dependencies (`dbt-core`, `dbt-databricks`)
- `scripts/explore_schema.py` — ad hoc helper to list tables/columns/samples in the configured Databricks schema (reads credentials from environment variables, no secrets stored)
- `dags/`, `profiles/profiles.yml`, `mwaa/` — Airflow/MWAA orchestration, see below

## Orchestration (Airflow / Amazon MWAA)

Two DAGs in `dags/` run the medallion pipeline:

- **`dbt_silver`** — runs `dbt run --select silver` then `dbt test --select silver` on a daily schedule (`0 6 * * *`), then triggers `dbt_gold`.
- **`dbt_gold`** — runs `dbt run --select gold` then `dbt test --select gold`. It has no schedule of its own (`schedule_interval=None`) — it's only triggered by `dbt_silver`, so gold always builds on fresh silver data instead of racing a fixed clock offset. It can still be triggered manually for backfills.

`dags/dbt_common.py` holds shared config: it resolves `DBT_PROJECT_DIR`/`DBT_PROFILES_DIR` relative to the DAG file's own location (so the same code works locally and once synced to MWAA's `dags/` S3 prefix), and reads Databricks credentials from Airflow Variables so they can be backed by [MWAA's Secrets Manager backend](https://docs.aws.amazon.com/mwaa/latest/userguide/connections-secrets-manager.html) instead of a `.env` file.

**Why a separate `profiles/profiles.yml`:** MWAA workers don't have your local `~/.dbt/profiles.yml`, so a copy that only references `env_var(...)` (no literal secrets — safe to commit) is deployed alongside the DAGs and pointed to via `--profiles-dir`.

**Why a separate dbt virtualenv (`mwaa/startup.sh`):** dbt-core's pinned dependencies (click, jinja2, pydantic, etc.) commonly conflict with the versions MWAA pins for Airflow itself. Rather than fighting that in the environment's `requirements.txt`, `mwaa/startup.sh` creates an isolated virtualenv (`/usr/local/airflow/dbt_venv`) and installs `dbt-core`/`dbt-databricks` there; the DAGs call that venv's `dbt` binary directly.

### Deploying to MWAA

1. Sync this whole repo to your MWAA environment's S3 `dags/` prefix (so `dbt_project.yml`, `models/`, `macros/`, `profiles/` sit alongside `dags/dbt_silver_dag.py` / `dags/dbt_gold_dag.py` — paths are resolved relative to the DAG files, so this layout just works).
2. Upload `mwaa/startup.sh` to S3 and set it as the environment's **Startup script file** (with its S3 object version).
3. Point the environment's **Requirements file** at `mwaa/requirements.txt` (no extra packages needed today beyond what MWAA bundles).
4. In the Airflow UI (Admin > Variables), or via a Secrets Manager-backed secret, set:
   - `dbt_bin_path` = `/usr/local/airflow/dbt_venv/bin/dbt`
   - `dbt_databricks_host`, `dbt_databricks_http_path`, `dbt_databricks_token`, `dbt_databricks_catalog`, `dbt_databricks_schema`
   - Optional overrides: `dbt_project_dir`, `dbt_profiles_dir`, `dbt_target` (defaults to `prod`)
5. Unpause `dbt_silver` (and `dbt_gold`, though it only runs when triggered) in the Airflow UI.

### Testing locally

```powershell
dbt run --project-dir . --profiles-dir .\profiles --select silver --target prod
dbt run --project-dir . --profiles-dir .\profiles --select gold --target prod
```

These are exactly the commands the DAGs run under the hood (both verified against Databricks while building this).

## Useful commands

| Command | Description |
| --- | --- |
| `dbt debug` | Test the Databricks connection |
| `dbt run` | Run all models |
| `dbt test` | Run all tests |
| `dbt build` | Run + test seeds, models, and snapshots |
| `dbt docs generate && dbt docs serve` | Generate and view project documentation |
