# infra/ -- Terraform for the self-heal agent's AWS resources

Creates: the DynamoDB circuit breaker table, Secrets Manager secret containers,
the EventBridge rule, the dispatcher Lambda, and the ECR repo/ECS cluster/Fargate
task definition/IAM roles the agent runs under. See `../agent/README.md` for the
full one-time setup runbook (GitHub App, Bedrock model access, secret values, etc.)
-- this file is just about the Terraform mechanics.

## One-time bootstrap: remote state

`versions.tf` declares a partial `backend "s3" {}` -- required because
`.github/workflows/deploy-agent.yml` runs `terraform apply` from a fresh GitHub
Actions runner every time, which has no memory of a previous run's local state.
Create the state bucket + lock table once, by hand, before the first `terraform init`
(chicken-and-egg: Terraform can't manage the bucket it stores its own state in):

```powershell
aws s3api create-bucket --bucket <your-unique-tfstate-bucket-name> --region <region>
aws s3api put-bucket-versioning --bucket <your-unique-tfstate-bucket-name> --versioning-configuration Status=Enabled
aws dynamodb create-table --table-name <your-tfstate-lock-table-name> `
  --attribute-definitions AttributeName=LockID,AttributeType=S `
  --key-schema AttributeName=LockID,KeyType=HASH `
  --billing-mode PAY_PER_REQUEST
```

Then:

```powershell
terraform init `
  -backend-config="bucket=<your-unique-tfstate-bucket-name>" `
  -backend-config="key=dbt-self-heal/terraform.tfstate" `
  -backend-config="region=<region>" `
  -backend-config="dynamodb_table=<your-tfstate-lock-table-name>"
```

Wire the same four values into the repo's GitHub Actions variables
(`TF_STATE_BUCKET`, `TF_STATE_KEY`, `TF_STATE_REGION`, `TF_STATE_LOCK_TABLE`) so
`deploy-agent.yml`'s `terraform init` step can pass them as `-backend-config` flags too.

`deploy-agent.yml`'s `terraform apply` step also needs every non-default value from
`variables.tf` (mirrors `terraform.tfvars`, which is git-ignored) as repo variables --
none of these are secret, the real credentials live in Secrets Manager instead:

| Repo variable | Example value |
| --- | --- |
| `AWS_REGION` | `us-east-1` (likely already set for `deploy-mwaa.yml`) |
| `MWAA_ENV_NAME` | `prod-airflow` (likely already set) |
| `AGENT_VPC_ID` | `vpc-xxxxxxxx` |
| `AGENT_SUBNET_IDS` | `["subnet-xxxx","subnet-yyyy"]` -- literal HCL list string |
| `AGENT_SECURITY_GROUP_IDS` | `["sg-xxxx","sg-yyyy"]` -- literal HCL list string |
| `DATABRICKS_HOST` | `dbc-xxxxxxxx-xxxx.cloud.databricks.com` |
| `DATABRICKS_HTTP_PATH` | `/sql/1.0/warehouses/xxxxxxxxxxxxxxxx` |
| `DATABRICKS_CATALOG` | `ai_project` |
| `DBT_DATABRICKS_CI_SCHEMA` | `agent_ci` |
| `DBT_DATABRICKS_CI_CLIENT_ID` | the `agent_ci*` service principal's Application id (see `../agent/README.md` step 3) |
| `SELF_HEAL_GITHUB_APP_ID` | the self-heal GitHub App's App id (can't be named `GITHUB_*` -- GitHub reserves that prefix for repo variables) |
| `AGENT_ECR_REPOSITORY` | `terraform output ecr_repository_url` (after the very first manual apply) |

Also create a GitHub Actions **environment** named `agent-deploy` (Settings -> Environments)
with a required reviewer, so the first-ever deploy needs a manual approval click.

## Day-to-day

```powershell
cp terraform.tfvars.example terraform.tfvars   # fill in your values
terraform plan
terraform apply
```

After the first `apply`, populate the Secrets Manager secret values (`terraform output
secrets_to_populate` lists the names) -- see `../agent/README.md` step 5.
