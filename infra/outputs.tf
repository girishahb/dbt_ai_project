output "circuit_breaker_table_name" {
  value = aws_dynamodb_table.self_heal_attempts.name
}

output "secrets_to_populate" {
  description = "Run `aws secretsmanager put-secret-value` for each of these once (see agent/README.md) before the agent's first real run."
  value = [
    aws_secretsmanager_secret.github_app_private_key.name,
    aws_secretsmanager_secret.github_app_installation_id.name,
    aws_secretsmanager_secret.databricks_ci_client_secret.name,
    aws_secretsmanager_secret.slack_webhook_url.name,
  ]
}

output "ecr_repository_url" {
  description = "Push the agent image here (see agent/README.md / .github/workflows/deploy-agent.yml)."
  value       = aws_ecr_repository.agent.repository_url
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.agent.name
}

output "ecs_task_definition_arn" {
  value = aws_ecs_task_definition.agent.arn
}

output "dispatcher_lambda_name" {
  value = aws_lambda_function.dispatcher.function_name
}

output "eventbridge_rule_name" {
  value = aws_cloudwatch_event_rule.dbt_task_failed.name
}
