variable "aws_region" {
  description = "AWS region hosting MWAA, the agent's Fargate task, and all supporting resources."
  type        = string
}

variable "project_name" {
  description = "Short name used to prefix/tag every resource this root creates."
  type        = string
  default     = "dbt-self-heal"
}

variable "mwaa_environment_name" {
  description = "Name of the existing MWAA environment (see mwaa/) whose task failures trigger the agent."
  type        = string
}

variable "github_owner" {
  description = "GitHub org/user that owns the dbt project repo."
  type        = string
  default     = "girishahb"
}

variable "github_repo" {
  description = "GitHub repo name the agent opens branches/PRs against."
  type        = string
  default     = "dbt_ai_project"
}

# --- Networking -------------------------------------------------------
# Deliberately no defaults / no VPC created here: which VPC/subnets the
# Fargate task runs in is an account-level decision for a human to make
# (existing MWAA VPC is usually the right choice, so the task can reach
# whatever reaches Databricks/GitHub/Bedrock today), not something to guess
# in code.

variable "vpc_id" {
  description = "VPC the agent's Fargate task runs in (typically the same VPC as the MWAA environment)."
  type        = string
}

variable "subnet_ids" {
  description = "Subnet(s) for the Fargate task. Needs outbound internet access (NAT or public + assign_public_ip) to reach Databricks/GitHub/Bedrock."
  type        = list(string)
}

variable "security_group_ids" {
  description = "Security group(s) for the Fargate task's ENI. Must allow outbound HTTPS."
  type        = list(string)
}

# --- Databricks connection (non-secret connection details; the actual
# token lives in Secrets Manager -- see secrets.tf / databricks_ci_token) ---

variable "databricks_host" {
  description = "Databricks workspace hostname, no https:// (matches DBT_DATABRICKS_HOST in profiles/profiles.yml)."
  type        = string
}

variable "databricks_http_path" {
  description = "SQL warehouse/cluster HTTP path (matches DBT_DATABRICKS_HTTP_PATH)."
  type        = string
}

variable "databricks_catalog" {
  description = "Unity Catalog name (matches DBT_DATABRICKS_CATALOG)."
  type        = string
  default     = "ai_project"
}

variable "databricks_ci_schema" {
  description = "Scratch schema the agent validates fixes in (matches DBT_DATABRICKS_CI_SCHEMA -- see profiles/profiles.yml ci target)."
  type        = string
  default     = "agent_ci"
}

variable "github_app_id" {
  description = "The GitHub App's App ID (not the installation id, which lives in Secrets Manager -- App ID itself isn't sensitive, it's visible on the app's public settings page)."
  type        = string
}

# --- Bedrock ------------------------------------------------------------

variable "bedrock_model_id" {
  description = "Bedrock model id the agent calls for classification/fix-proposal (must have model access enabled in the Bedrock console first)."
  type        = string
  default     = "anthropic.claude-sonnet-4-5-20250929-v1:0"
}

# --- Circuit breaker ------------------------------------------------------

variable "circuit_breaker_max_attempts_per_day" {
  description = "Max auto-fix attempts per dag_id+model per UTC day before the dispatcher just notifies instead of starting the agent."
  type        = number
  default     = 1
}

# --- Container image ------------------------------------------------------

variable "agent_image_tag" {
  description = "Tag of the agent image in ECR to run (set by the deploy workflow after a successful docker push)."
  type        = string
  default     = "latest"
}
