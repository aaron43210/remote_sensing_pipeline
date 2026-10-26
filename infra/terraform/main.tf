data "aws_caller_identity" "me" {}

locals {
  bucket              = "hyperspectral-${var.name}-${var.env}"
  has_custom_domain   = var.domain_name != ""
  origin_host         = local.has_custom_domain ? "origin.${var.domain_name}" : ""
  default_origin_host = aws_eip.node.public_dns != "" ? aws_eip.node.public_dns : aws_instance.node.public_dns
  site_origin         = local.has_custom_domain ? "https://${var.domain_name}" : "https://${aws_cloudfront_distribution.site.domain_name}"
  domain_val          = local.has_custom_domain ? var.domain_name : aws_cloudfront_distribution.site.domain_name
  services            = ["api_gateway", "orchestrator", "ingestion", "preprocessing", "ml_inference", "event_sink", "frontend", "tools", "pipelines"]
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

#trivy:ignore:AVD-AWS-0132 SSE-S3 (AES-256) instead of a paid customer-managed KMS key
resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

# Refuse any request that is not HTTPS
resource "aws_s3_bucket_policy" "lake_tls_only" {
  bucket     = aws_s3_bucket.lake.id
  depends_on = [aws_s3_bucket_public_access_block.lake]
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Sid = "TLSOnly", Effect = "Deny", Principal = "*",
    Action = "s3:*", Resource = [aws_s3_bucket.lake.arn, "${aws_s3_bucket.lake.arn}/*"],
  Condition = { Bool = { "aws:SecureTransport" = "false" } } }] })
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
    allowed_origins = [local.site_origin]
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
  policy = jsonencode({ rules = [{ rulePriority = 1, description = "keep 5 images",
    selection = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 5 },
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
resource "random_password" "origin" { # CloudFront -> origin shared secret (X-Origin-Verify)
  length  = 48
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
    ORIGIN_SECRET   = random_password.origin.result
  }
  name  = "/hydranet/${var.env}/${each.key}"
  type  = "SecureString"
  value = each.value
}

# Values only you have. Terraform creates them as CHANGE_ME and never overwrites them,
# so they stay out of Terraform state. Set each one (deploy.sh refuses while any is CHANGE_ME):
#   aws ssm put-parameter --overwrite --type SecureString --name /hydranet/prod/GOOGLE_CLIENT_SECRET --value '...'
resource "aws_ssm_parameter" "manual" {
  for_each = {
    GOOGLE_CLIENT_ID     = "String"
    GOOGLE_CLIENT_SECRET = "SecureString"
    EARTHDATA_TOKEN      = "SecureString" # optional: platform NASA token for batch jobs ("unset" = none)
  }
  name  = "/hydranet/${var.env}/${each.key}"
  type  = each.value
  value = "CHANGE_ME"
  lifecycle { ignore_changes = [value] }
}

resource "aws_ssm_parameter" "config" {
  for_each = {
    S3_BUCKET             = aws_s3_bucket.lake.bucket
    S3_REGION             = var.region
    DOMAIN                = local.domain_val
    ORIGIN_HOST           = local.origin_host
    PUBLIC_ORIGIN         = local.site_origin
    ADMIN_EMAIL           = var.admin_email
    ALLOWED_EMAILS        = join(",", var.allowed_emails)
    ALLOWED_EMAIL_DOMAINS = join(",", var.allowed_email_domains)
    REGISTRY              = "${data.aws_caller_identity.me.account_id}.dkr.ecr.${var.region}.amazonaws.com"
  }
  name  = "/hydranet/${var.env}/${each.key}"
  type  = "String"
  value = each.value == "" ? "-" : each.value # SSM refuses empty values; deploy.sh maps "-" back to empty
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
  policy = jsonencode({ Version = "2012-10-17", Statement = concat([
    { Effect = "Allow", Action = ["s3:ListBucket", "s3:GetBucketLocation"], Resource = aws_s3_bucket.lake.arn },
    { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"],
    Resource = "${aws_s3_bucket.lake.arn}/*" },
    { Effect = "Allow", Action = ["ssm:GetParameter", "ssm:GetParametersByPath"],
    Resource = "arn:aws:ssm:${var.region}:${data.aws_caller_identity.me.account_id}:parameter/hydranet/${var.env}/*" },
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"],
    Resource = "${aws_cloudwatch_log_group.app.arn}:*" },
    ], local.has_custom_domain ? [
    # certbot (scripts/origin_cert.sh): DNS-01 challenge for the origin certificate
    { Effect = "Allow", Action = ["route53:ListHostedZones", "route53:GetChange"], Resource = "*" },
    { Effect = "Allow", Action = ["route53:ChangeResourceRecordSets"], Resource = data.aws_route53_zone.site[0].arn },
  ] : []) })
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/hydranet/${var.env}"
  retention_in_days = var.log_retention_days
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
  description = "Traffic from CloudFront only; no SSH (use SSM Session Manager)"
  vpc_id      = data.aws_vpc.default.id
  # ONE port only: the CloudFront prefix list counts as ~55 rules, and a group holds 60.
  # Own domain: HTTPS (443). No domain: HTTP (80), CloudFront's default domain cannot cover the server.
  ingress {
    description     = "From CloudFront edge locations only"
    from_port       = local.has_custom_domain ? 443 : 80
    to_port         = local.has_custom_domain ? 443 : 80
    protocol        = "tcp"
    prefix_list_ids = [data.aws_ec2_managed_prefix_list.cloudfront.id]
  }
  egress { # HTTPS out only: NASA, Google, ECR, S3, SSM, Let's Encrypt, GitHub (DNS/NTP/metadata are link-local)
    description = "HTTPS to the internet"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"] #trivy:ignore:AVD-AWS-0104 NASA/Google/AWS endpoints have no fixed IP ranges
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
  user_data = templatefile("${path.module}/user_data.sh", { env = var.env, region = var.region,
  domain = var.domain_name, origin_host = local.origin_host, le_email = var.alert_email, swap_gb = var.swap_gb })
  user_data_replace_on_change = false
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 2 # containers reach the instance role through IMDSv2
  }
  root_block_device {
    volume_size = var.volume_gb
    volume_type = "gp3"
    encrypted   = true
  }
  tags = { Name = "hydranet-${var.env}", Backup = "daily" }
  lifecycle { ignore_changes = [ami, user_data] } # a newer AMI must not silently replace the server
}

# Fixed public address: the instance stops at night, and its automatic IP would change on every start.
resource "aws_eip" "node" {
  domain = "vpc"
  tags   = { Name = "hydranet-${var.env}" }
}

resource "aws_eip_association" "node" {
  instance_id   = aws_instance.node.id
  allocation_id = aws_eip.node.id
}

# ── DNS + certificates (used when custom domain is configured) ───────────────
data "aws_route53_zone" "site" {
  count        = local.has_custom_domain ? 1 : 0
  name         = var.route53_zone_name
  private_zone = false
}

resource "aws_route53_record" "origin" {
  count   = local.has_custom_domain ? 1 : 0
  zone_id = data.aws_route53_zone.site[0].zone_id
  name    = local.origin_host
  type    = "A"
  ttl     = 300
  records = [aws_eip.node.public_ip]
}

resource "aws_acm_certificate" "site" { # CloudFront certificates must live in us-east-1
  count             = local.has_custom_domain ? 1 : 0
  provider          = aws.us_east_1
  domain_name       = var.domain_name
  validation_method = "DNS"
  lifecycle { create_before_destroy = true }
}

resource "aws_route53_record" "acm" {
  for_each = local.has_custom_domain ? { for o in aws_acm_certificate.site[0].domain_validation_options : o.domain_name => o } : {}
  zone_id  = data.aws_route53_zone.site[0].zone_id
  name     = each.value.resource_record_name
  type     = each.value.resource_record_type
  records  = [each.value.resource_record_value]
  ttl      = 300
}

resource "aws_acm_certificate_validation" "site" {
  count                   = local.has_custom_domain ? 1 : 0
  provider                = aws.us_east_1
  certificate_arn         = aws_acm_certificate.site[0].arn
  validation_record_fqdns = [for r in aws_route53_record.acm : r.fqdn]
}

resource "aws_route53_record" "site" {
  for_each = local.has_custom_domain ? toset(["A", "AAAA"]) : toset([])
  zone_id  = data.aws_route53_zone.site[0].zone_id
  name     = var.domain_name
  type     = each.key
  alias {
    name                   = aws_cloudfront_distribution.site.domain_name
    zone_id                = aws_cloudfront_distribution.site.hosted_zone_id
    evaluate_target_health = false
  }
}

# ── Daily EBS snapshots (the database lives on this disk), 7 kept ────────────
resource "aws_iam_role" "dlm" {
  name = "hydranet-${var.env}-dlm"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow",
  Principal = { Service = "dlm.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}

resource "aws_iam_role_policy_attachment" "dlm" {
  role       = aws_iam_role.dlm.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSDataLifecycleManagerServiceRole"
}

resource "aws_dlm_lifecycle_policy" "snapshots" {
  description        = "hydranet-${var.env} daily snapshots"
  execution_role_arn = aws_iam_role.dlm.arn
  state              = "ENABLED"
  policy_details {
    resource_types = ["INSTANCE"]
    target_tags    = { Backup = "daily" }
    schedule {
      name      = "daily"
      copy_tags = true
      create_rule {
        interval      = 24
        interval_unit = "HOURS"
        times         = ["19:00"] # UTC = noon Pacific, while the instance is running
      }
      retain_rule { count = 7 }
    }
  }
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
  aliases         = local.has_custom_domain ? [var.domain_name] : []
  origin {
    domain_name = local.has_custom_domain ? local.origin_host : local.default_origin_host
    origin_id   = "node"
    custom_header {
      name  = "X-Origin-Verify"
      value = random_password.origin.result
    }
    custom_origin_config {
      http_port                = 80
      https_port               = 443
      origin_protocol_policy   = local.has_custom_domain ? "https-only" : "http-only"
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
  web_acl_id = var.enable_waf ? aws_wafv2_web_acl.site[0].arn : null #trivy:ignore:AVD-AWS-0011 WAF is optional (cost); see enable_waf
  viewer_certificate {
    cloudfront_default_certificate = local.has_custom_domain ? false : true
    acm_certificate_arn            = local.has_custom_domain ? aws_acm_certificate_validation.site[0].certificate_arn : null
    ssl_support_method             = local.has_custom_domain ? "sni-only" : null
    minimum_protocol_version       = local.has_custom_domain ? "TLSv1.2_2021" : "TLSv1"
  }
  depends_on = [aws_eip_association.node]
}

# ── Optional WAF in front of CloudFront (~$8-10/month): AWS common rules + per-IP rate limit
resource "aws_wafv2_web_acl" "site" {
  count    = var.enable_waf ? 1 : 0
  provider = aws.us_east_1
  name     = "hydranet-${var.env}"
  scope    = "CLOUDFRONT"
  default_action {
    allow {}
  }
  rule {
    name     = "aws-common"
    priority = 1
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesCommonRuleSet"
        rule_action_override { # map files and job bodies are legitimately large
          name = "SizeRestrictions_BODY"
          action_to_use {
            count {}
          }
        }
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "aws-common"
      sampled_requests_enabled   = true
    }
  }
  rule {
    name     = "per-ip-rate"
    priority = 2
    action {
      block {}
    }
    statement {
      rate_based_statement {
        limit              = 2000 # requests per 5 minutes per IP
        aggregate_key_type = "IP"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "per-ip-rate"
      sampled_requests_enabled   = true
    }
  }
  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "hydranet"
    sampled_requests_enabled   = true
  }
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
    enforce_workgroup_configuration = true
    result_configuration {
      output_location = "s3://${local.bucket}/ops/athena/"
      encryption_configuration { encryption_option = "SSE_S3" }
    }
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
  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1b51337f2181470704d52450ec84732706f72749"
  ]
}

resource "aws_iam_role" "deploy" {
  name = "hydranet-${var.env}-deploy"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow",
    Principal = { Federated = aws_iam_openid_connect_provider.github.arn }, Action = "sts:AssumeRoleWithWebIdentity",
    Condition = {
      StringEquals = { "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com" },
      StringLike   = { "token.actions.githubusercontent.com:sub" = "repo:${var.github_repo}:*" }
    } }] })
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
