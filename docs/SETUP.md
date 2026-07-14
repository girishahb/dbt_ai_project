# Setup: building this from zero

This is a complete, copy-pasteable runbook for standing up the whole
self-healing dbt pipeline described in [`ARCHITECTURE.md`](./ARCHITECTURE.md)
in a fresh AWS account + Databricks workspace + GitHub repo. Follow the
phases in order — later phases depend on IDs/secrets created in earlier ones.

Commands are given in **PowerShell** (this project was built on Windows) —
the equivalent bash is almost always the same command with `$env:X`/backtick
line-continuations swapped for `export X`/`\`. Every `aws`/`gh`/`terraform`
call below is the real command, not pseudocode.

> **A note on file encoding (read this before you paste JSON policy documents
> on Windows).** PowerShell's `>` redirect operator defaults to UTF-16. AWS
> CLI's `--policy-document file://...` rejects anything that isn't plain
> ASCII/UTF-8 with a cryptic `must contain only printable ASCII characters`
> error. Always write JSON files with `Set-Content -Encoding utf8` (or a
> plain text editor), never `command > file.json`. See
> [`OPERATIONS.md`](./OPERATIONS.md#gotcha-powershell-utf-16-json-files).

## Prerequisites

| Tool | Check | Install |
| --- | --- | --- |
| Python 3.11+ | `python --version` | [python.org](https://www.python.org/downloads/) |
| AWS CLI v2 | `aws --version` | `winget install Amazon.AWSCLI` |
| GitHub CLI | `gh --version` | `winget install GitHub.cli` |
| Terraform >= 1.5 | `terraform --version` | `winget install Hashicorp.Terraform` |
| Docker | `docker --version` | Docker Desktop |
| Git | `git --version` | `winget install Git.Git` |

You'll also need:

- An AWS account with permission to create IAM roles, and an existing
  **Amazon MWAA environment** running your dbt DAGs (or willingness to stand
  one up — out of scope here, see `mwaa/README` equivalents elsewhere).
- A **Databricks workspace** with Unity Catalog and a SQL Warehouse, and
  workspace admin access (to create service principals and grant permissions).
- A **GitHub repo** containing your dbt project, and org/repo admin access
  (to create a GitHub App and branch protection rules).
- `gh auth login` and `aws configure` (or `aws sso login`) already done.

```powershell
gh auth status
aws sts get-caller-identity
```

---

## Phase 0 — the dbt project + medallion models

If you already have a dbt project targeting Databricks, skip to Phase 1. If
starting from scratch:

```powershell
mkdir my_dbt_project; cd my_dbt_project
python -m pip install dbt-core dbt-databricks
dbt init my_dbt_project
```

Structure your models as bronze (sources only, declared in `models/sources.yml`,
nothing built) → silver (`models/silver/`, cleaning/validation) → gold
(`models/gold/`, marts) — see `models/` in this repo for a worked example.
Use a custom-schema macro so `+schema: silver` / `+schema: gold` land in
`<catalog>.silver` / `<catalog>.gold` directly instead of dbt's default
`<target_schema>_<custom_schema>` naming:

```sql
-- macros/get_custom_schema.sql
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
```

Set up your local `profiles/profiles.yml` with `dev`/`prod` targets reading
Databricks credentials from environment variables (never hardcode secrets in
the file — see this repo's `profiles/profiles.yml` for the exact pattern,
and `.env.example` for the variable names).

---

## Phase 1 — CI safety net (do this *before* the agent exists)

The agent's entire safety model rests on one thing existing first: an
isolated Databricks target the PR-time CI and the agent both validate
against, that can never touch production data.

### 1.1 Add a `ci` target to `profiles/profiles.yml`

```yaml
ci:
  type: databricks
  catalog: "{{ env_var('DBT_DATABRICKS_CATALOG', 'default') }}"
  schema: "{{ env_var('DBT_DATABRICKS_CI_SCHEMA', 'agent_ci') }}"
  host: "{{ env_var('DBT_DATABRICKS_HOST') }}"
  http_path: "{{ env_var('DBT_DATABRICKS_HTTP_PATH') }}"
  auth_type: oauth
  client_id: "{{ env_var('DBT_DATABRICKS_CI_CLIENT_ID', '') }}"
  client_secret: "{{ env_var('DBT_DATABRICKS_CI_CLIENT_SECRET', '') }}"
  threads: 4
```

We'll populate the OAuth client id/secret in Phase 3 (they belong to a
service principal that doesn't exist yet).

### 1.2 Add the `dbt_ci.yml` GitHub Actions workflow

```yaml
# .github/workflows/dbt_ci.yml
name: dbt CI

on:
  pull_request:
    branches: [main]
    paths:
      - "dags/**"
      - "models/**"
      - "macros/**"
      - "seeds/**"
      - "snapshots/**"
      - "analyses/**"
      - "tests/**"
      - "profiles/**"
      - "dbt_project.yml"

concurrency:
  group: dbt-ci-${{ github.event.pull_request.number }}
  cancel-in-progress: true

jobs:
  dbt-build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: python -m pip install --no-cache-dir -r requirements.txt
      - name: dbt build (against isolated ci schema)
        env:
          DBT_DATABRICKS_HOST: ${{ secrets.DBT_DATABRICKS_HOST }}
          DBT_DATABRICKS_HTTP_PATH: ${{ secrets.DBT_DATABRICKS_HTTP_PATH }}
          DBT_DATABRICKS_CI_CLIENT_ID: ${{ vars.DBT_DATABRICKS_CI_CLIENT_ID }}
          DBT_DATABRICKS_CI_CLIENT_SECRET: ${{ secrets.DBT_DATABRICKS_CI_CLIENT_SECRET }}
          DBT_DATABRICKS_CATALOG: ${{ vars.DBT_DATABRICKS_CATALOG }}
          DBT_DATABRICKS_CI_SCHEMA: ${{ vars.DBT_DATABRICKS_CI_SCHEMA }}
        run: dbt build --project-dir . --profiles-dir ./profiles --target ci
```

The check-run name this produces is **`dbt-build`** (shown in the GitHub UI
as `dbt CI / dbt-build`) — write this exact string down, it's needed twice
more: once in branch protection (below) and once as `REQUIRED_CHECK_NAME`
that the agent polls for.

### 1.3 Branch protection

```powershell
gh api repos/<owner>/<repo>/branches/main/protection -X PUT `
  --input - <<'JSON'
{
  "required_status_checks": { "strict": true, "contexts": ["dbt-build"] },
  "enforce_admins": false,
  "required_pull_request_reviews": null,
  "restrictions": null
}
JSON
```

(PowerShell heredocs to a pipe are finicky — it's usually more reliable to
do this once via the GitHub UI: **Settings → Branches → Add rule** for
`main`, require status check `dbt-build`.)

---

## Phase 2 — Airflow/MWAA wiring

### 2.1 Add the failure callback

Add an `on_failure_callback` to every dbt task that publishes an
EventBridge event on failure (see `dags/dbt_common.py` in this repo for the
full, defensive implementation — it must never raise, and no-ops if
`boto3`/AWS creds aren't available so local runs are unaffected):

```python
def notify_self_heal_agent(context: dict) -> None:
    try:
        import boto3
        task_instance = context["task_instance"]
        events = boto3.client("events", region_name=os.environ.get("AWS_REGION"))
        events.put_events(Entries=[{
            "Source": "airflow.dbt",
            "DetailType": "DbtTaskFailed",
            "Detail": json.dumps({
                "dag_id": context["dag"].dag_id,
                "task_id": task_instance.task_id,
                "run_id": context["run_id"],
                "try_number": task_instance.try_number,
                "log_url": task_instance.log_url,
            }),
        }])
    except Exception as exc:
        print(f"notify_self_heal_agent: could not publish EventBridge event: {exc}")

DEFAULT_DBT_ARGS = {"owner": "data-engineering", "retries": 1, "on_failure_callback": notify_self_heal_agent}
```

Use `DEFAULT_DBT_ARGS` as `default_args` on every dbt-running DAG.

### 2.2 IAM: let MWAA workers publish EventBridge events

MWAA's execution role needs `events:PutEvents` on the default bus. Find the
role and attach it:

```powershell
$mwaaEnv = "<your-mwaa-environment-name>"
$execRoleArn = (aws mwaa get-environment --name $mwaaEnv --query "Environment.ExecutionRoleArn" --output text)
$roleName = ($execRoleArn -split "/")[-1]

aws iam put-role-policy --role-name $roleName --policy-name mwaa-eventbridge-notify --policy-document '{
  "Version": "2012-10-17",
  "Statement": [{ "Effect": "Allow", "Action": "events:PutEvents", "Resource": "*" }]
}'
```

Deploy the DAG changes (sync to the MWAA `dags/` S3 prefix as usual), unpause
the DAGs.

---

## Phase 3 — Databricks CI service principal (OAuth M2M)

The agent (and `dbt_ci.yml`) authenticate as a **dedicated** service
principal scoped only to scratch schemas — never a shared personal access
token, and never anything with access to real `silver`/`gold` data.

### 3.1 Create the service principal

Databricks workspace admin console → **Identity and access → Service
principals → Add service principal**. Name it e.g. `dbt-self-heal-ci`. Note
its **Application id** (this is the OAuth *client id* — not secret).

Generate an OAuth secret for it: the service principal's **Secrets** tab →
**Generate secret**. **Copy it now — it is shown exactly once.**

> If your workspace's admin PAT has `scim`/`access-management` scopes
> enabled, this can be automated via the SCIM API
> (`POST /api/2.0/preview/scim/v2/ServicePrincipals`) and the OAuth secrets
> API. Many workspaces scope tokens more tightly than that by default, in
> which case the console click-through above is the reliable path — that
> was the case building this project.

### 3.2 Grant warehouse access

SQL warehouse → **Permissions** tab → add the service principal with
**Can use**.

### 3.3 Grant Unity Catalog privileges

Run once as a workspace admin (via a SQL warehouse — Databricks SQL editor,
or the API):

```sql
-- Replace ai_project, agent_ci_silver/gold, default, and the application id.
CREATE SCHEMA IF NOT EXISTS ai_project.agent_ci_silver;
CREATE SCHEMA IF NOT EXISTS ai_project.agent_ci_gold;

GRANT USE CATALOG ON CATALOG ai_project TO `<service-principal-application-id>`;
GRANT CREATE SCHEMA, USE SCHEMA ON CATALOG ai_project TO `<service-principal-application-id>`;

-- Full ownership of the scratch schemas dbt actually writes to.
GRANT ALL PRIVILEGES ON SCHEMA ai_project.agent_ci_silver TO `<service-principal-application-id>`;
GRANT ALL PRIVILEGES ON SCHEMA ai_project.agent_ci_gold TO `<service-principal-application-id>`;

-- Read-only on the bronze schema so source() resolves real upstream data.
GRANT USE SCHEMA, SELECT ON SCHEMA ai_project.default TO `<service-principal-application-id>`;
```

**Do not** grant anything on the real `silver`/`gold` schemas. This is the
guarantee that makes "the agent validated its fix in CI" mean something —
there's no code path by which the `ci` target can write to production data,
enforced by Unity Catalog permissions, not by application logic.

> **Common gotcha:** if `agent_ci_silver`/`agent_ci_gold` schemas already
> exist (e.g. created by an admin during manual testing), dbt's
> `CREATE OR REPLACE VIEW`/table needs *ownership*, not just `SELECT`. If you
> hit `PERMISSION_DENIED ... does not have MANAGE on Table`, either
> `DROP SCHEMA ... CASCADE` and let the service principal create it fresh, or
> explicitly `ALTER SCHEMA ... OWNER TO` the service principal.

### 3.4 Wire the client id/secret through

```powershell
# .env (local testing) -- never commit this file
DBT_DATABRICKS_CI_CLIENT_ID=<application-id>
DBT_DATABRICKS_CI_CLIENT_SECRET=<oauth-secret>
```

```powershell
# GitHub repo config for dbt_ci.yml
gh variable set DBT_DATABRICKS_CI_CLIENT_ID --body "<application-id>"
gh secret set DBT_DATABRICKS_CI_CLIENT_SECRET --body "<oauth-secret>"
gh variable set DATABRICKS_CATALOG --body "ai_project"
gh variable set DBT_DATABRICKS_CI_SCHEMA --body "agent_ci"
gh secret set DBT_DATABRICKS_HOST --body "<workspace-hostname>"
gh secret set DBT_DATABRICKS_HTTP_PATH --body "/sql/1.0/warehouses/<warehouse-id>"
```

Verify it end to end before moving on:

```powershell
dbt build --project-dir . --profiles-dir .\profiles --target ci
```

---

## Phase 4 — GitHub App for the agent

The agent needs its own GitHub identity — not your personal token, not the
default `GITHUB_TOKEN` (which can't trigger other workflows or bypass
certain protections cleanly) — scoped to exactly one repo.

1. **GitHub → Settings → Developer settings → GitHub Apps → New GitHub App.**
   - Webhook: uncheck **Active** (the agent doesn't need webhooks, it calls
     the GitHub API directly).
   - Repository permissions: **Contents** (read/write), **Pull requests**
     (read/write), **Checks** (read).
   - Where can this GitHub App be installed: **Only on this account**.
2. **Create GitHub App.** Note the **App ID** shown on its settings page (not
   secret — it's visible on the public settings page).
3. **Generate a private key** (downloads a `.pem` — this *is* secret, treat
   it like any other credential).
4. **Install the app** on your repo: the app's settings page → **Install
   App** → select the repo. Note the **installation id** — either from the
   URL (`.../settings/installations/<id>`) or:

   ```powershell
   # Requires a short-lived JWT signed with the private key -- PyJWT example:
   python -c "
   import jwt, time
   with open('path/to/key.pem') as f: private_key = f.read()
   now = int(time.time())
   token = jwt.encode({'iat': now - 60, 'exp': now + 300, 'iss': '<app-id>'}, private_key, algorithm='RS256')
   print(token)
   "
   $jwt = "<paste token>"
   curl.exe -H "Authorization: Bearer $jwt" -H "Accept: application/vnd.github+json" https://api.github.com/app/installations
   ```

Keep the App ID, private key `.pem`, and installation id handy — they go
into Terraform vars / Secrets Manager in Phase 6.

---

## Phase 5 — Slack notifications (optional but recommended)

Slack → your workspace → **Incoming Webhooks** app → create a webhook for
the channel you want alerts in. Copy the URL (`https://hooks.slack.com/services/...`).

---

## Phase 6 — AWS foundations

### 6.1 OIDC role for GitHub Actions (no static AWS keys in GitHub)

If you already have an OIDC provider + role for other workflows (e.g.
`deploy-mwaa.yml`), reuse it and skip to 6.2 — just make sure its trust
policy and inline policy are extended per below.

```powershell
# One-time per AWS account: register GitHub's OIDC provider.
aws iam create-open-id-connect-provider `
  --url https://token.actions.githubusercontent.com `
  --client-id-list sts.amazonaws.com `
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1
```

```powershell
$accountId = (aws sts get-caller-identity --query Account --output text)
$owner = "<github-org-or-user>"
$repo = "<repo-name>"
```

Write the trust policy to a file **with UTF-8 encoding** (see the encoding
warning at the top of this doc):

```powershell
@'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Federated": "arn:aws:iam::ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com" },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": { "token.actions.githubusercontent.com:aud": "sts.amazonaws.com" },
      "StringLike": {
        "token.actions.githubusercontent.com:sub": [
          "repo:OWNER/REPO:ref:refs/heads/main",
          "repo:OWNER/REPO:environment:agent-deploy"
        ]
      }
    }
  }]
}
'@ -replace "ACCOUNT_ID", $accountId -replace "OWNER/REPO", "$owner/$repo" |
  Set-Content -Encoding utf8 trust_policy.json

aws iam create-role --role-name GithubActions-SelfHeal --assume-role-policy-document file://trust_policy.json
```

> **Why both `ref:refs/heads/main` and `environment:agent-deploy` in the
> trust policy:** a job that runs under a GitHub **environment** (which
> `deploy-agent.yml`'s manual-approval gate requires) presents a different
> `sub` claim than a plain push-to-main job. Missing this is a common cause
> of `Not authorized to perform sts:AssumeRoleWithWebIdentity` — see
> [`OPERATIONS.md`](./OPERATIONS.md#error-not-authorized-to-perform-stsassumerolewithwebidentity).

### 6.2 Inline permissions policy for that role

This role needs to manage exactly the resources Terraform will create,
nothing broader. Write this with `Set-Content -Encoding utf8`, then:

```powershell
aws iam put-role-policy --role-name GithubActions-SelfHeal --policy-name AgentDeployPolicy --policy-document file://agent_deploy_policy.json
```

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Sid": "EcrAuth", "Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*" },
    { "Sid": "EcrRepo", "Effect": "Allow", "Action": [
        "ecr:CreateRepository", "ecr:DescribeRepositories", "ecr:DeleteRepository",
        "ecr:PutImageScanningConfiguration", "ecr:SetRepositoryPolicy", "ecr:GetRepositoryPolicy",
        "ecr:TagResource", "ecr:ListTagsForResource", "ecr:BatchCheckLayerAvailability",
        "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage", "ecr:PutImage", "ecr:InitiateLayerUpload",
        "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:DescribeImages", "ecr:ListImages"
      ], "Resource": "arn:aws:ecr:*:ACCOUNT_ID:repository/dbt-self-heal-*" },
    { "Sid": "EcsManage", "Effect": "Allow", "Action": [
        "ecs:CreateCluster", "ecs:DeleteCluster", "ecs:DescribeClusters", "ecs:RegisterTaskDefinition",
        "ecs:DeregisterTaskDefinition", "ecs:DescribeTaskDefinition", "ecs:ListTagsForResource",
        "ecs:TagResource", "ecs:UntagResource"
      ], "Resource": "*" },
    { "Sid": "LambdaManage", "Effect": "Allow", "Action": [
        "lambda:CreateFunction", "lambda:DeleteFunction", "lambda:GetFunction",
        "lambda:GetFunctionCodeSigningConfig", "lambda:UpdateFunctionCode", "lambda:UpdateFunctionConfiguration",
        "lambda:AddPermission", "lambda:RemovePermission", "lambda:GetPolicy", "lambda:ListVersionsByFunction",
        "lambda:TagResource", "lambda:UntagResource", "lambda:ListTags"
      ], "Resource": "arn:aws:lambda:*:ACCOUNT_ID:function:dbt-self-heal-*" },
    { "Sid": "DynamoDbManage", "Effect": "Allow", "Action": [
        "dynamodb:CreateTable", "dynamodb:DeleteTable", "dynamodb:DescribeTable", "dynamodb:DescribeTimeToLive",
        "dynamodb:UpdateTimeToLive", "dynamodb:DescribeContinuousBackups", "dynamodb:ListTagsOfResource",
        "dynamodb:TagResource", "dynamodb:UntagResource", "dynamodb:GetItem", "dynamodb:PutItem"
      ], "Resource": "arn:aws:dynamodb:*:ACCOUNT_ID:table/dbt-self-heal-*" },
    { "Sid": "SecretsManage", "Effect": "Allow", "Action": [
        "secretsmanager:CreateSecret", "secretsmanager:DeleteSecret", "secretsmanager:DescribeSecret",
        "secretsmanager:GetResourcePolicy", "secretsmanager:PutResourcePolicy", "secretsmanager:TagResource",
        "secretsmanager:UntagResource", "secretsmanager:ListSecrets"
      ], "Resource": "arn:aws:secretsmanager:*:ACCOUNT_ID:secret:dbt-self-heal/*" },
    { "Sid": "EventBridgeManage", "Effect": "Allow", "Action": [
        "events:PutRule", "events:DeleteRule", "events:DescribeRule", "events:PutTargets",
        "events:RemoveTargets", "events:ListTargetsByRule", "events:ListTagsForResource",
        "events:TagResource", "events:UntagResource"
      ], "Resource": "arn:aws:events:*:ACCOUNT_ID:rule/dbt-self-heal-*" },
    { "Sid": "IamManageAgentRoles", "Effect": "Allow", "Action": [
        "iam:CreateRole", "iam:DeleteRole", "iam:GetRole", "iam:PutRolePolicy", "iam:DeleteRolePolicy",
        "iam:GetRolePolicy", "iam:AttachRolePolicy", "iam:DetachRolePolicy", "iam:ListRolePolicies",
        "iam:ListAttachedRolePolicies", "iam:ListInstanceProfilesForRole", "iam:TagRole", "iam:UntagRole"
      ], "Resource": "arn:aws:iam::ACCOUNT_ID:role/dbt-self-heal-*" },
    { "Sid": "IamPassAgentRoles", "Effect": "Allow", "Action": "iam:PassRole", "Resource": "arn:aws:iam::ACCOUNT_ID:role/dbt-self-heal-*" },
    { "Sid": "LogsDescribeAll", "Effect": "Allow", "Action": "logs:DescribeLogGroups", "Resource": "*" },
    { "Sid": "LogsManage", "Effect": "Allow", "Action": [
        "logs:CreateLogGroup", "logs:DeleteLogGroup", "logs:PutRetentionPolicy", "logs:TagResource", "logs:ListTagsForResource"
      ], "Resource": [
        "arn:aws:logs:*:ACCOUNT_ID:log-group:/ecs/dbt-self-heal-*",
        "arn:aws:logs:*:ACCOUNT_ID:log-group:/aws/lambda/dbt-self-heal-*"
      ] },
    { "Sid": "TerraformStateS3", "Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
      "Resource": ["arn:aws:s3:::<your-tfstate-bucket>", "arn:aws:s3:::<your-tfstate-bucket>/*"] },
    { "Sid": "TerraformStateLock", "Effect": "Allow", "Action": ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem"],
      "Resource": "arn:aws:dynamodb:*:ACCOUNT_ID:table/<your-tfstate-lock-table>" }
  ]
}
```

> `logs:DescribeLogGroups` needs `Resource: "*"` — it's a *list* action, AWS
> evaluates it against a synthetic "no group specified" ARN shape that a
> scoped `log-group:/ecs/foo-*` resource never matches. See
> [`OPERATIONS.md`](./OPERATIONS.md#error-accessdeniedexception-on-logsdescribeloggroups).

### 6.3 Terraform remote state backend (one-time, by hand)

Terraform can't manage the bucket it stores its own state in — create these
once, outside Terraform:

```powershell
$region = "us-east-1"
$accountId = (aws sts get-caller-identity --query Account --output text)
$stateBucket = "dbt-self-heal-tfstate-$accountId"
$lockTable = "dbt-self-heal-tfstate-lock"

aws s3api create-bucket --bucket $stateBucket --region $region
aws s3api put-bucket-versioning --bucket $stateBucket --versioning-configuration Status=Enabled
aws dynamodb create-table --table-name $lockTable `
  --attribute-definitions AttributeName=LockID,AttributeType=S `
  --key-schema AttributeName=LockID,KeyType=HASH `
  --billing-mode PAY_PER_REQUEST
```

Add the matching S3/DynamoDB ARNs to the `AgentDeployPolicy` above if you
haven't already (they're templated in as `<your-tfstate-bucket>` /
`<your-tfstate-lock-table>`).

### 6.4 Enable Bedrock model access

Bedrock console → **Model access** → request/enable **Claude Sonnet 4.5**
(Anthropic) in your region. This is a one-time, per-account/region grant.

> **Important:** Claude Sonnet 4.5 does not support direct on-demand
> `InvokeModel` with the bare model id — it must be invoked through a
> **cross-region inference profile**. Verify and grab the exact profile id:
>
> ```powershell
> aws bedrock list-inference-profiles --region us-east-1 `
>   --query "inferenceProfileSummaries[?contains(inferenceProfileId,'claude-sonnet-4-5')].inferenceProfileId" `
>   --output table
> ```
>
> Use the `us.anthropic.claude-sonnet-4-5-...` id (not the bare
> `anthropic.claude-sonnet-4-5-...` id) as `bedrock_model_id` — see
> [`OPERATIONS.md`](./OPERATIONS.md#error-invocation-of-model-id--with-on-demand-throughput-isnt-supported).
> Sanity check it actually works:
>
> ```powershell
> aws bedrock-runtime invoke-model --region us-east-1 `
>   --model-id "us.anthropic.claude-sonnet-4-5-20250929-v1:0" `
>   --body '{\"anthropic_version\":\"bedrock-2023-05-31\",\"max_tokens\":10,\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}' `
>   --cli-binary-format raw-in-base64-out out.json
> Get-Content out.json
> ```

---

## Phase 7 — build the agent + infra code

This is the actual application code — see [`ARCHITECTURE.md`](./ARCHITECTURE.md)
for what each piece does. In this repo it already exists under `agent/`,
`dispatcher/`, and `infra/`; if porting to a new project, copy those three
directories wholesale (they're self-contained modulo the repo-specific
constants in `agent/config.py`'s defaults) and adjust:

- `agent/config.py` — `GITHUB_OWNER`/`GITHUB_REPO` defaults.
- `infra/variables.tf` — `github_owner`/`github_repo` defaults.
- `dags/dbt_common.py` — nothing repo-specific, just the callback.

Key files, for reference:

| File | Purpose |
| --- | --- |
| `agent/state.py` | `SelfHealState` TypedDict passed between every graph node |
| `agent/graph.py` | Wires `agent/nodes/*` into the LangGraph `StateGraph` |
| `agent/nodes/*.py` | One function per node (see the table in `ARCHITECTURE.md`) |
| `agent/tools/*.py` | Everything that talks to MWAA, Databricks, GitHub, Slack, Bedrock, the repo clone |
| `agent/main.py` | Container entrypoint — reads `FAILURE_*` env, runs the graph once, exits |
| `agent/Dockerfile` | `python:3.11-slim` + `git` + `agent/requirements.txt` |
| `dispatcher/handler.py` | Lambda: circuit breaker + `ecs.RunTask` |
| `infra/*.tf` | DynamoDB, Secrets Manager containers, EventBridge, Lambda, ECR/ECS/IAM |

---

## Phase 8 — deploy the infrastructure

### 8.1 `terraform.tfvars`

```powershell
cd infra
Copy-Item terraform.tfvars.example terraform.tfvars
notepad terraform.tfvars   # fill in vpc_id, subnet_ids, security_group_ids,
                            # databricks_host/http_path/catalog/ci_schema/ci_client_id,
                            # github_app_id (from Phase 4), mwaa_environment_name
```

`vpc_id`/`subnet_ids`/`security_group_ids` should normally be the **same
VPC as your MWAA environment**, so the agent's Fargate task reaches
Databricks/GitHub/Bedrock the same way MWAA already does:

```powershell
aws mwaa get-environment --name <mwaa-environment-name> --query "Environment.NetworkConfiguration"
```

### 8.2 First apply (from your machine, before wiring CI/CD)

```powershell
terraform init `
  -backend-config="bucket=$stateBucket" `
  -backend-config="key=dbt-self-heal/terraform.tfstate" `
  -backend-config="region=$region" `
  -backend-config="dynamodb_table=$lockTable"

terraform plan
terraform apply
```

This creates the DynamoDB table, empty Secrets Manager containers,
EventBridge rule, dispatcher Lambda, ECR repo, ECS cluster + task definition,
and all IAM roles — but the ECS task definition points at an image that
doesn't exist in ECR yet (`:latest`), so don't expect it to run
successfully until Phase 8.3.

### 8.3 Build and push the agent image

```powershell
$ecrUrl = (terraform output -raw ecr_repository_url)
aws ecr get-login-password --region $region | docker login --username AWS --password-stdin ($ecrUrl -split "/")[0]

cd ..
docker build -f agent/Dockerfile -t "${ecrUrl}:latest" .
docker push "${ecrUrl}:latest"
```

### 8.4 Populate Secrets Manager

```powershell
aws secretsmanager put-secret-value --secret-id dbt-self-heal/github-app-private-key `
  --secret-string (Get-Content -Raw path\to\your-app-key.pem)
aws secretsmanager put-secret-value --secret-id dbt-self-heal/github-app-installation-id `
  --secret-string "<installation-id-from-phase-4>"
aws secretsmanager put-secret-value --secret-id dbt-self-heal/databricks-ci-client-secret `
  --secret-string "<oauth-secret-from-phase-3>"
aws secretsmanager put-secret-value --secret-id dbt-self-heal/slack-webhook-url `
  --secret-string "<webhook-url-from-phase-5>"
```

(`terraform output secrets_to_populate` lists the exact secret names if you
need a reminder later.)

---

## Phase 9 — wire up CI/CD (deploy-agent.yml)

### 9.1 GitHub Actions environment with manual approval

**Settings → Environments → New environment** → name it `agent-deploy` →
add yourself (or the team) as a **required reviewer**. This is deliberate —
`terraform apply` here can touch IAM/ECS/Lambda, so the very first deploy
(and every deploy after) needs an explicit human click.

### 9.2 Repo variables

```powershell
$netCfg = aws mwaa get-environment --name <mwaa-environment-name> --query "Environment.NetworkConfiguration" | ConvertFrom-Json

gh variable set AWS_REGION --body $region
gh variable set MWAA_ENV_NAME --body "<mwaa-environment-name>"
gh variable set AGENT_VPC_ID --body "<vpc-id>"
# List-valued vars need literal HCL list syntax -- escape inner quotes in PowerShell:
gh variable set AGENT_SUBNET_IDS --body '[\"subnet-aaa\",\"subnet-bbb\"]'
gh variable set AGENT_SECURITY_GROUP_IDS --body '[\"sg-aaa\",\"sg-bbb\"]'
gh variable set DATABRICKS_HOST --body "<workspace-hostname>"
gh variable set DATABRICKS_HTTP_PATH --body "/sql/1.0/warehouses/<id>"
gh variable set DATABRICKS_CATALOG --body "ai_project"
gh variable set DBT_DATABRICKS_CI_SCHEMA --body "agent_ci"
gh variable set DBT_DATABRICKS_CI_CLIENT_ID --body "<application-id-from-phase-3>"
gh variable set SELF_HEAL_GITHUB_APP_ID --body "<app-id-from-phase-4>"
gh variable set AGENT_ECR_REPOSITORY --body $ecrUrl
gh variable set TF_STATE_BUCKET --body $stateBucket
gh variable set TF_STATE_KEY --body "dbt-self-heal/terraform.tfstate"
gh variable set TF_STATE_REGION --body $region
gh variable set TF_STATE_LOCK_TABLE --body $lockTable
gh variable set AWS_ROLE_ARN --body "arn:aws:iam::${accountId}:role/GithubActions-SelfHeal"
```

> **`SELF_HEAL_GITHUB_APP_ID`, not `GITHUB_APP_ID`** — GitHub reserves the
> `GITHUB_` prefix for its own repo variables and will reject the name.

### 9.3 `deploy-agent.yml`

```yaml
name: Deploy self-heal agent

on:
  push:
    branches: [main]
    paths: ["agent/**", "dispatcher/**", "infra/**"]

permissions:
  id-token: write
  contents: read

concurrency:
  group: deploy-agent
  cancel-in-progress: false

jobs:
  build-and-push:
    runs-on: ubuntu-latest
    environment: agent-deploy
    outputs:
      image_tag: ${{ steps.meta.outputs.image_tag }}
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with: { role-to-assume: "${{ vars.AWS_ROLE_ARN }}", aws-region: "${{ vars.AWS_REGION }}" }
      - uses: aws-actions/amazon-ecr-login@v2
      - id: meta
        run: echo "image_tag=${{ github.sha }}" >> "$GITHUB_OUTPUT"
      - env:
          ECR_REPOSITORY: ${{ vars.AGENT_ECR_REPOSITORY }}
          IMAGE_TAG: ${{ steps.meta.outputs.image_tag }}
        run: |
          set -euo pipefail
          docker build -f agent/Dockerfile -t "${ECR_REPOSITORY}:${IMAGE_TAG}" -t "${ECR_REPOSITORY}:latest" .
          docker push "${ECR_REPOSITORY}:${IMAGE_TAG}"
          docker push "${ECR_REPOSITORY}:latest"

  terraform-apply:
    needs: build-and-push
    runs-on: ubuntu-latest
    environment: agent-deploy
    defaults: { run: { working-directory: infra } }
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with: { role-to-assume: "${{ vars.AWS_ROLE_ARN }}", aws-region: "${{ vars.AWS_REGION }}" }
      - uses: hashicorp/setup-terraform@v3
      - run: >
          terraform init
          -backend-config="bucket=${{ vars.TF_STATE_BUCKET }}"
          -backend-config="key=${{ vars.TF_STATE_KEY }}"
          -backend-config="region=${{ vars.TF_STATE_REGION }}"
          -backend-config="dynamodb_table=${{ vars.TF_STATE_LOCK_TABLE }}"
      - run: >
          terraform apply -auto-approve
          -var="agent_image_tag=${{ needs.build-and-push.outputs.image_tag }}"
          -var="aws_region=${{ vars.AWS_REGION }}"
          -var="mwaa_environment_name=${{ vars.MWAA_ENV_NAME }}"
          -var="vpc_id=${{ vars.AGENT_VPC_ID }}"
          -var='subnet_ids=${{ vars.AGENT_SUBNET_IDS }}'
          -var='security_group_ids=${{ vars.AGENT_SECURITY_GROUP_IDS }}'
          -var="databricks_host=${{ vars.DATABRICKS_HOST }}"
          -var="databricks_http_path=${{ vars.DATABRICKS_HTTP_PATH }}"
          -var="databricks_catalog=${{ vars.DATABRICKS_CATALOG }}"
          -var="databricks_ci_schema=${{ vars.DBT_DATABRICKS_CI_SCHEMA }}"
          -var="databricks_ci_client_id=${{ vars.DBT_DATABRICKS_CI_CLIENT_ID }}"
          -var="github_app_id=${{ vars.SELF_HEAL_GITHUB_APP_ID }}"
```

> **Why every var is passed as `-var=...` instead of relying on
> `terraform.tfvars`:** that file is git-ignored (it holds real
> infra-specific values you don't want in source control), so a fresh GitHub
> Actions runner never has it. Repo variables are the CI-side equivalent.

### 9.4 Push and approve

```powershell
git add .github/workflows/deploy-agent.yml agent/ dispatcher/ infra/
git commit -m "Add self-healing agent"
git push
```

Open the run in the Actions tab and click **Approve and deploy** on the
`agent-deploy` environment gate. Watch it through — see
[`OPERATIONS.md`](./OPERATIONS.md#troubleshooting-a-failed-deploy-agentyml-run)
for the most common failure modes and exact fixes (all of which were hit and
fixed once each while building this).

---

## Phase 10 — verify

```powershell
aws ecs describe-task-definition --task-definition dbt-self-heal-agent `
  --query "taskDefinition.containerDefinitions[0].image"

aws lambda get-function --function-name dbt-self-heal-dispatcher --query "Configuration.State"

aws events describe-rule --name dbt-self-heal-dbt-task-failed --query "State"
```

Then run the actual end-to-end test — deliberately break a model, push it,
and watch the whole loop fire — in
[`OPERATIONS.md`](./OPERATIONS.md#end-to-end-test-breaking-a-model-on-purpose).

## What's next

- [`OPERATIONS.md`](./OPERATIONS.md) — testing, debugging, secret rotation, tuning the risk gate, troubleshooting.
- [`CONCEPTS.md`](./CONCEPTS.md) — the "why" behind every non-obvious choice above.
