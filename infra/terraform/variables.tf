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
  description = "Monthly budget; alerts at 25/50/80/100 %"
  type        = number
  default     = 60
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
  type = string
}
variable "earthdata_token" {
  description = "NASA Earthdata bearer token (stored in SSM as SecureString)"
  type        = string
  sensitive   = true
  default     = ""
}
