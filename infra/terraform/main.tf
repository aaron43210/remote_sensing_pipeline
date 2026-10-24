data "aws_caller_identity" "me" {}

locals {
  bucket   = "hyperspectral-${var.name}-${var.env}"
  services = ["api_gateway", "orchestrator", "ingestion", "preprocessing", "ml_inference", "event_sink", "frontend", "tools", "pipelines"]
}

# ── Object storage: the data lake (bronze / silver / gold / models) ──────────
resource "aws_s3_bucket" "lake" {
  bucket = local.bucket
}

resource "aws_s3_bucket_public_access_block" "lake" {
  bucket                  = aws_s3_bucket.lake.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "lake" {
  bucket = aws_s3_bucket.lake.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id
  dynamic "rule" {
    for_each = { "bronze/emit/" = 30, "bronze/landsat/" = 30, "silver/" = 14, "gold/results/" = 180, "ops/pg_backups/" = 30 }
    content {
      id     = "expire-${replace(trimsuffix(rule.key, "/"), "/", "-")}"
      status = "Enabled"
      filter { prefix = rule.key }
      expiration { days = rule.value }
      noncurrent_version_expiration { noncurrent_days = 7 }
    }
  }
  rule {
    id     = "abort-multipart"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload { days_after_initiation = 2 }
  }
}

# The browser fetches maps straight from S3 with presigned URLs (incl. COG range reads)
resource "aws_s3_bucket_cors_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id
  cors_rule {
    allowed_methods = ["GET", "HEAD"]
    allowed_origins = ["https://${aws_cloudfront_distribution.site.domain_name}"]
    allowed_headers = ["Range", "Authorization"]
    expose_headers  = ["Content-Range", "Content-Length", "ETag", "Accept-Ranges"]
    max_age_seconds = 3600
  }
}

# ── Container registry ───────────────────────────────────────────────────────
resource "aws_ecr_repository" "svc" {
  for_each             = toset(local.services)
  name                 = "hydranet/${each.key}"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
}

resource "aws_ecr_lifecycle_policy" "svc" {
  for_each   = aws_ecr_repository.svc
  repository = each.value.name
  policy = jsonencode({ rules = [{ rulePriority = 1, description = "keep 15 images",
    selection = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 15 },
  action = { type = "expire" } }] })
}

# ── Secrets (SSM Parameter Store, free tier) ─────────────────────────────────
resource "random_password" "jwt" {
  length  = 64
  special = false
}
resource "random_password" "db" {
  length  = 32
  special = false
}
resource "random_password" "admin" {
  length  = 20
  special = false
}
resource "random_id" "credentials" { # AES-256 key for users' NASA tokens: 64 hex characters
  byte_length = 32
}

resource "aws_ssm_parameter" "secret" {
  for_each = {
    JWT_SECRET      = random_password.jwt.result
    CREDENTIALS_KEY = random_id.credentials.hex
    DB_PASSWORD     = random_password.db.result
    ADMIN_PASSWORD  = random_password.admin.result
    EARTHDATA_TOKEN = var.earthdata_token == "" ? "unset" : var.earthdata_token
  }
  name  = "/hydranet/${var.env}/${each.key}"
  type  = "SecureString"
  value = each.value
}

resource "aws_ssm_parameter" "config" {
  for_each = {
    S3_BUCKET     = aws_s3_bucket.lake.bucket
    S3_REGION     = var.region
    PUBLIC_ORIGIN = "https://${aws_cloudfront_distribution.site.domain_name}"
    ADMIN_EMAIL   = var.admin_email
    REGISTRY      = "${data.aws_caller_identity.me.account_id}.dkr.ecr.${var.region}.amazonaws.com"
  }
  name  = "/hydranet/${var.env}/${each.key}"
  type  = "String"
  value = each.value
}

# ── Instance role: this bucket, its logs, ECR pulls, SSM (no SSH keys) ───────
resource "aws_iam_role" "node" {
  name = "hydranet-${var.env}-node"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow",
  Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "ecr" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

resource "aws_iam_role_policy" "node" {
  name = "hydranet-node"
  role = aws_iam_role.node.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["s3:ListBucket", "s3:GetBucketLocation"], Resource = aws_s3_bucket.lake.arn },
    { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"],
    Resource = "${aws_s3_bucket.lake.arn}/*" },
    { Effect = "Allow", Action = ["ssm:GetParameter", "ssm:GetParametersByPath"],
    Resource = "arn:aws:ssm:${var.region}:${data.aws_caller_identity.me.account_id}:parameter/hydranet/${var.env}/*" },
    { Effect = "Allow", Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:PutRetentionPolicy"],
    Resource = "*" },
  ] })
}

resource "aws_iam_instance_profile" "node" {
  name = "hydranet-${var.env}-node"
  role = aws_iam_role.node.name
}

# ── Network: only CloudFront may reach the instance ──────────────────────────
data "aws_vpc" "default" {
  default = true
}

data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

resource "aws_security_group" "node" {
  name        = "hydranet-${var.env}"
  description = "HTTP from CloudFront only; no SSH (use SSM Session Manager)"
  vpc_id      = data.aws_vpc.default.id
  ingress {
    from_port       = 80
    to_port         = 80
    protocol        = "tcp"
    prefix_list_ids = [data.aws_ec2_managed_prefix_list.cloudfront.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

data "aws_ssm_parameter" "al2023" {
  name = var.instance_type == "t4g.large" || startswith(var.instance_type, "t4g") || startswith(var.instance_type, "c7g") || startswith(var.instance_type, "m7g") ? "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64" : "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

resource "aws_instance" "node" {
  ami                    = data.aws_ssm_parameter.al2023.value
  instance_type          = var.instance_type
  iam_instance_profile   = aws_iam_instance_profile.node.name
  vpc_security_group_ids = [aws_security_group.node.id]
  user_data              = templatefile("${path.module}/user_data.sh", { env = var.env, region = var.region })
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 2 # containers reach the instance role through IMDSv2
  }
  root_block_device {
    volume_size = var.volume_gb
    volume_type = "gp3"
    encrypted   = true
  }
  tags = { Name = "hydranet-${var.env}" }
}

# ── CloudFront: HTTPS for the site + /api (SSE needs no caching) ─────────────
data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}
data "aws_cloudfront_cache_policy" "optimized" {
  name = "Managed-CachingOptimized"
}
# Forwards the viewer's cookies and headers to the origin, plus CloudFront's own
# CloudFront-Viewer-Address (the real client IP, which nginx trusts only because
# the instance accepts traffic from CloudFront alone).
data "aws_cloudfront_origin_request_policy" "all_viewer" {
  name = "Managed-AllViewerAndCloudFrontHeaders-2022-06"
}

resource "aws_cloudfront_distribution" "site" {
  enabled         = true
  is_ipv6_enabled = true
  comment         = "hydranet-${var.env}"
  price_class     = "PriceClass_100"
  origin {
    domain_name = aws_instance.node.public_dns
    origin_id   = "node"
    custom_origin_config {
      http_port                = 80
      https_port               = 443
      origin_protocol_policy   = "http-only"
      origin_ssl_protocols     = ["TLSv1.2"]
      origin_read_timeout      = 60
      origin_keepalive_timeout = 60
    }
  }
  default_cache_behavior {
    target_origin_id       = "node"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = data.aws_cloudfront_cache_policy.optimized.id
    compress               = true
  }
  ordered_cache_behavior {
    path_pattern             = "/api/*"
    target_origin_id         = "node"
    viewer_protocol_policy   = "https-only"
    allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods           = ["GET", "HEAD"]
    cache_policy_id          = data.aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer.id
    compress                 = false
  }
  restrictions {
    geo_restriction { restriction_type = "none" }
  }
  viewer_certificate { cloudfront_default_certificate = true }
}

# ── Analytics over the lake (Athena + Glue, pay per query) ───────────────────
resource "aws_glue_catalog_database" "lake" {
  name = "hydranet_${var.env}"
}

resource "aws_glue_catalog_table" "job_facts" {
  database_name = aws_glue_catalog_database.lake.name
  name          = "job_facts"
  table_type    = "EXTERNAL_TABLE"
  parameters = { classification = "parquet", "projection.enabled" = "true", "projection.dt.type" = "date",
    "projection.dt.range" = "2026-01-01,NOW", "projection.dt.format" = "yyyy-MM-dd",
  "storage.location.template" = "s3://${local.bucket}/gold/tables/job_facts/dt=$${dt}/" }
  partition_keys {
    name = "dt"
    type = "string"
  }
  storage_descriptor {
    location      = "s3://${local.bucket}/gold/tables/job_facts/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"
    ser_de_info { serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe" }
    dynamic "columns" {
      for_each = { job_id = "string", user_id = "bigint", tasks = "string", source = "string", status = "string",
      area_km2 = "double", total_s = "double", reused = "boolean", model_version = "string" }
      content {
        name = columns.key
        type = columns.value
      }
    }
  }
}

resource "aws_athena_workgroup" "lake" {
  name = "hydranet-${var.env}"
  configuration {
    result_configuration { output_location = "s3://${local.bucket}/ops/athena/" }
    bytes_scanned_cutoff_per_query = 1073741824 # 1 GB guard rail
  }
}

# ── Cost guard rails ─────────────────────────────────────────────────────────
resource "aws_budgets_budget" "monthly" {
  name         = "hydranet-${var.env}"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"
  dynamic "notification" {
    for_each = [25, 50, 80, 100]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [var.alert_email]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  count = var.office_hours_only ? 1 : 0
  name  = "hydranet-${var.env}-scheduler"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow",
  Principal = { Service = "scheduler.amazonaws.com" }, Action = "sts:AssumeRole" }] })
  inline_policy {
    name = "start-stop"
    policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow",
    Action = ["ec2:StartInstances", "ec2:StopInstances"], Resource = aws_instance.node.arn }] })
  }
}

resource "aws_scheduler_schedule" "power" {
  for_each = var.office_hours_only ? { stop = "cron(0 22 * * ? *)", start = "cron(0 7 * * ? *)" } : {}
  name     = "hydranet-${var.env}-${each.key}"
  flexible_time_window { mode = "OFF" }
  schedule_expression          = each.value
  schedule_expression_timezone = "America/Los_Angeles"
  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ec2:${each.key}Instances"
    role_arn = aws_iam_role.scheduler[0].arn
    input    = jsonencode({ InstanceIds = [aws_instance.node.id] })
  }
}

# ── GitHub Actions deploys with OIDC (no long-lived keys) ────────────────────
resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

resource "aws_iam_role" "deploy" {
  name = "hydranet-${var.env}-deploy"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow",
    Principal = { Federated = aws_iam_openid_connect_provider.github.arn }, Action = "sts:AssumeRoleWithWebIdentity",
    Condition = { StringEquals = { "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com" },
  StringLike = { "token.actions.githubusercontent.com:sub" = "repo:${var.github_repo}:*" } } }] })
}

resource "aws_iam_role_policy" "deploy" {
  name = "deploy"
  role = aws_iam_role.deploy.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
    "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage"], Resource = [for r in aws_ecr_repository.svc : r.arn] },
    { Effect = "Allow", Action = ["ssm:SendCommand"], Resource = [aws_instance.node.arn,
    "arn:aws:ssm:${var.region}::document/AWS-RunShellScript"] },
    { Effect = "Allow", Action = ["ssm:GetCommandInvocation"], Resource = "*" },
    { Effect = "Allow", Action = ["s3:PutObject", "s3:GetObject"], Resource = "${aws_s3_bucket.lake.arn}/models/*" },
  ] })
}
