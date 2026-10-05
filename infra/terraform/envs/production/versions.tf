terraform {
  required_version = ">= 1.9"

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.26"
    }
  }

  # Remote state on R2 (S3-compatible backend). State holds token VALUES in plain text,
  # so the state bucket must be private, separate from data buckets, and readable only
  # by the operators/CI that run Terraform. Bootstrap once by hand:
  #   1. Create bucket "pv-tfstate" (dashboard or `wrangler r2 bucket create pv-tfstate`).
  #   2. Create an R2 token with Object Read & Write on that bucket only; export
  #      AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY from it (never commit them).
  #   3. Uncomment the block, replace <ACCOUNT_ID>, run `terraform init -migrate-state`.
  # use_lockfile needs Terraform >= 1.10 (S3-native locking via conditional writes).
  #
  # backend "s3" {
  #   bucket                      = "pv-tfstate"
  #   key                         = "production/r2.tfstate"
  #   region                      = "auto"
  #   endpoints                   = { s3 = "https://<ACCOUNT_ID>.r2.cloudflarestorage.com" }
  #   use_path_style              = true
  #   skip_credentials_validation = true
  #   skip_region_validation      = true
  #   skip_requesting_account_id  = true
  #   skip_metadata_api_check     = true
  #   skip_s3_checksum            = true
  #   use_lockfile                = true
  # }
}
