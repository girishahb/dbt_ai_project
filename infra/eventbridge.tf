# Matches the event dags/dbt_common.py's on_failure_callback publishes to
# the account's default event bus, and routes it at the dispatcher Lambda.
# Using the default bus (rather than a custom one) keeps this simple -- this
# project doesn't need cross-account routing or multiple independent event
# producers/consumers, so a dedicated bus would just be extra to manage.
resource "aws_cloudwatch_event_rule" "dbt_task_failed" {
  name        = "${var.project_name}-dbt-task-failed"
  description = "Matches DbtTaskFailed events published by MWAA's on_failure_callback."

  event_pattern = jsonencode({
    source        = ["airflow.dbt"]
    "detail-type" = ["DbtTaskFailed"]
  })
}

resource "aws_cloudwatch_event_target" "dispatcher" {
  rule      = aws_cloudwatch_event_rule.dbt_task_failed.name
  target_id = "dispatcher-lambda"
  arn       = aws_lambda_function.dispatcher.arn
}

resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.dispatcher.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.dbt_task_failed.arn
}
