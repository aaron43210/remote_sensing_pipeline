output "site_url" { value = local.site_origin }
output "google_oauth_settings" {
  description = "Paste into Google Cloud Console -> Credentials -> your OAuth client"
  value = {
    authorized_javascript_origin = local.site_origin
    authorized_redirect_uri      = "${local.site_origin}/api/auth/google/callback"
  }
}
output "bucket" { value = aws_s3_bucket.lake.bucket }
output "instance_id" { value = aws_instance.node.id }
output "origin_ip" { value = aws_eip.node.public_ip }
output "registry" { value = "${data.aws_caller_identity.me.account_id}.dkr.ecr.${var.region}.amazonaws.com" }
output "deploy_role_arn" { value = aws_iam_role.deploy.arn }
output "set_these_ssm_parameters" {
  description = "Created as CHANGE_ME; deploy.sh refuses to run until they are set"
  value       = [for k, p in aws_ssm_parameter.manual : p.name]
}
