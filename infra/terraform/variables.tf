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
  description = "t4g.large = 2 vCPU / 8 GB arm64 (images are multi-arch). t3.large for x86."
  type        = string
  default     = "t4g.large"
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
  description = "Stop the instance 22:00-07:00 (America/Los_Angeles) to save credits"
  type        = bool
  default     = true
}
variable "admin_email" {
  description = "Google account that becomes admin on first sign-in"
  type        = string
}
variable "domain_name" {
  description = "Site domain, e.g. maps.example.com (required: Google sign-in and HTTPS need a real domain)"
  type        = string
  validation {
    condition     = can(regex("^[a-z0-9.-]+\\.[a-z]{2,}$", var.domain_name))
    error_message = "Set domain_name to your domain, e.g. maps.example.com."
  }
}
variable "route53_zone_name" {
  description = "Route 53 public hosted zone that holds domain_name, e.g. example.com"
  type        = string
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
