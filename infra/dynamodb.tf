# Circuit breaker: prevents the dispatcher Lambda from starting a new agent
# run for a dag/model that already had an auto-fix attempt today. Without
# this, a fix that looks right but doesn't actually resolve the failure
# could re-trigger itself into a fix/fail/re-fix loop on every retry.
#
# Single-table, on-demand billing -- traffic here is "one write per DAG
# failure", nowhere near enough volume to justify provisioned capacity.
resource "aws_dynamodb_table" "self_heal_attempts" {
  name         = "${var.project_name}-attempts"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "attempt_key" # "{dag_id}#{model}#{yyyy-mm-dd}"

  attribute {
    name = "attempt_key"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at" # epoch seconds; dispatcher sets ~48h out
    enabled        = true
  }

  tags = {
    Project = var.project_name
  }
}
