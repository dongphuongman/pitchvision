output "buckets" {
  description = "Bucket names per region."
  value = {
    eu = module.r2_eu.buckets
    us = module.r2_us.buckets
  }
}

output "s3_endpoints" {
  description = "STORAGE_ENDPOINT per region."
  value = {
    eu = module.r2_eu.s3_endpoint
    us = module.r2_us.s3_endpoint
  }
}

output "s3_credentials" {
  description = "S3 credentials per region and role. Sensitive: pipe `terraform output -json s3_credentials` straight into the secret store."
  value = {
    eu = module.r2_eu.s3_credentials
    us = module.r2_us.s3_credentials
  }
  sensitive = true
}
