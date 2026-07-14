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

## Day-to-day

```powershell
cp terraform.tfvars.example terraform.tfvars   # fill in your values
terraform plan
terraform apply
```

After the first `apply`, populate the Secrets Manager secret values (`terraform output
secrets_to_populate` lists the names) -- see `../agent/README.md` step 5.
