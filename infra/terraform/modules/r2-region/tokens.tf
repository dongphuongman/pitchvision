# Account-owned API tokens, each scoped to specific buckets (AD 9.5 / AD 14).
# No token gets account-level R2 admin ("Workers R2 Storage Write") - only object access.
#
#   role    bucket scope                         permission group
#   api     data                                 Bucket Item Write (signs presigned PUT/GET; the
#                                                 signature only carries the signer's rights)
#   worker  data, models                         Bucket Item Write
#   backup  data (read), backup (write)          Bucket Item Read + Bucket Item Write
#   tile Worker: NOT a token - read-only R2 binding in its wrangler config.
#
# S3 credentials derived from a token (Cloudflare R2 docs, "Get S3 API credentials from an API token"):
#   AWS_ACCESS_KEY_ID     = token id
#   AWS_SECRET_ACCESS_KEY = sha256(token value), lowercase hex

data "cloudflare_account_api_token_permission_groups_list" "bucket_item_write" {
  count = local.lookup_groups ? 1 : 0

  account_id = var.account_id
  name       = "Workers%20R2%20Storage%20Bucket%20Item%20Write" # must be URL-encoded

  lifecycle {
    postcondition {
      condition     = length(self.result) == 1
      error_message = "Expected exactly one permission group named \"Workers R2 Storage Bucket Item Write\"."
    }
  }
}

data "cloudflare_account_api_token_permission_groups_list" "bucket_item_read" {
  count = local.lookup_groups ? 1 : 0

  account_id = var.account_id
  name       = "Workers%20R2%20Storage%20Bucket%20Item%20Read"

  lifecycle {
    postcondition {
      condition     = length(self.result) == 1
      error_message = "Expected exactly one permission group named \"Workers R2 Storage Bucket Item Read\"."
    }
  }
}

locals {
  lookup_groups = var.create_tokens && var.permission_group_ids == null

  # Bucket resource key: com.cloudflare.edge.r2.bucket.<account>_<jurisdiction>_<bucket>
  bucket_resource = {
    for k, name in local.bucket_names :
    k => "com.cloudflare.edge.r2.bucket.${var.account_id}_${var.jurisdiction}_${name}"
  }

  write_group_id = (
    var.permission_group_ids != null ? var.permission_group_ids.bucket_item_write
    : local.lookup_groups ? data.cloudflare_account_api_token_permission_groups_list.bucket_item_write[0].result[0].id : null
  )
  read_group_id = (
    var.permission_group_ids != null ? var.permission_group_ids.bucket_item_read
    : local.lookup_groups ? data.cloudflare_account_api_token_permission_groups_list.bucket_item_read[0].result[0].id : null
  )

  # role => list of { group, buckets }
  token_roles = {
    api = [
      { group = local.write_group_id, buckets = ["data"] },
    ]
    worker = [
      { group = local.write_group_id, buckets = ["data", "models"] },
    ]
    backup = [
      { group = local.read_group_id, buckets = ["data"] },
      { group = local.write_group_id, buckets = ["backup"] },
    ]
  }
}

resource "cloudflare_account_token" "role" {
  for_each = var.create_tokens ? local.token_roles : {}

  account_id = var.account_id
  name       = "pv-${var.environment}-${var.region}-r2-${each.key}"
  expires_on = var.token_expires_on

  policies = [
    for p in each.value : {
      effect            = "allow"
      permission_groups = [{ id = p.group }]
      resources         = jsonencode({ for b in p.buckets : local.bucket_resource[b] => "*" })
    }
  ]

  condition = length(var.token_allowed_cidrs) > 0 ? {
    request_ip = {
      in     = var.token_allowed_cidrs
      not_in = null
    }
  } : null
}
