output "events_stream_name" {
  value = aws_kinesis_stream.events.name
}

output "state_table_name" {
  value = aws_dynamodb_table.state.name
}

output "raw_bucket" {
  value = aws_s3_bucket.raw.bucket
}

output "features_bucket" {
  value = aws_s3_bucket.features.bucket
}

output "models_bucket" {
  value = aws_s3_bucket.models.bucket
}

output "dead_letter_queue_url" {
  value = aws_sqs_queue.dlq.url
}

output "state_machine_arn" {
  value = one(aws_sfn_state_machine.replenishment[*].arn)
}

output "http_api_endpoint" {
  value = one(aws_apigatewayv2_api.http[*].api_endpoint)
}

output "sagemaker_model_package_group" {
  value = aws_sagemaker_model_package_group.stockout.model_package_group_name
}

output "guardrail_id" {
  value = aws_bedrock_guardrail.planner.guardrail_id
}
