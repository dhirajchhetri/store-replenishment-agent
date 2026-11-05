resource "aws_sagemaker_model_package_group" "stockout" {
  model_package_group_name        = "${var.name}-stockout"
  model_package_group_description = "Shelf demand forecasts. Register a version only after a holdout check on the training set you supply."
}

resource "aws_sagemaker_model" "stockout" {
  count = var.enable_sagemaker_endpoint ? 1 : 0

  name               = "${var.name}-stockout"
  execution_role_arn = aws_iam_role.sagemaker.arn

  primary_container {
    image          = var.sagemaker_image_uri
    model_data_url = var.model_data_url
    environment = {
      SAGEMAKER_PROGRAM = "serve.py"
    }
  }
}

resource "aws_sagemaker_endpoint_configuration" "stockout" {
  count = var.enable_sagemaker_endpoint ? 1 : 0

  name = "${var.name}-stockout"

  production_variants {
    variant_name           = "AllTraffic"
    model_name             = aws_sagemaker_model.stockout[0].name
    initial_instance_count = 1
    instance_type          = "ml.m5.large"
  }
}

resource "aws_sagemaker_endpoint" "stockout" {
  count = var.enable_sagemaker_endpoint ? 1 : 0

  name                 = "${var.name}-stockout"
  endpoint_config_name = aws_sagemaker_endpoint_configuration.stockout[0].name
}

resource "aws_bedrock_guardrail" "planner" {
  name                      = "${var.name}-planner"
  description               = "Prompt-attack filter for free text. Shelf capacity and reserved-stock rules stay in application code."
  blocked_input_messaging   = "The planner cannot process that request."
  blocked_outputs_messaging = "The planner cannot return that response."

  content_policy_config {
    filters_config {
      input_strength  = "HIGH"
      output_strength = "NONE"
      type            = "PROMPT_ATTACK"
    }
  }
}
