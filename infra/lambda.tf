data "archive_file" "dispatcher" {
  type        = "zip"
  source_dir  = "${path.module}/../dispatcher"
  output_path = "${path.module}/.build/dispatcher.zip"
}

resource "aws_iam_role" "dispatcher" {
  name = "${var.project_name}-dispatcher"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "dispatcher" {
  name = "${var.project_name}-dispatcher"
  role = aws_iam_role.dispatcher.id

  # Least privilege: this function only ever needs to (a) claim/read the
  # circuit-breaker row for the dag+task+day it was invoked for, (b) start
  # exactly one known Fargate task definition, and (c) read the Slack
  # webhook secret to post a heads-up. It never needs broader
  # dynamodb/ecs/secretsmanager access than that.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "CircuitBreakerTable"
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem"]
        Resource = aws_dynamodb_table.self_heal_attempts.arn
      },
      {
        Sid      = "StartAgentTask"
        Effect   = "Allow"
        Action   = ["ecs:RunTask"]
        Resource = aws_ecs_task_definition.agent.arn
      },
      {
        Sid      = "PassAgentTaskRoles"
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = [aws_iam_role.agent_task.arn, aws_iam_role.agent_task_execution.arn]
      },
      {
        Sid      = "ReadSlackWebhook"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = aws_secretsmanager_secret.slack_webhook_url.arn
      },
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:*:log-group:/aws/lambda/${var.project_name}-dispatcher*"
      },
    ]
  })
}

resource "aws_lambda_function" "dispatcher" {
  function_name    = "${var.project_name}-dispatcher"
  role             = aws_iam_role.dispatcher.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 30
  filename         = data.archive_file.dispatcher.output_path
  source_code_hash = data.archive_file.dispatcher.output_base64sha256

  environment {
    variables = {
      CIRCUIT_BREAKER_TABLE_NAME = aws_dynamodb_table.self_heal_attempts.name
      ECS_CLUSTER_ARN            = aws_ecs_cluster.agent.arn
      ECS_TASK_DEFINITION_ARN    = aws_ecs_task_definition.agent.arn
      ECS_CONTAINER_NAME         = "agent"
      SUBNET_IDS                 = join(",", var.subnet_ids)
      SECURITY_GROUP_IDS         = join(",", var.security_group_ids)
      SLACK_WEBHOOK_SECRET_ARN   = aws_secretsmanager_secret.slack_webhook_url.arn
      CIRCUIT_BREAKER_TTL_HOURS  = "48"
    }
  }

  tags = {
    Project = var.project_name
  }
}
