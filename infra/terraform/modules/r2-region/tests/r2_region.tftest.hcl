# Offline plan tests with a mocked provider: `terraform init && terraform test` in the module dir.
# Runs are plan-only (buckets have prevent_destroy, so apply-mode teardown would fail).
# No Cloudflare credentials or network calls to the Cloudflare API are needed.

mock_provider "cloudflare" {}

variables {
  account_id  = "0123456789abcdef0123456789abcdef"
  environment = "staging"
  web_origins = ["https://staging.app.pitchvision.ai"]

  # Mocked lists of nested objects are not supported by `terraform test` overrides
  # ("expected object type, found tuple"), so the name lookup is bypassed here.
  permission_group_ids = {
    bucket_item_read  = "read-group-id"
    bucket_item_write = "write-group-id"
  }
}

run "eu_region" {
  command = plan

  variables {
    region       = "eu"
    jurisdiction = "eu"
  }

  assert {
    condition     = output.buckets == { data = "pv-eu-data", models = "pv-eu-models", backup = "pv-eu-backup" }
    error_message = "Unexpected bucket names."
  }

  assert {
    condition     = alltrue([for b in cloudflare_r2_bucket.this : b.jurisdiction == "eu"])
    error_message = "Every EU bucket must be created with jurisdiction=eu."
  }

  assert {
    condition     = output.s3_endpoint == "https://0123456789abcdef0123456789abcdef.eu.r2.cloudflarestorage.com"
    error_message = "EU buckets must use the jurisdiction-specific S3 endpoint."
  }

  assert {
    condition     = cloudflare_r2_bucket_cors.data.rules[0].expose_headers == tolist(["ETag"]) && cloudflare_r2_bucket_cors.data.rules[0].max_age_seconds == 3600
    error_message = "CORS must expose ETag with MaxAge 3600."
  }

  assert {
    condition     = toset(cloudflare_r2_bucket_cors.data.rules[0].allowed.methods) == toset(["PUT", "GET", "HEAD"])
    error_message = "CORS methods must be PUT, GET, HEAD."
  }

  assert {
    condition     = length(cloudflare_r2_bucket_lifecycle.data.rules) == 4
    error_message = "Data bucket needs abort-multipart + 2 IA + 1 expire rules."
  }

  assert {
    condition     = cloudflare_r2_bucket_lifecycle.data.rules[0].abort_multipart_uploads_transition.condition.max_age == 604800
    error_message = "Incomplete multipart uploads must be aborted after 7 days."
  }

  assert {
    condition     = cloudflare_r2_bucket_lifecycle.data.rules[3].conditions.prefix == "segments/" && cloudflare_r2_bucket_lifecycle.data.rules[3].delete_objects_transition.condition.max_age == 15552000
    error_message = "segments/ must expire after 180 days."
  }

  assert {
    condition     = jsondecode(cloudflare_account_token.role["api"].policies[0].resources) == { "com.cloudflare.edge.r2.bucket.0123456789abcdef0123456789abcdef_eu_pv-eu-data" = "*" }
    error_message = "api token must be scoped to the EU data bucket only."
  }

  assert {
    condition     = length(keys(jsondecode(cloudflare_account_token.role["worker"].policies[0].resources))) == 2
    error_message = "worker token must cover data + models."
  }

  assert {
    condition     = cloudflare_account_token.role["backup"].policies[0].permission_groups[0].id == "read-group-id"
    error_message = "backup token must only read the data bucket."
  }
}

run "us_region" {
  command = plan

  variables {
    region        = "us"
    location_hint = "enam"
    name_prefix   = "pv-stg"
  }

  assert {
    condition     = cloudflare_r2_bucket.this["data"].name == "pv-stg-us-data" && cloudflare_r2_bucket.this["data"].location == "enam"
    error_message = "US data bucket must be pv-stg-us-data with location hint enam."
  }

  assert {
    condition     = output.s3_endpoint == "https://0123456789abcdef0123456789abcdef.r2.cloudflarestorage.com"
    error_message = "Default-jurisdiction buckets use the global S3 endpoint."
  }
}

run "rejects_bad_origin" {
  command = plan

  variables {
    region      = "eu"
    web_origins = ["https://app.pitchvision.ai/path"]
  }

  expect_failures = [var.web_origins]
}
