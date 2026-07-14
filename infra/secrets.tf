# Secret *resources* only -- Terraform creates the named container, but
# never writes a value into it. Secret values are populated once, out of
# band, by whoever holds them (see agent/README.md for the exact `aws
# secretsmanager put-secret-value` command for each). Keeping the value out
# of Terraform means it never lands in a .tf file or in state as plaintext,
# and re-running `terraform apply` can never accidentally overwrite a
# rotated secret with a stale placeholder.

resource "aws_secretsmanager_secret" "github_app_private_key" {
  name        = "${var.project_name}/github-app-private-key"
  description = "PEM private key for the GitHub App the agent uses to open/merge PRs."
}

resource "aws_secretsmanager_secret" "github_app_installation_id" {
  name        = "${var.project_name}/github-app-installation-id"
  description = "Installation id of the GitHub App on ${var.github_owner}/${var.github_repo}."
}

resource "aws_secretsmanager_secret" "databricks_ci_client_secret" {
  name        = "${var.project_name}/databricks-ci-client-secret"
  description = "OAuth M2M client secret for the service principal scoped to agent_ci_* schemas only (see profiles/profiles.yml ci target). Client id itself lives in var.databricks_ci_client_id -- not sensitive."
}

resource "aws_secretsmanager_secret" "slack_webhook_url" {
  name        = "${var.project_name}/slack-webhook-url"
  description = "Incoming webhook URL the agent posts notifications to."
}
