# Self-heal agent

A LangGraph agent that runs once per `dbt_silver`/`dbt_gold` task failure: reads the
Airflow log, classifies the dbt error, patches the model in an isolated clone,
validates the fix against the `ci` Databricks target, opens a PR, and auto-merges
only low-risk/verified changes. See the architecture plan for the full design;
this file is the practical "how do I actually turn this on" runbook.

## Code map

- `state.py` -- the `SelfHealState` shape passed between every node.
- `graph.py` -- wires `nodes/*` into the LangGraph `StateGraph`.
- `nodes/` -- one function per graph node; each takes `SelfHealState`, returns a partial update.
- `tools/` -- everything that talks to the outside world (MWAA, Databricks, GitHub, Slack, Bedrock, the repo clone). Nodes call these; tests can mock these.
- `main.py` -- container entrypoint, reads `FAILURE_*` env vars, runs the graph once, exits.
- `config.py` -- every tunable, all env-var driven.

## One-time setup (do these before the agent's first real run)

1. **Enable Bedrock model access.** Bedrock console -> Model access -> request/enable
   the model in `var.bedrock_model_id` (default Claude Sonnet). One-time per account/region.

2. **Create the GitHub App.** GitHub -> Settings -> Developer settings -> GitHub Apps -> New GitHub App.
   - Permissions: Contents (read/write), Pull requests (read/write), Checks (read).
   - Install it on `girishahb/dbt_ai_project` only.
   - Generate a private key (downloads a `.pem`) and note the App ID and Installation ID
     (Installation ID is in the URL when you view the installation, or via
     `GET /app/installations` with a JWT).

3. **Create a scoped Databricks service principal for CI.** The `ci` target
   (`profiles/profiles.yml`) authenticates via OAuth M2M (client id/secret), never a
   shared PAT, and only ever needs privileges on schemas matching `agent_ci*` in the
   configured catalog.

   - Databricks admin console -> Identity and access -> Service principals -> Add
     service principal (e.g. `dbt-self-heal-ci`). Note its **Application id** (client id).
   - Generate an OAuth secret for it (Secrets tab -> Generate secret) -- shown once, save it.
   - Grant it warehouse access: the SQL warehouse's Permissions tab -> add the service
     principal with **Can use**.
   - As a workspace admin, run once via a SQL warehouse (adjust catalog name and
     the application id):

     ```sql
     CREATE SCHEMA IF NOT EXISTS ai_project.agent_ci_silver;
     CREATE SCHEMA IF NOT EXISTS ai_project.agent_ci_gold;
     GRANT USE CATALOG ON CATALOG ai_project TO `<service-principal-application-id>`;
     GRANT CREATE SCHEMA, USE SCHEMA ON CATALOG ai_project TO `<service-principal-application-id>`;
     GRANT ALL PRIVILEGES ON SCHEMA ai_project.agent_ci_silver TO `<service-principal-application-id>`;
     GRANT ALL PRIVILEGES ON SCHEMA ai_project.agent_ci_gold TO `<service-principal-application-id>`;
     GRANT USE SCHEMA, SELECT ON SCHEMA ai_project.<bronze_schema> TO `<service-principal-application-id>`;
     ```

     The last grant is read-only access to whatever schema the bronze sources
     (`models/sources.yml`) actually live in, so the `ci` target can still resolve
     upstream data via `source()`. It should have **no** grants on the real
     `silver`/`gold` schemas themselves.

4. **Create a Slack incoming webhook** (or swap `tools/slack.py` for another notifier).

5. **Populate Secrets Manager** (after `terraform apply` has created the secret containers --
   see infra/secrets.tf):

   ```powershell
   aws secretsmanager put-secret-value --secret-id dbt-self-heal/github-app-private-key --secret-string (Get-Content -Raw path\to\key.pem)
   aws secretsmanager put-secret-value --secret-id dbt-self-heal/github-app-installation-id --secret-string "<installation-id>"
   aws secretsmanager put-secret-value --secret-id dbt-self-heal/databricks-ci-client-secret --secret-string "<oauth-secret>"
   aws secretsmanager put-secret-value --secret-id dbt-self-heal/slack-webhook-url --secret-string "<webhook-url>"
   ```

   The service principal's **client id** is not a secret -- it goes in
   `terraform.tfvars` as `databricks_ci_client_id` (and from there into the ECS task
   definition as a plain env var), not into Secrets Manager.

6. **Branch protection on `main`:** GitHub repo Settings -> Branches -> add a rule for
   `main` requiring the `dbt-build` status check (from `.github/workflows/dbt_ci.yml`)
   to pass before merging. This is the hard backstop described in the plan -- even a bug
   in the agent's own risk_gate can't get past it.

7. **Deploy the infra:** `cd infra && terraform init && terraform apply` (with
   `terraform.tfvars` filled in from `terraform.tfvars.example`), then build+push the
   agent image (see `.github/workflows/deploy-agent.yml`, or manually:
   `docker build -f agent/Dockerfile -t <ecr-repo-url>:latest .` then `docker push`).

## Local testing (no AWS infra required)

Everything under `tools/` talks to a real external system, so unit-testing the graph
logic itself means running individual nodes directly against fixtures rather than the
whole `main.py` entrypoint. Two useful entry points:

- Test `tools/dbt_runner.py` / the `ci` target for real (no AWS needed, just the
  Databricks creds already in your local `.env`):

  ```powershell
  dbt build --project-dir . --profiles-dir .\profiles --target ci
  ```

- Exercise the fix-loop nodes (`propose_fix`/`apply_fix`/`validate_fix`) against a real
  clone and the real `ci` schema without needing AWS/GitHub/Bedrock credentials by
  stubbing `tools.bedrock.get_model` and `tools.github_client` -- see the pattern used
  for the manual dry run described in the top-level plan's Phase 3 test notes.

## Guardrails cheat sheet

| Guardrail | Where enforced |
|---|---|
| Never write outside `models/` | `tools/repo_tools.py::_resolve_and_check` (code, not prompt) |
| Never run against prod Databricks | `macros/get_custom_schema.sql` (schema isolation) + separate `databricks_ci_client_secret` service principal |
| Bounded fix retries | `graph.py::route_validation` + `config.MAX_FIX_RETRIES` |
| Auto-merge only for small, known-safe diffs | `nodes/risk_gate.py` + `config.LOW_RISK_*` |
| Required CI check is a hard backstop | `nodes/open_pr_and_merge.py` calls GitHub, doesn't trust its own risk_gate alone |
| One attempt per dag+task per day | `dispatcher/handler.py` circuit breaker (DynamoDB) |
| Never leave `main` broken | `nodes/revert_and_escalate.py` |
