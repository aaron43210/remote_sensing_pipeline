variable "region" {
  description = "us-west-2: NASA Earthdata Cloud (EMIT) and the Landsat bucket live here"
  type        = string
  default     = "us-west-2"
}
variable "env" {
  type    = string
  default = "prod"
}
variable "name" {
  description = "Globally unique suffix for the bucket, e.g. your GitHub handle"
  type        = string
}
variable "instance_type" {
  description = "m7i-flex.large = 2 vCPU / 8 GB x86, allowed on the AWS Free plan (release.yml builds amd64 images)"
  type        = string
  default     = "m7i-flex.large"
}
variable "volume_gb" {
  type    = number
  default = 50
}
variable "budget_usd" {
  description = "Monthly budget; e-mail alerts at 25/50/80/100 %. About $40/month at the defaults (see README)."
  type        = number
  default     = 45
}
variable "alert_email" {
  type = string
}
variable "github_repo" {
  description = "owner/repo allowed to deploy through GitHub OIDC"
  type        = string
}
variable "office_hours_only" {
  description = "Run the instance only between run_start_hour and run_stop_hour (America/Los_Angeles) to save credits"
  type        = bool
  default     = true
}
variable "run_start_hour" {
  type    = number
  default = 8
}
variable "run_stop_hour" {
  type    = number
  default = 20
}
variable "run_days" {
  description = "Days the instance runs: \"*\" (every day) or \"MON-FRI\" (weekdays only, cheapest)"
  type        = string
  default     = "*"
}
variable "github_repo_ids" {
  description = "GitHub's immutable form owner@ownerId/repo@repoId (seen in CloudTrail AssumeRoleWithWebIdentity), e.g. aaron43210@225593939/remote_sensing_pipeline@1357258149"
  type        = string
  default     = ""
}
variable "admin_email" {
  description = "Google account that becomes admin on first sign-in"
  type        = string
}
variable "domain_name" {
  description = "Site domain, e.g. maps.example.com. If empty, AWS CloudFront's free default domain (*.cloudfront.net) is used."
  type        = string
  default     = ""
  validation {
    condition     = var.domain_name == "" || can(regex("^[a-z0-9.-]+\\.[a-z]{2,}$", var.domain_name))
    error_message = "domain_name must be empty or a valid domain, e.g. maps.example.com."
  }
}
variable "route53_zone_name" {
  description = "Route 53 public hosted zone that holds domain_name, e.g. example.com. Leave empty if using CloudFront's default domain."
  type        = string
  default     = ""
}
variable "allowed_emails" {
  description = "Google accounts allowed to sign in (besides admin_email)"
  type        = list(string)
  default     = []
}
variable "allowed_email_domains" {
  description = "Google Workspace domains (or gmail.com) whose accounts may sign in"
  type        = list(string)
  default     = []
}
variable "log_retention_days" {
  type    = number
  default = 14
}
variable "swap_gb" {
  description = "Swap file on the instance, so a large scene cannot crash an 8 GB t4g.large"
  type        = number
  default     = 4
}

variable "enable_waf" {
  description = "AWS WAF in front of CloudFront (common attack rules + per-IP rate limit). About $8-10/month."
  type        = bool
  default     = false
}
