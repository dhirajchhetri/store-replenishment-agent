variable "aws_region" {
  description = "Region for the reference stack."
  type        = string
  default     = "us-east-1"
}

variable "name" {
  description = "Prefix for resource names."
  type        = string
  default     = "store-replenish"
}

variable "enable_compute" {
  description = "Create Lambdas, Step Functions, the schedule, and the HTTP API. Requires lambda_zip_path."
  type        = bool
  default     = false
}

variable "lambda_zip_path" {
  description = "Zip produced by scripts/package_lambda.sh. Required when enable_compute is true."
  type        = string
  default     = "../../dist/lambda.zip"
}

variable "schedule_store_id" {
  description = "Store id passed to the hourly state machine. Add a schedule per store, or change this value."
  type        = string
  default     = "store-100"
}

variable "bedrock_model_id" {
  description = "Bedrock model the planner Lambda is allowed to invoke. The offline path does not call it."
  type        = string
  default     = "anthropic.claude-3-5-haiku-20241022-v1:0"
}

variable "enable_sagemaker_endpoint" {
  description = "Create a SageMaker model and endpoint. Leave false until an image and model artifact exist."
  type        = bool
  default     = false
}

variable "sagemaker_image_uri" {
  description = "ECR image that runs sagemaker/serve.py. Required when enable_sagemaker_endpoint is true."
  type        = string
  default     = ""
}

variable "model_data_url" {
  description = "s3:// URI of model.tar.gz produced by the training container. Required with the endpoint."
  type        = string
  default     = ""
}
