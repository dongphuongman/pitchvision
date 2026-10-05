# Authenticates with CLOUDFLARE_API_TOKEN from the environment (bootstrap token, see README).
provider "cloudflare" {}

locals {
  environment = "staging"
  name_prefix = "pv-stg" # pv-stg-eu-data ... keeps staging apart from production in one account
}

module "r2_eu" {
  source = "../../modules/r2-region"

  account_id   = var.account_id
  environment  = local.environment
  region       = "eu"
  jurisdiction = "eu" # fixed at creation (ADR-011 / GDPR)
  name_prefix  = local.name_prefix
  web_origins  = var.web_origins

  token_expires_on    = var.token_expires_on
  token_allowed_cidrs = var.token_allowed_cidrs
}

module "r2_us" {
  source = "../../modules/r2-region"

  account_id    = var.account_id
  environment   = local.environment
  region        = "us"
  jurisdiction  = "default"
  location_hint = "enam"
  name_prefix   = local.name_prefix
  web_origins   = var.web_origins

  token_expires_on    = var.token_expires_on
  token_allowed_cidrs = var.token_allowed_cidrs
}
