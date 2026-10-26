# One-time: the private, versioned, encrypted bucket that holds Terraform state.
#   cd infra/terraform/bootstrap && terraform init && terraform apply -var name=<same name as tfvars>
terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.70" }
  }
}

variable "name" { type = string }
variable "region" {
  type    = string
  default = "us-west-2"
}

provider "aws" { region = var.region }

resource "aws_s3_bucket" "state" {
  bucket = "hydranet-tfstate-${var.name}"
  lifecycle { prevent_destroy = true }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration { status = "Enabled" }
}

#trivy:ignore:AVD-AWS-0132 SSE-S3 (AES-256) instead of a paid customer-managed KMS key
resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket     = aws_s3_bucket.state.id
  depends_on = [aws_s3_bucket_public_access_block.state]
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Sid = "TLSOnly", Effect = "Deny", Principal = "*",
    Action = "s3:*", Resource = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"],
  Condition = { Bool = { "aws:SecureTransport" = "false" } } }] })
}

output "state_bucket" { value = aws_s3_bucket.state.bucket }
