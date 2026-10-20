output "site_url" { value = "https://${aws_cloudfront_distribution.site.domain_name}" }
output "bucket" { value = aws_s3_bucket.lake.bucket }
output "instance_id" { value = aws_instance.node.id }
output "registry" { value = "${data.aws_caller_identity.me.account_id}.dkr.ecr.${var.region}.amazonaws.com" }
output "deploy_role_arn" { value = aws_iam_role.deploy.arn }
output "admin_password_ssm" { value = "/hydranet/${var.env}/ADMIN_PASSWORD" }
