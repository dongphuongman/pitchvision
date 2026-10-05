variable "account_id" {
  description = "Cloudflare account ID that owns the buckets and the account-owned tokens."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{32}$", var.account_id))
    error_message = "account_id must be a 32-character lowercase hex Cloudflare account ID."
  }
}

variable "environment" {
  description = "Deployment environment. Used in token names and outputs."
  type        = string

  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "environment must be \"staging\" or \"production\"."
  }
}

variable "region" {
  description = "PitchVision data region code (AD 9.2: bucket names are pv-{region}-*)."
  type        = string

  validation {
    condition     = contains(["eu", "us"], var.region)
    error_message = "region must be \"eu\" or \"us\"."
  }
}

variable "jurisdiction" {
  description = <<-EOT
    R2 jurisdiction. "eu" guarantees storage/processing inside the EU (ADR-011 / GDPR).
    FIXED AT CREATION: changing it forces bucket replacement (destroy + create = data loss).
    Use "default" for buckets placed with a location hint instead.
  EOT
  type        = string
  default     = "default"

  validation {
    condition     = contains(["default", "eu", "fedramp", "us"], var.jurisdiction)
    error_message = "jurisdiction must be one of default, eu, fedramp, us."
  }
}

variable "location_hint" {
  description = "Best-effort location hint (apac, eeur, enam, weur, wnam, oc). Only honoured on first creation. Leave null for jurisdiction-bound buckets."
  type        = string
  default     = null

  validation {
    condition     = var.location_hint == null || contains(["apac", "eeur", "enam", "weur", "wnam", "oc"], coalesce(var.location_hint, "x"))
    error_message = "location_hint must be null or one of apac, eeur, enam, weur, wnam, oc."
  }
}

variable "name_prefix" {
  description = "Bucket name prefix. Production uses \"pv\" (pv-eu-data); staging uses a distinct prefix so both environments can share one account."
  type        = string
  default     = "pv"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{0,40}$", var.name_prefix))
    error_message = "name_prefix must be lowercase alphanumerics/hyphens (R2 bucket naming)."
  }
}

# ---------------------------------------------------------------- CORS (AD 9.5)

variable "web_origins" {
  description = "Browser origins allowed to PUT/GET/HEAD objects on the data bucket (presigned multipart upload, SP-07)."
  type        = list(string)

  validation {
    condition     = length(var.web_origins) > 0 && alltrue([for o in var.web_origins : can(regex("^https?://[^/*]+$", o))])
    error_message = "web_origins must be a non-empty list of scheme://host[:port] origins without a path or wildcard."
  }
}

variable "cors_allowed_headers" {
  description = "Access-Control-Allow-Headers for the data bucket. [\"*\"] matches what SP-07 validated on real R2."
  type        = list(string)
  default     = ["*"]
}

variable "cors_max_age_seconds" {
  description = "Preflight cache duration (AD 9.5: 3600)."
  type        = number
  default     = 3600
}

# ----------------------------------------------------------- Lifecycle (AD 9.5)

variable "abort_multipart_days" {
  description = "Abort incomplete multipart uploads after N days (applied to every bucket)."
  type        = number
  default     = 7
}

variable "ia_transition_days" {
  description = "Move objects under ia_transition_prefixes to Infrequent Access after N days. IA has a 30-day minimum storage charge and per-GB retrieval fees."
  type        = number
  default     = 30

  validation {
    condition     = var.ia_transition_days >= 30
    error_message = "ia_transition_days below 30 only adds IA minimum-duration charges; use >= 30."
  }
}

variable "ia_transition_prefixes" {
  description = <<-EOT
    Key prefixes moved to Infrequent Access. R2 lifecycle filters match KEY PREFIXES ONLY, so
    object keys put the storage class first (pitchvision_schemas.keys, AD 9.2 revised 05/10/2026):
    source/orgs/{org}/videos/{video}/original.mp4, segments/orgs/{org}/videos/{video}/jobs/...
  EOT
  type        = list(string)
  default     = ["source/", "segments/"]
}

variable "expire_days" {
  description = "Delete objects under expire_prefixes after N days (AD 9.5: 180)."
  type        = number
  default     = 180
}

variable "expire_prefixes" {
  description = "Key prefixes deleted after expire_days. Same prefix-only caveat as ia_transition_prefixes."
  type        = list(string)
  default     = ["segments/"]
}

variable "backup_storage_class" {
  description = "Default storage class of the backup bucket. Backups are write-mostly, so Infrequent Access is cheaper unless restores are frequent."
  type        = string
  default     = "InfrequentAccess"

  validation {
    condition     = contains(["Standard", "InfrequentAccess"], var.backup_storage_class)
    error_message = "backup_storage_class must be Standard or InfrequentAccess."
  }
}

# --------------------------------------------------------------------- Tokens

variable "create_tokens" {
  description = "Create the account-owned, bucket-scoped API tokens (api, worker, backup)."
  type        = bool
  default     = true
}

variable "token_expires_on" {
  description = "Optional RFC 3339 expiry for all tokens of this region (e.g. 2027-10-01T00:00:00Z). null = no expiry; rotate by tainting/replacing the token resource."
  type        = string
  default     = null
}

variable "token_allowed_cidrs" {
  description = "Optional client IP allowlist (CIDRs) applied to every token. Empty = no IP condition."
  type        = list(string)
  default     = []
}

variable "permission_group_ids" {
  description = "Optional pre-resolved permission group IDs. null = look them up by name with the cloudflare_account_api_token_permission_groups_list data source (normal use); set in offline tests."
  type = object({
    bucket_item_read  = string
    bucket_item_write = string
  })
  default = null
}
