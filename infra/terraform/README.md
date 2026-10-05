# PitchVision infrastructure: Cloudflare R2 (M1-OPS-04)

Terraform for the object storage described in AD 9.1, 9.2, 9.5 and 14: per-region buckets,
CORS for browser multipart upload, lifecycle rules and per-role, bucket-scoped API tokens.

```
infra/terraform/
  modules/r2-region/        one data region: data, models and backup buckets, CORS, lifecycle, tokens
    tests/                  offline `terraform test` (mocked provider, no credentials)
  envs/staging/             root module: EU + US regions, bucket prefix "pv-stg"
  envs/production/          root module: EU + US regions, bucket prefix "pv"
```

| Region | Buckets (production) | Placement | S3 endpoint |
|---|---|---|---|
| eu | `pv-eu-data`, `pv-eu-models`, `pv-eu-backup` | `jurisdiction = "eu"` | `https://<account>.eu.r2.cloudflarestorage.com` |
| us | `pv-us-data`, `pv-us-models`, `pv-us-backup` | location hint `enam` | `https://<account>.r2.cloudflarestorage.com` |

Staging uses the same layout with the `pv-stg-` prefix (`pv-stg-eu-data`, ...).

Versions: Terraform >= 1.9, `cloudflare/cloudflare` ~> 5.26 (validated with v5.26.0).

## Prerequisites

1. **Cloudflare account ID** (dashboard, right sidebar of any zone, or R2 overview). R2 must be purchased/enabled on the account.
2. **Bootstrap API token, created by hand** (dashboard > Manage account > Account API tokens, or My Profile > API Tokens).
   Terraform never creates this token. Permissions:
   - `Account > Account API Tokens > Edit`: to create the account-owned role tokens
   - `Account > Workers R2 Storage > Edit`: to create buckets and set CORS/lifecycle

   Scope it to this one account only; set an expiry and keep it out of CI logs.
3. Docker or a local `terraform` >= 1.9.

## Plan and apply

```bash
cd infra/terraform/envs/staging            # or envs/production
cp terraform.tfvars.example terraform.tfvars  # fill in account_id and web_origins
export CLOUDFLARE_API_TOKEN=...            # bootstrap token (prefix the command with a space so it stays out of shell history)
terraform init                             # add -backend=false until the remote backend is set up
terraform plan -out=r2.tfplan
terraform apply r2.tfplan
```

Docker equivalent: `docker run --rm -it -v "$PWD/../..:/tf" -w /tf/envs/staging -e CLOUDFLARE_API_TOKEN hashicorp/terraform:1.9 plan`.

Apply staging first, then production; within an environment the EU and US modules are independent.

### Remote state

State is local until the commented `backend "s3"` block in `envs/*/versions.tf` is enabled.
That block stores state in an R2 bucket (`pv-tfstate`) through the S3-compatible backend. The steps are
in the comment. **State contains every token value in plain text**, so:

- the state bucket is separate from the data buckets, private, and readable only by the people and CI jobs that run Terraform;
- its credentials are a separate R2 token (Object Read & Write on `pv-tfstate` only), not one of the role tokens below.

## Tokens and S3 credentials

Each region gets three **account-owned** tokens (`cloudflare_account_token`). None of them has
account-level R2 admin rights:

| Role | Name | Buckets | Permission group |
|---|---|---|---|
| `api` | `pv-<env>-<region>-r2-api` | data | Workers R2 Storage Bucket Item Write (signs presigned PUT/GET, TTL 15 min) |
| `worker` | `pv-<env>-<region>-r2-worker` | data, models | Workers R2 Storage Bucket Item Write |
| `backup` | `pv-<env>-<region>-r2-backup` | data (read), backup (write) | Bucket Item Read + Bucket Item Write |
| tile Worker | not a token | data (read) | read-only R2 binding in the Worker's wrangler config |

Permission group IDs are looked up by name with `cloudflare_account_api_token_permission_groups_list`.
The token resource is `com.cloudflare.edge.r2.bucket.<account>_<jurisdiction>_<bucket>`.

**How the S3 credentials are derived** (Cloudflare R2 docs, "Get S3 API credentials from an API token"):

- `AWS_ACCESS_KEY_ID` = the token `id`
- `AWS_SECRET_ACCESS_KEY` = SHA-256 of the token `value`, as lowercase hex (Terraform `sha256()`)

The module computes both and returns them only in the **sensitive** output `s3_credentials`
(`{ eu = { api = { access_key_id, secret_access_key, endpoint }, ... }, us = ... }`). Send them straight
to the secret store, and never echo them:

```bash
terraform output -json s3_credentials \
  | jq -c '.eu.api' \
  | aws secretsmanager put-secret-value --secret-id pv/staging/eu/r2-api --secret-string file:///dev/stdin
```

- Kubernetes: AWS Secrets Manager -> External Secrets -> `STORAGE_ENDPOINT`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` (AD 14).
- Self-host: SeaweedFS/MinIO, not R2. If a self-hosted deployment does use R2, put the values in `.env` with `chmod 600`.
- Never run `terraform output s3_credentials` without `-json` piped into a sink in CI, and never `terraform show` in CI logs.

Rotation: `terraform apply -replace='module.r2_eu.cloudflare_account_token.role["api"]'`, push the new secret, then roll the pods.
You can also set `token_expires_on` so tokens expire.

Presigned URLs must be signed for the `s3_endpoint` host. Custom domains do not work for presigned
S3 URLs (SP-07). EU buckets are only reachable on the `.eu.` endpoint.

## Lifecycle and CORS (AD 9.5)

| Bucket | Rule |
|---|---|
| all | abort incomplete multipart uploads after 7 days (restated explicitly: the lifecycle resource replaces R2's built-in default rule) |
| data | `source/`, `segments/` -> Infrequent Access after 30 days |
| data | `segments/` deleted after 180 days |
| backup | default storage class Infrequent Access, no expiry |

CORS applies to the data bucket only: origins = `web_origins`, methods PUT/GET/HEAD, `AllowedHeaders ["*"]`,
`ExposeHeaders ["ETag"]` (required for the browser to complete a multipart upload, SP-07), MaxAge 3600.
Uploads must use equal-size parts of at least 5 MiB, except the last one (SP-07).

## Gaps: AD config the provider cannot express

1. **Lifecycle prefixes do not match the AD 9.2 key layout.** R2 lifecycle filters match key *prefixes* only.
   Keys are `orgs/{org_id}/videos/{video_id}/source/...` and `.../jobs/{job_id}/segments/...`, so the AD
   literal prefixes `source/` and `segments/` match nothing. The rules are deployed as the AD states them
   (variables `ia_transition_prefixes` and `expire_prefixes`), but they stay inactive until one of these is decided:
   - (recommended) put the storage class first in the key, e.g. `source/orgs/{org}/videos/{video}/original.mp4`
     and `segments/orgs/{org}/videos/{video}/jobs/{job}/{i}/...`. This changes `pitchvision_schemas` key builders.
   - or drop bucket lifecycle for these classes and run an app-level sweeper (cron) that transitions or deletes by age.
2. **Plan-dependent retention** (AD 9.1: segments deleted after 90 days on the free plan, 180 otherwise) cannot be
   expressed per org in bucket lifecycle. It needs an app-level deletion job, or a plan segment in the key prefix.
3. **Daily backup copy of `source/`** is not an R2 feature. The module creates the `backup` bucket and a
   `backup` token (read data, write backup). The copy itself needs a job, for example:
   - a Kubernetes CronJob per region (daily) running `rclone copy --max-age 48h r2data:pv-eu-data r2backup:pv-eu-backup --include "**/source/**"`
     with the `backup` credentials and the jurisdiction endpoint. Copy only, never `sync`, so deletes do not propagate by accident;
   - or a Cloudflare Worker with a cron trigger and two R2 bindings (`jurisdiction = "eu"`) that lists keys by
     `uploaded` date. This is fine for small daily volumes, but Worker CPU/subrequest limits make rclone the safer default for multi-GB videos.
4. **Erasure requests** (AD 14: within 24 h) must also delete from the backup bucket. The deletion path therefore needs
   write access to `backup` (extend the `worker` token or run the deletion via the backup job).
5. **Tile Worker binding**: it is configured in the Worker's wrangler config, not here.

## Spike bucket

`pitchvision-spike` (APAC location, created by hand for SP-07, token expires 2026-11-04) is **not** managed by
Terraform and is not imported. Staging creates new `pv-stg-*` buckets. Delete the spike bucket by hand when SP-07
artifacts are no longer needed.

## Cost notes

- Infrequent Access: lower storage price, but **per-GB retrieval fees** and a **30-day minimum storage charge**.
  Use it only for data that is rarely read again (source after normalisation, segments after the job). Tiles and
  exports stay Standard because browsers read them.
- Lifecycle transitions to IA are billed as Class A operations.
- Egress from R2 is free; Class A/B operations dominate for many small tile objects.

## EU jurisdiction caveat

The jurisdiction is **fixed when the bucket is created**. A bucket cannot be moved into or out of `eu`. Changing
`jurisdiction` or the bucket name in Terraform plans a destroy + create, which deletes all customer data.
Buckets therefore have `prevent_destroy = true`. The location hint is also only honoured on first creation, and
it is best-effort. EU buckets must be addressed with the `jurisdiction` argument in every API call and with the
`.eu.` S3 endpoint.

## Validation (no credentials needed)

```bash
docker run --rm -v "$PWD:/tf" -w /tf hashicorp/terraform:1.9 fmt -check -recursive
docker run --rm -v "$PWD:/tf" -w /tf/modules/r2-region hashicorp/terraform:1.9 init -backend=false
docker run --rm -v "$PWD:/tf" -w /tf/modules/r2-region hashicorp/terraform:1.9 test
docker run --rm -v "$PWD:/tf" -w /tf/envs/staging hashicorp/terraform:1.9 init -backend=false
docker run --rm -v "$PWD:/tf" -w /tf/envs/staging hashicorp/terraform:1.9 validate
docker run --rm -v "$PWD:/tf" -w /tf/envs/staging ghcr.io/terraform-linters/tflint --config=/tf/.tflint.hcl
```

The committed `.terraform.lock.hcl` files have `zh:` hashes for all platforms. Run
`terraform providers lock -platform=darwin_arm64 -platform=linux_amd64` to add `h1:` hashes for other platforms.
