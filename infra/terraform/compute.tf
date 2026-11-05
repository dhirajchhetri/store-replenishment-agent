locals {
  lambda_env = {
    REPLENISHMENT_BACKEND          = "dynamodb"
    REPLENISHMENT_TABLE            = aws_dynamodb_table.state.name
    REPLENISHMENT_LLM              = "bedrock"
    REPLENISHMENT_BEDROCK_MODEL_ID = var.bedrock_model_id
  }

  handlers = {
    score     = "replenishment.handlers.score"
    decide    = "replenishment.handlers.decide"
    guardrail = "replenishment.handlers.guardrail"
    save      = "replenishment.handlers.save"
    ingest    = "replenishment.handlers.ingest"
    api       = "replenishment.handlers.api"
  }
}

resource "aws_cloudwatch_log_group" "lambda" {
  for_each = var.enable_compute ? local.handlers : {}

  name              = "/aws/lambda/${var.name}-${each.key}"
  retention_in_days = 30
  kms_key_id        = aws_kms_key.data.arn
}

resource "aws_lambda_function" "worker" {
  for_each = var.enable_compute ? local.handlers : {}

  function_name    = "${var.name}-${each.key}"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = each.value
  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)
  timeout          = each.key == "api" ? 30 : 60
  memory_size      = 1024
  kms_key_arn      = aws_kms_key.data.arn

  environment {
    variables = local.lambda_env
  }

  depends_on = [aws_cloudwatch_log_group.lambda]
}

resource "aws_lambda_event_source_mapping" "ingest" {
  count = var.enable_compute ? 1 : 0

  event_source_arn               = aws_kinesis_stream.events.arn
  function_name                  = aws_lambda_function.worker["ingest"].arn
  starting_position              = "LATEST"
  batch_size                     = 100
  maximum_retry_attempts         = 3
  bisect_batch_on_function_error = true
  destination_config {
    on_failure {
      destination_arn = aws_sqs_queue.dlq.arn
    }
  }
}

resource "aws_sfn_state_machine" "replenishment" {
  count = var.enable_compute ? 1 : 0

  name     = var.name
  role_arn = aws_iam_role.states.arn
  definition = replace(replace(replace(replace(replace(
    file("${path.module}/../step_functions/replenishment.asl.json"),
    "__SCORE_ARN__", aws_lambda_function.worker["score"].arn),
    "__DECIDE_ARN__", aws_lambda_function.worker["decide"].arn),
    "__GUARDRAIL_ARN__", aws_lambda_function.worker["guardrail"].arn),
    "__SAVE_ARN__", aws_lambda_function.worker["save"].arn),
    "__DLQ_URL__", aws_sqs_queue.dlq.url,
  )
}

resource "aws_scheduler_schedule" "hourly" {
  count = var.enable_compute ? 1 : 0

  name = "${var.name}-hourly"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression = "cron(0 8-20 * * ? *)"

  target {
    arn      = aws_sfn_state_machine.replenishment[0].arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ store_id = var.schedule_store_id })

    dead_letter_config {
      arn = aws_sqs_queue.dlq.arn
    }
  }
}

resource "aws_apigatewayv2_api" "http" {
  count         = var.enable_compute ? 1 : 0
  name          = "${var.name}-http"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "api" {
  count = var.enable_compute ? 1 : 0

  api_id                 = aws_apigatewayv2_api.http[0].id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.worker["api"].invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "proxy" {
  count = var.enable_compute ? 1 : 0

  api_id    = aws_apigatewayv2_api.http[0].id
  route_key = "ANY /{proxy+}"
  target    = "integrations/${aws_apigatewayv2_integration.api[0].id}"
}

resource "aws_apigatewayv2_stage" "default" {
  count = var.enable_compute ? 1 : 0

  api_id      = aws_apigatewayv2_api.http[0].id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "api" {
  count = var.enable_compute ? 1 : 0

  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.worker["api"].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http[0].execution_arn}/*/*"
}
