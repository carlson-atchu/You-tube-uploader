terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region"       { default = "us-east-1" }
variable "project_name"     { default = "yt-auto-uploader" }
variable "upload_mode"      { default = "dual" }
variable "channel_category" { default = "kids_learning" }
variable "schedule_hours"      { default = 6 }
variable "video_duration_secs" { default = 10800 }  # 3 hours default; set to 300 for preview

data "aws_caller_identity" "current" {}

resource "aws_kms_key" "main" {
  description             = "KMS key for yt-auto-uploader"
  deletion_window_in_days = 10
  enable_key_rotation     = true
  tags                    = { Project = var.project_name }
}

resource "aws_kms_alias" "main" {
  name          = "alias/${var.project_name}"
  target_key_id = aws_kms_key.main.key_id
}

resource "aws_s3_bucket" "videos" {
  bucket = "${var.project_name}-videos-${data.aws_caller_identity.current.account_id}"
  tags   = { Project = var.project_name }
}

resource "aws_s3_bucket_public_access_block" "videos" {
  bucket                  = aws_s3_bucket.videos.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "videos" {
  bucket = aws_s3_bucket.videos.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.main.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "videos" {
  bucket = aws_s3_bucket.videos.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket" "logs" {
  bucket = "${var.project_name}-logs-${data.aws_caller_identity.current.account_id}"
  tags   = { Project = var.project_name }
}

resource "aws_s3_bucket_public_access_block" "logs" {
  bucket                  = aws_s3_bucket.logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.main.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "logs" {
  bucket = aws_s3_bucket.logs.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_logging" "videos" {
  bucket        = aws_s3_bucket.videos.id
  target_bucket = aws_s3_bucket.logs.id
  target_prefix = "s3-access-logs/"
}

resource "aws_s3_bucket_logging" "logs_self" {
  bucket        = aws_s3_bucket.logs.id
  target_bucket = aws_s3_bucket.logs.id
  target_prefix = "self-logs/"
}

resource "aws_s3_bucket_lifecycle_configuration" "videos" {
  bucket = aws_s3_bucket.videos.id
  rule {
    id     = "expire-uploaded"
    status = "Enabled"
    filter { prefix = "uploaded/" }
    expiration { days = 90 }
  }
}

resource "aws_secretsmanager_secret" "youtube_creds" {
  name        = "${var.project_name}/youtube-credentials"
  description = "YouTube OAuth2 credentials"
  kms_key_id  = aws_kms_key.main.arn
}

resource "aws_secretsmanager_secret" "claude_api" {
  name        = "${var.project_name}/claude-api-key"
  description = "Anthropic Claude API key"
  kms_key_id  = aws_kms_key.main.arn
}

resource "aws_iam_role" "lambda_role" {
  name = "${var.project_name}-lambda-role"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "lambda_policy" {
  name = "${var.project_name}-lambda-policy"
  role = aws_iam_role.lambda_role.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject","s3:PutObject","s3:DeleteObject","s3:ListBucket","s3:CopyObject"]
        Resource = [aws_s3_bucket.videos.arn,"${aws_s3_bucket.videos.arn}/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [aws_secretsmanager_secret.youtube_creds.arn,aws_secretsmanager_secret.claude_api.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt","kms:GenerateDataKey"]
        Resource = aws_kms_key.main.arn
      },
      {
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = aws_lambda_function.video_generator.arn
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Effect   = "Allow"
        Action   = ["xray:PutTraceSegments","xray:PutTelemetryRecords"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["polly:SynthesizeSpeech"]
        Resource = "*"
      }
    ]
  })
}

data "archive_file" "upload_handler_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda_functions"
  output_path = "${path.module}/upload_handler.zip"
}

resource "aws_lambda_function" "upload_handler" {
  function_name    = "${var.project_name}-upload-handler"
  role             = aws_iam_role.lambda_role.arn
  handler          = "upload_handler.lambda_handler"
  runtime          = "python3.12"
  timeout          = 900
  memory_size      = 1024
  filename         = data.archive_file.upload_handler_zip.output_path
  source_code_hash = data.archive_file.upload_handler_zip.output_base64sha256
  layers           = [aws_lambda_layer_version.python_deps.arn]
  tracing_config { mode = "Active" }
  ephemeral_storage { size = 10240 }
  environment {
    variables = {
      S3_BUCKET_NAME        = aws_s3_bucket.videos.id
      PREMADE_PREFIX        = "premade-videos/"
      GENERATED_PREFIX      = "generated-videos/"
      UPLOAD_MODE           = var.upload_mode
      CHANNEL_CATEGORY      = var.channel_category
      YOUTUBE_SECRET_NAME   = aws_secretsmanager_secret.youtube_creds.name
      CLAUDE_SECRET_NAME    = aws_secretsmanager_secret.claude_api.name
      VIDEO_GEN_LAMBDA_NAME = aws_lambda_function.video_generator.function_name
      VIDEO_DURATION_SECS   = tostring(var.video_duration_secs)
    }
  }
  tags = { Project = var.project_name }
}

data "archive_file" "video_generator_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda_functions"
  output_path = "${path.module}/video_generator.zip"
}

resource "aws_s3_object" "asset_video" {
  bucket = aws_s3_bucket.videos.id
  key    = "assets/nature/jungle_rain_clip.mp4"
  source = "${path.module}/../assets/nature/jungle_rain_clip.mp4"
  etag   = filemd5("${path.module}/../assets/nature/jungle_rain_clip.mp4")
}

resource "aws_s3_object" "asset_audio" {
  bucket = aws_s3_bucket.videos.id
  key    = "assets/audio/rain_jungle_1.mp3"
  source = "${path.module}/../assets/audio/rain_jungle_1.mp3"
  etag   = filemd5("${path.module}/../assets/audio/rain_jungle_1.mp3")
}

resource "aws_s3_object" "asset_font" {
  bucket = aws_s3_bucket.videos.id
  key    = "assets/fonts/Nunito-Bold.ttf"
  source = "${path.module}/../assets/fonts/Nunito-Bold.ttf"
  etag   = filemd5("${path.module}/../assets/fonts/Nunito-Bold.ttf")
}

resource "aws_lambda_function" "video_generator" {
  function_name    = "${var.project_name}-video-generator"
  role             = aws_iam_role.lambda_role.arn
  handler          = "video_generator.lambda_handler"
  runtime          = "python3.12"
  timeout          = 900
  memory_size      = 3008
  filename         = data.archive_file.video_generator_zip.output_path
  source_code_hash = data.archive_file.video_generator_zip.output_base64sha256
  layers           = [
    aws_lambda_layer_version.python_deps.arn,
    aws_lambda_layer_version.ffmpeg.arn,
  ]
  tracing_config { mode = "Active" }
  environment {
    variables = {
      S3_BUCKET_NAME   = aws_s3_bucket.videos.id
      GENERATED_PREFIX = "generated-videos/"
      ASSETS_PREFIX    = "assets/"
    }
  }
  ephemeral_storage { size = 10240 }
  tags = { Project = var.project_name }
}

resource "aws_lambda_layer_version" "python_deps" {
  layer_name          = "${var.project_name}-python-deps"
  filename            = "${path.module}/layers/python_deps.zip"
  source_code_hash    = filebase64sha256("${path.module}/layers/python_deps.zip")
  compatible_runtimes = ["python3.12"]
}

resource "aws_s3_object" "ffmpeg_layer_zip" {
  bucket = aws_s3_bucket.videos.id
  key    = "layers/ffmpeg.zip"
  source = "${path.module}/layers/ffmpeg.zip"
  etag   = filemd5("${path.module}/layers/ffmpeg.zip")
}

resource "aws_lambda_layer_version" "ffmpeg" {
  layer_name          = "${var.project_name}-ffmpeg"
  s3_bucket           = aws_s3_object.ffmpeg_layer_zip.bucket
  s3_key              = aws_s3_object.ffmpeg_layer_zip.key
  source_code_hash    = filebase64sha256("${path.module}/layers/ffmpeg.zip")
  compatible_runtimes = ["python3.12"]
}

resource "aws_cloudwatch_event_rule" "every_6_hours" {
  name                = "${var.project_name}-schedule"
  description         = "Trigger YouTube uploader every ${var.schedule_hours} hours"
  schedule_expression = "rate(${var.schedule_hours} hours)"
}

resource "aws_cloudwatch_event_target" "upload_target" {
  rule      = aws_cloudwatch_event_rule.every_6_hours.name
  target_id = "UploadHandler"
  arn       = aws_lambda_function.upload_handler.arn
}

resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.upload_handler.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.every_6_hours.arn
}

resource "aws_cloudwatch_metric_alarm" "upload_failures" {
  alarm_name          = "${var.project_name}-upload-failures"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 21600
  statistic           = "Sum"
  threshold           = 2
  dimensions          = { FunctionName = aws_lambda_function.upload_handler.function_name }
  alarm_description   = "Alert when upload handler fails 2+ times in a 6-hour window"
}

output "s3_bucket_name"     { value = aws_s3_bucket.videos.id }
output "upload_handler_arn" { value = aws_lambda_function.upload_handler.arn }
output "youtube_secret_arn" { value = aws_secretsmanager_secret.youtube_creds.arn }
output "kms_key_arn"        { value = aws_kms_key.main.arn }
