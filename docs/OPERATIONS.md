# Operations runbook

Day-2 operations: testing the full loop, watching a live run, resetting the
circuit breaker, rotating secrets, tuning the risk gate, and fixes for every
error actually hit while building this.

## Table of contents

- [End-to-end test: breaking a model on purpose](#end-to-end-test-breaking-a-model-on-purpose)
- [Watching a run live](#watching-a-run-live)
- [Resetting the circuit breaker](#resetting-the-circuit-breaker)
- [Rotating secrets](#rotating-secrets)
- [Tuning the risk gate](#tuning-the-risk-gate)
- [Manually invoking the agent locally](#manually-invoking-the-agent-locally)
- [Troubleshooting a failed deploy-agent.yml run](#troubleshooting-a-failed-deploy-agentyml-run)
- [Rollback](#rollback)

---

## End-to-end test: breaking a model on purpose

This is the real test — it exercises every hop in the
[architecture diagram](./ARCHITECTURE.md#3-self-healing-system-end-to-end)
with a genuine failure, not a mock.

1. **Introduce a real, mechanical break.** The easiest reliable trigger is a
   `missing_column` error: reference a column in a silver/gold model that
   doesn't exist in its upstream source/model.

   ```sql
   -- models/silver/silver_customers.sql -- add a bogus column reference
   select
       customer_id,
       customer_name,
       this_column_does_not_exist,   -- <- deliberate break
       ...
   from {{ source('bronze', 'sales_customers') }}
   ```

2. **Push straight to `main`** (bypassing the PR flow on purpose, to
   simulate an unreviewed break making it to prod — if your branch
   protection blocks direct pushes, merge a PR with `--admin` instead).
   `deploy-mwaa.yml` syncs it to MWAA within about a minute.

3. **Trigger the DAG** (or wait for its schedule):

   ```powershell
   aws mwaa create-web-login-token --name <mwaa-environment-name>
   # Use the returned token to open the Airflow UI and trigger dbt_silver manually,
   # or use the Airflow REST API directly if you have network access to the webserver.
   ```

4. **Watch it cascade** (see [Watching a run live](#watching-a-run-live)
   below for the exact commands):
   - Airflow task fails → `on_failure_callback` fires.
   - An EventBridge event appears (`dbt-self-heal-dbt-task-failed` rule
     invocation count increments).
   - The dispatcher Lambda claims the circuit breaker and starts a Fargate task.
   - The Fargate task (`dbt-self-heal-agent` in ECS cluster
     `dbt-self-heal-cluster`) fetches the log, classifies it as
     `missing_column`, proposes a revert-like fix, validates against `ci`,
     opens a PR.
   - `dbt_ci.yml`'s `dbt-build` check runs on that PR.
   - If green and low-risk, the agent merges it, waits for `deploy-mwaa.yml`
     to redeploy, and re-triggers `dbt_silver`.
   - Slack gets a notification at each major step (start, PR opened, merged,
     verified/reverted).

5. **Confirm the end state**: PR merged, DAG green on the retrigger, Slack
   shows `fixed_and_verified`.

### Testing the fix-loop nodes without breaking a real DAG

You don't need AWS/GitHub/Bedrock wired up to exercise `propose_fix` /
`apply_fix` / `validate_fix` — just the real `ci` Databricks target:

```powershell
dbt build --project-dir . --profiles-dir .\profiles --target ci
```

To exercise the LLM-driven nodes without spending real Bedrock calls or
touching real GitHub state, stub `agent.tools.bedrock.get_model` and
`agent.tools.github_client` and call the node functions directly against a
real clone — each node in `agent/nodes/` is a plain function
`(SelfHealState) -> dict`, so this needs no special test harness.

---

## Watching a run live

```powershell
# EventBridge rule invocation metrics (did the event even get matched?)
aws cloudwatch get-metric-statistics --namespace AWS/Events `
  --metric-name Invocations --dimensions Name=RuleName,Value=dbt-self-heal-dbt-task-failed `
  --start-time (Get-Date).AddMinutes(-30).ToString("o") --end-time (Get-Date).ToString("o") `
  --period 60 --statistics Sum

# Dispatcher Lambda logs (did it claim the circuit breaker / start a task?)
aws logs tail /aws/lambda/dbt-self-heal-dispatcher --follow

# Which Fargate task is running / ran
aws ecs list-tasks --cluster dbt-self-heal-cluster
aws ecs describe-tasks --cluster dbt-self-heal-cluster --tasks <task-arn>

# The agent's own reasoning + every tool call it made
aws logs tail /ecs/dbt-self-heal-agent --follow
```

The agent's final log line is the full `SelfHealState` (minus the raw log
text) as JSON — `final_status` tells you exactly how the run ended
(`fixed_and_verified`, `pr_open_needs_review`, `escalated_unfixable`,
`escalated_validation_exhausted`, or `escalated_reverted_after_remerge_failure`).

---

## Resetting the circuit breaker

The dispatcher only starts one agent run per `{dag_id}#{task_id}` per UTC
day. To force another run today (e.g. while iterating on a fix during
testing), delete that day's row:

```powershell
$dagId = "dbt_silver"
$taskId = "run_dbt_silver"
$today = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd")

aws dynamodb delete-item --table-name dbt-self-heal-attempts `
  --key "{\`"attempt_key\`":{\`"S\`":\`"$dagId#$taskId#$today\`"}}"
```

Or just wait — rows carry a 48-hour TTL (`CIRCUIT_BREAKER_TTL_HOURS`) and
expire automatically.

To see what's currently claimed:

```powershell
aws dynamodb scan --table-name dbt-self-heal-attempts
```

---

## Rotating secrets

None of the agent's credentials are baked into the container image or
Terraform state — rotate any of them by just writing a new value, no
redeploy needed (the ECS task definition reads Secrets Manager at container
*start* time, so the next agent invocation picks it up automatically):

```powershell
# GitHub App private key (e.g. after regenerating it in the App's settings)
aws secretsmanager put-secret-value --secret-id dbt-self-heal/github-app-private-key `
  --secret-string (Get-Content -Raw path\to\new-key.pem)

# Databricks service principal OAuth secret (e.g. after regenerating it)
aws secretsmanager put-secret-value --secret-id dbt-self-heal/databricks-ci-client-secret `
  --secret-string "<new-oauth-secret>"

# Slack webhook
aws secretsmanager put-secret-value --secret-id dbt-self-heal/slack-webhook-url `
  --secret-string "<new-webhook-url>"
```

The Databricks service principal's **client id** and the GitHub App's
**App ID**/**installation id** aren't secrets by themselves (they're not
usable without the paired secret) — they live as plain Terraform variables /
GitHub Actions variables, not in Secrets Manager. Rotating those means
updating `terraform.tfvars` (locally) or the matching repo variable (CI) and
re-applying.

---

## Tuning the risk gate

`agent/nodes/risk_gate.py` decides `low_risk` (auto-merge after CI passes)
vs. `needs_review` (label + stop) using four independent checks — any one
failing routes to `needs_review`. All thresholds are env vars on the ECS
task definition (`infra/ecs.tf`), sourced from `agent/config.py` defaults:

| Env var | Default | Effect |
| --- | --- | --- |
| `LOW_RISK_MAX_FILES` | `2` | Max files changed in the fix diff |
| `LOW_RISK_MAX_LINES` | `80` | Max total changed lines |
| `LOW_RISK_ERROR_TYPES` | `missing_column,compile_error` | Which `classify_error` outputs are eligible for auto-merge at all |
| `ALLOWED_WRITE_PREFIXES` | `models/` | Path prefixes the agent may ever write to — enforced in code (`tools/repo_tools.py`), not just risk-scored |

To make the agent more conservative (more human review, less auto-merge),
lower `LOW_RISK_MAX_FILES`/`LOW_RISK_MAX_LINES` or shrink
`LOW_RISK_ERROR_TYPES`. To loosen it, raise the thresholds — but remember
the required `dbt-build` check is the real gate regardless of this file;
loosening risk_gate only changes what merges *without a human clicking
approve*, never what's allowed to merge with a failing check.

Apply changes by editing `infra/ecs.tf`'s `environment` block (or, more
simply, add a new Terraform variable following the pattern already used for
`bedrock_model_id`) and running the `deploy-agent.yml` pipeline.

---

## Manually invoking the agent locally

Useful for debugging a specific failure without waiting for the dispatcher:

```powershell
$env:FAILURE_DAG_ID = "dbt_silver"
$env:FAILURE_TASK_ID = "run_dbt_silver"
$env:FAILURE_RUN_ID = "manual__2026-07-14T00:00:00+00:00"
$env:FAILURE_TRY_NUMBER = "1"
$env:FAILURE_LOG_URL = ""
# Plus all the same env vars the ECS task definition sets (GITHUB_*, DBT_DATABRICKS_*, BEDROCK_MODEL_ID, ...)

python -m agent.main
```

Requires local AWS credentials with the same Bedrock/MWAA permissions as
the task role, plus the GitHub App private key / Databricks OAuth secret
available as env vars (fetch them once with `aws secretsmanager
get-secret-value` and export locally — never commit them).

---

## Troubleshooting a failed deploy-agent.yml run

Every one of these was hit once while building this project — the fixes
below are the exact fixes applied, not theoretical.

### Error: `The value cannot be empty or all whitespace` on `backend "s3" {}`

**Cause:** `TF_STATE_BUCKET`/`TF_STATE_KEY`/`TF_STATE_REGION`/`TF_STATE_LOCK_TABLE`
repo variables were never set (or misspelled), so `terraform init
-backend-config="bucket="` passes an empty string.

**Fix:**

```powershell
gh variable set TF_STATE_BUCKET --body "<your-tfstate-bucket>"
gh variable set TF_STATE_KEY --body "dbt-self-heal/terraform.tfstate"
gh variable set TF_STATE_REGION --body "<region>"
gh variable set TF_STATE_LOCK_TABLE --body "<your-tfstate-lock-table>"
```

Then re-run the failed job: `gh run rerun <run-id> --failed`.

### Error: `Not authorized to perform sts:AssumeRoleWithWebIdentity`

**Cause:** the IAM role's OIDC trust policy only allows the `sub` claim for
plain branch pushes (`repo:OWNER/REPO:ref:refs/heads/main`), but the job
runs under a GitHub **environment** (`agent-deploy`'s manual approval gate),
which presents a *different* `sub` claim shape.

**Fix:** widen the trust policy's `StringLike` condition to allow both:

```json
"token.actions.githubusercontent.com:sub": [
  "repo:OWNER/REPO:ref:refs/heads/main",
  "repo:OWNER/REPO:environment:agent-deploy"
]
```

```powershell
aws iam update-assume-role-policy --role-name GithubActions-SelfHeal --policy-document file://trust_policy.json
```

### Error: `not authorized to perform: ecr:GetAuthorizationToken`

**Cause:** the OIDC role has no ECR permissions yet.

**Fix:** attach the `AgentDeployPolicy` from
[`SETUP.md` §6.2](./SETUP.md#62-inline-permissions-policy-for-that-role) (or
just the missing statement if you're incrementally building the policy).

### Error: `AccessDeniedException` on `logs:DescribeLogGroups`

**Cause:** `logs:DescribeLogGroups` is a *list* action — the AWS provider
calls it to check whether a log group already exists before creating it,
and IAM evaluates that call against a synthetic ARN shape
(`log-group::log-stream:`, no group name) that a scoped
`log-group:/ecs/dbt-self-heal-*` resource never matches, no matter how you
write the ARN.

**Fix:** grant it on `Resource: "*"` as its own statement (safe — it's
read-only and only lists names/metadata):

```json
{ "Sid": "LogsDescribeAll", "Effect": "Allow", "Action": "logs:DescribeLogGroups", "Resource": "*" }
```

### Error: `reading Lambda Function ... code signing config ... AccessDeniedException`

**Cause:** the AWS provider always checks a Lambda function's code-signing
config as part of managing it; `lambda:GetFunctionCodeSigningConfig` was
missing from the policy.

**Fix:** add it to the `LambdaManage` statement's `Action` list.

### Error: `must contain only printable ASCII characters` from `aws iam put-role-policy`

**Cause:** the policy JSON file was written with PowerShell's `>` redirect
operator (or a Write that preserved an existing file's encoding), which
defaults to **UTF-16**. AWS CLI's `file://` loader rejects anything that
isn't ASCII/UTF-8.

**Fix:** always write JSON intended for `file://` with explicit UTF-8, never
plain `>`:

```powershell
$json | Set-Content -Encoding utf8 policy.json
```

If a file is already corrupted this way, convert it explicitly (auto-detect
often fails silently and re-writes the same bad encoding):

```powershell
$content = [System.IO.File]::ReadAllText("bad.json", [System.Text.Encoding]::Unicode)
[System.IO.File]::WriteAllText("fixed.json", $content, (New-Object System.Text.UTF8Encoding($false)))
```

Verify with `[System.IO.File]::ReadAllBytes("file.json")[0..5]` — UTF-8 text
starting with `{` should start `123, 13, 10, ...` (`{`, `\r`, `\n`); UTF-16LE
shows a `0` byte after every character (`123, 0, 13, 0, 10, 0, ...`).

### Error: `Invocation of model ID ... with on-demand throughput isn't supported`

**Cause:** Claude Sonnet 4.5 rejects direct `InvokeModel` calls using the
bare model id (`anthropic.claude-sonnet-4-5-...`) — it requires a
**cross-region inference profile** id instead.

**Fix:**

```powershell
aws bedrock list-inference-profiles --region us-east-1 `
  --query "inferenceProfileSummaries[?contains(inferenceProfileId,'claude-sonnet-4-5')].inferenceProfileId"
```

Use the `us.anthropic.claude-sonnet-4-5-...` id as `bedrock_model_id` /
`BEDROCK_MODEL_ID`, and make sure the task role's `bedrock:InvokeModel`
statement includes both the inference-profile ARN and `foundation-model/*`
across regions (cross-region profiles route to whichever region has
capacity):

```json
"Resource": [
  "arn:aws:bedrock:*::foundation-model/*",
  "arn:aws:bedrock:us-east-1:*:inference-profile/*"
]
```

### `gh variable set` with a list value loses its quotes

**Cause:** PowerShell string interpolation strips the inner double quotes
from something like `["subnet-a","subnet-b"]` before it reaches `gh`.

**Fix:** escape the inner quotes explicitly:

```powershell
gh variable set AGENT_SUBNET_IDS --body '[\"subnet-a\",\"subnet-b\"]'
```

### `gh variable set GITHUB_APP_ID` fails: `Variable names must not start with GITHUB_`

**Cause:** GitHub reserves the `GITHUB_` prefix for its own automatic repo
variables.

**Fix:** use a different name throughout (this project uses
`SELF_HEAL_GITHUB_APP_ID`), and make sure `deploy-agent.yml`'s
`terraform apply` step references the same name.

### PR won't merge: `the base branch policy prohibits the merge`

**Cause:** branch protection requires the `dbt-build` check, but the PR
doesn't touch any of the paths `dbt_ci.yml` triggers on (e.g. a pure
`infra/`/`agent/` change) — so the check never runs, and GitHub can't
confirm a required check that never started.

**Fix:** for genuinely infra-only changes unrelated to dbt models, merge
with admin override: `gh pr merge <n> --squash --admin`. For anything that
touches `models/`/`dags/`/etc., let the check run normally.

---

## Rollback

If a merged "fix" turns out to be wrong after the fact (missed by both CI
and the DAG retrigger — e.g. it passed `ci` but produces subtly wrong data
in prod):

```powershell
git log --oneline -10                     # find the merge commit
git revert -m 1 <merge-commit-sha>         # -m 1: revert against the main-branch parent
git push
```

This is exactly what `nodes/revert_and_escalate.py` does automatically when
the DAG retrigger fails outright — the manual version above is for the
rarer case where the retrigger *looked* successful but the output was still
wrong.

To pause the whole system without tearing down infrastructure (e.g. during
an incident, or while investigating a bad auto-fix):

```powershell
# Disable the EventBridge rule -- dispatcher Lambda simply never gets invoked.
aws events disable-rule --name dbt-self-heal-dbt-task-failed
# ...investigate...
aws events enable-rule --name dbt-self-heal-dbt-task-failed
```
