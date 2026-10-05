output "region" {
  description = "Region code (eu/us)."
  value       = var.region
}

output "jurisdiction" {
  description = "R2 jurisdiction of every bucket in this region."
  value       = var.jurisdiction
}

output "buckets" {
  description = "Bucket names by purpose (data, models, backup)."
  value       = { for k, b in cloudflare_r2_bucket.this : k => b.name }
}

output "s3_endpoint" {
  description = "STORAGE_ENDPOINT for boto3 (region_name=\"auto\", SigV4). EU buckets are only reachable on the .eu. endpoint; presigned URLs must be signed for this host."
  value       = local.s3_endpoint
}

output "token_ids" {
  description = "Token IDs by role (= S3 access key IDs). Not secret, but treat as sensitive metadata."
  value       = { for role, t in cloudflare_account_token.role : role => t.id }
  sensitive   = true
}

output "s3_credentials" {
  description = <<-EOT
    S3 credentials by role: access_key_id = token id, secret_access_key = sha256(token value).
    Read once with `terraform output -json` and push to the secret store (AWS Secrets Manager ->
    External Secrets; self-host .env with mode 600). Never print in CI logs.
  EOT
  value = {
    for role, t in cloudflare_account_token.role : role => {
      access_key_id     = t.id
      secret_access_key = sha256(t.value)
      endpoint          = local.s3_endpoint
    }
  }
  sensitive = true
}
