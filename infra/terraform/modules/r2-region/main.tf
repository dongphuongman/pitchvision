# One PitchVision data region on Cloudflare R2 (AD 9.2 / 9.5):
#   <prefix>-<region>-data    videos, jobs, tiles, exports (browser access via presigned URLs -> CORS)
#   <prefix>-<region>-models  ONNX weights + TensorRT engines
#   <prefix>-<region>-backup  daily copy of source/ (same jurisdiction, no versioning)
# Buckets are never public; tiles are served by a Worker with a read-only R2 binding.

locals {
  day_seconds = 86400

  bucket_names = {
    data   = "${var.name_prefix}-${var.region}-data"
    models = "${var.name_prefix}-${var.region}-models"
    backup = "${var.name_prefix}-${var.region}-backup"
  }

  bucket_storage_class = {
    data   = "Standard"
    models = "Standard"
    backup = var.backup_storage_class
  }

  # S3 API endpoint. Jurisdiction-bound buckets are ONLY reachable on the jurisdiction
  # endpoint, and presigned URLs must be signed for that host (SP-07: presigned URLs work
  # on <account>.r2.cloudflarestorage.com, not on custom domains).
  s3_endpoint = (
    var.jurisdiction == "default"
    ? "https://${var.account_id}.r2.cloudflarestorage.com"
    : "https://${var.account_id}.${var.jurisdiction}.r2.cloudflarestorage.com"
  )

  abort_multipart_rule = {
    id      = "abort-incomplete-multipart"
    enabled = true
    conditions = {
      prefix = ""
    }
    abort_multipart_uploads_transition = {
      condition = {
        type    = "Age"
        max_age = var.abort_multipart_days * local.day_seconds
      }
    }
    storage_class_transitions = null
    delete_objects_transition = null
  }

  ia_rules = [
    for p in var.ia_transition_prefixes : {
      id      = "ia-after-${var.ia_transition_days}d-${trim(replace(p, "/", "-"), "-")}"
      enabled = true
      conditions = {
        prefix = p
      }
      storage_class_transitions = [{
        storage_class = "InfrequentAccess"
        condition = {
          type    = "Age"
          max_age = var.ia_transition_days * local.day_seconds
        }
      }]
      abort_multipart_uploads_transition = null
      delete_objects_transition          = null
    }
  ]

  expire_rules = [
    for p in var.expire_prefixes : {
      id      = "expire-after-${var.expire_days}d-${trim(replace(p, "/", "-"), "-")}"
      enabled = true
      conditions = {
        prefix = p
      }
      delete_objects_transition = {
        condition = {
          type    = "Age"
          max_age = var.expire_days * local.day_seconds
        }
      }
      abort_multipart_uploads_transition = null
      storage_class_transitions          = null
    }
  ]
}

resource "cloudflare_r2_bucket" "this" {
  for_each = local.bucket_names

  account_id    = var.account_id
  name          = each.value
  jurisdiction  = var.jurisdiction
  location      = var.location_hint
  storage_class = local.bucket_storage_class[each.key]

  lifecycle {
    # Jurisdiction and location are fixed at creation; a diff here means replacement
    # (= deleting a bucket full of customer data). Remove this guard deliberately if needed.
    prevent_destroy = true
  }
}

# --------------------------------------------------------------------------- CORS
# Only the data bucket is reached from browsers (multipart PUT + GET/HEAD of exports).
resource "cloudflare_r2_bucket_cors" "data" {
  account_id   = var.account_id
  bucket_name  = cloudflare_r2_bucket.this["data"].name
  jurisdiction = var.jurisdiction

  rules = [{
    id = "web-app"
    allowed = {
      origins = var.web_origins
      methods = ["PUT", "GET", "HEAD"]
      headers = var.cors_allowed_headers
    }
    # SP-07: the browser must read ETag of every UploadPart response to CompleteMultipartUpload.
    expose_headers  = ["ETag"]
    max_age_seconds = var.cors_max_age_seconds
  }]
}

# ---------------------------------------------------------------------- Lifecycle
# The lifecycle resource REPLACES the whole rule set, including R2's built-in
# "abort multipart after 7 days" default, so that rule is restated explicitly.
resource "cloudflare_r2_bucket_lifecycle" "data" {
  account_id   = var.account_id
  bucket_name  = cloudflare_r2_bucket.this["data"].name
  jurisdiction = var.jurisdiction

  rules = concat([local.abort_multipart_rule], local.ia_rules, local.expire_rules)
}

resource "cloudflare_r2_bucket_lifecycle" "models" {
  account_id   = var.account_id
  bucket_name  = cloudflare_r2_bucket.this["models"].name
  jurisdiction = var.jurisdiction

  rules = [local.abort_multipart_rule]
}

resource "cloudflare_r2_bucket_lifecycle" "backup" {
  account_id   = var.account_id
  bucket_name  = cloudflare_r2_bucket.this["backup"].name
  jurisdiction = var.jurisdiction

  # No expiry: backups of source/ are kept as long as the source. Erasure requests
  # (AD 14, 24h SLA) must delete from this bucket too - see README "Gaps".
  rules = [local.abort_multipart_rule]
}
