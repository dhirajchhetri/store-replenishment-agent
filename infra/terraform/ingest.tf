resource "aws_kinesis_stream" "events" {
  name             = "${var.name}-events"
  shard_count      = 1
  retention_period = 24
  encryption_type  = "KMS"
  kms_key_id       = aws_kms_key.data.id
}

resource "aws_kinesis_firehose_delivery_stream" "raw" {
  name        = "${var.name}-raw"
  destination = "extended_s3"

  kinesis_source_configuration {
    kinesis_stream_arn = aws_kinesis_stream.events.arn
    role_arn           = aws_iam_role.firehose.arn
  }

  extended_s3_configuration {
    role_arn            = aws_iam_role.firehose.arn
    bucket_arn          = aws_s3_bucket.raw.arn
    buffering_size      = 5
    buffering_interval  = 60
    compression_format  = "GZIP"
    prefix              = "events/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/"
    error_output_prefix = "errors/!{firehose:error-output-type}/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/"
    kms_key_arn         = aws_kms_key.data.arn
  }
}

resource "aws_sqs_queue" "dlq" {
  name                              = "${var.name}-dlq"
  kms_master_key_id                 = aws_kms_key.data.arn
  kms_data_key_reuse_period_seconds = 300
  message_retention_seconds         = 1209600
}

resource "aws_iot_topic_rule" "rfid" {
  name        = replace("${var.name}_rfid", "-", "_")
  enabled     = true
  sql         = "SELECT * FROM 'stores/+/rfid'"
  sql_version = "2016-03-23"

  kinesis {
    stream_name   = aws_kinesis_stream.events.name
    role_arn      = aws_iam_role.iot.arn
    partition_key = "$${store_id}"
  }
}
