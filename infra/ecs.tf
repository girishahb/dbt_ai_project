resource "aws_ecr_repository" "agent" {
  name                 = "${var.project_name}-agent"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecs_cluster" "agent" {
  name = "${var.project_name}-cluster"
}

# --- Execution role: what ECS itself needs to *start* the container
# (pull from ECR, write its own logs, read secrets to inject as env vars).
# Distinct from the task role below on purpose -- this is standard ECS
# practice so "can this launch a container" and "what can the code inside
# the container do" stay two separately auditable IAM policies.
resource "aws_iam_role" "agent_task_execution" {
  name = "${var.project_name}-task-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "agent_task_execution_managed" {
  role       = aws_iam_role.agent_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "agent_task_execution_secrets" {
  name = "${var.project_name}-task-execution-secrets"
  role = aws_iam_role.agent_task_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "ReadSecretsForContainerEnv"
      Effect = "Allow"
      Action = ["secretsmanager:GetSecretValue"]
      Resource = [
        aws_secretsmanager_secret.github_app_private_key.arn,
        aws_secretsmanager_secret.github_app_installation_id.arn,
        aws_secretsmanager_secret.databricks_ci_client_secret.arn,
        aws_secretsmanager_secret.slack_webhook_url.arn,
      ]
    }]
  })
}

# --- Task role: what the agent's own code (agent/) can do at runtime.
# This is the guardrail-critical one -- see the plan's "Least-privilege
# IAM/tokens" section. Notably absent: any S3/RDS/broad ecs/iam access, and
# no Databricks *prod* write path (that's controlled by which service
# principal's OAuth secret is in databricks_ci_client_secret, not by AWS IAM).
resource "aws_iam_role" "agent_task" {
  name = "${var.project_name}-task"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "agent_task" {
  name = "${var.project_name}-task"
  role = aws_iam_role.agent_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Claude Sonnet 4.5 only supports on-demand invocation through a
        # cross-region inference profile (var.bedrock_model_id defaults to
        # "us.anthropic...", not the bare model id) -- which in turn routes
        # the request to whichever US region has capacity, so both the
        # inference-profile resource itself and foundation-model in every US
        # region it might route to need to be allowed.
        Sid    = "InvokeBedrock"
        Effect = "Allow"
        Action = ["bedrock:InvokeModel"]
        Resource = [
          "arn:aws:bedrock:*::foundation-model/*",
          "arn:aws:bedrock:${var.aws_region}:*:inference-profile/*",
        ]
      },
      {
        Sid    = "ReadMwaaLogsAndTriggerRuns"
        Effect = "Allow"
        Action = [
          "airflow:CreateWebLoginToken",
          "airflow:GetEnvironment",
        ]
        Resource = "arn:aws:airflow:${var.aws_region}:*:environment/${var.mwaa_environment_name}"
      },
      {
        Sid      = "ReadMwaaTaskLogsFallback"
        Effect   = "Allow"
        Action   = ["logs:DescribeLogStreams", "logs:GetLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:*:log-group:airflow-${var.mwaa_environment_name}-Task:*"
      },
      {
        Sid      = "CircuitBreakerReadWrite"
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem"]
        Resource = aws_dynamodb_table.self_heal_attempts.arn
      },
      {
        Sid      = "OwnLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:*:log-group:/ecs/${var.project_name}-agent*"
      },
    ]
  })
}

resource "aws_cloudwatch_log_group" "agent" {
  name              = "/ecs/${var.project_name}-agent"
  retention_in_days = 30
}

resource "aws_ecs_task_definition" "agent" {
  family                   = "${var.project_name}-agent"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "1024"
  memory                   = "2048"
  execution_role_arn       = aws_iam_role.agent_task_execution.arn
  task_role_arn            = aws_iam_role.agent_task.arn

  container_definitions = jsonencode([
    {
      name  = "agent"
      image = "${aws_ecr_repository.agent.repository_url}:${var.agent_image_tag}"
      # FAILURE_* vars are set per-invocation via ECS RunTask containerOverrides
      # (dispatcher/handler.py) -- everything below is static, run-independent config.
      environment = [
        { name = "AWS_REGION", value = var.aws_region },
        { name = "MWAA_ENVIRONMENT_NAME", value = var.mwaa_environment_name },
        { name = "BEDROCK_MODEL_ID", value = var.bedrock_model_id },
        { name = "GITHUB_OWNER", value = var.github_owner },
        { name = "GITHUB_REPO", value = var.github_repo },
        { name = "GITHUB_APP_ID", value = var.github_app_id },
        { name = "DBT_DATABRICKS_HOST", value = var.databricks_host },
        { name = "DBT_DATABRICKS_HTTP_PATH", value = var.databricks_http_path },
        { name = "DBT_DATABRICKS_CATALOG", value = var.databricks_catalog },
        { name = "DBT_DATABRICKS_CI_SCHEMA", value = var.databricks_ci_schema },
        { name = "DBT_DATABRICKS_CI_CLIENT_ID", value = var.databricks_ci_client_id },
      ]
      secrets = [
        { name = "GITHUB_APP_PRIVATE_KEY", valueFrom = aws_secretsmanager_secret.github_app_private_key.arn },
        { name = "GITHUB_APP_INSTALLATION_ID", valueFrom = aws_secretsmanager_secret.github_app_installation_id.arn },
        { name = "DBT_DATABRICKS_CI_CLIENT_SECRET", valueFrom = aws_secretsmanager_secret.databricks_ci_client_secret.arn },
        { name = "SLACK_WEBHOOK_URL", valueFrom = aws_secretsmanager_secret.slack_webhook_url.arn },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.agent.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "agent"
        }
      }
    }
  ])

  tags = {
    Project = var.project_name
  }
}
