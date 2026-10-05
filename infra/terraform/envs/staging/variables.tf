variable "account_id" {
  description = "Cloudflare account ID."
  type        = string
}

variable "web_origins" {
  description = "Web app origins allowed by the data buckets' CORS rule."
  type        = list(string)
}

variable "token_expires_on" {
  description = "Optional RFC 3339 expiry for every R2 token in this environment."
  type        = string
  default     = null
}

variable "token_allowed_cidrs" {
  description = "Optional client IP allowlist for every R2 token in this environment."
  type        = list(string)
  default     = []
}
