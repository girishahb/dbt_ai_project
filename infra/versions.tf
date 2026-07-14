terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  # Partial backend config on purpose -- the bucket/table names depend on
  # what you name the one-time bootstrap resources (see infra/README.md),
  # so they're supplied via `-backend-config` flags at `terraform init`
  # time (locally, or from .github/workflows/deploy-agent.yml) rather than
  # hardcoded here. This is required, not optional, once Terraform runs
  # from GitHub Actions: every workflow run is a fresh ephemeral runner
  # with no local state, so without a remote backend each run would have
  # no memory of what it already created.
  backend "s3" {}
}

provider "aws" {
  region = var.aws_region
}
