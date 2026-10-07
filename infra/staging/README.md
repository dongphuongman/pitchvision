# Staging (M1-OPS-07 / 08)

One VM runs the whole stack with Docker Compose: the images CI pushes to GHCR, Caddy for HTTPS, SeaweedFS (or R2)
for objects, Mailpit for email, Prometheus + Alertmanager for alerts, Sentry when a DSN is set. It is the self-host
layout of `infra/compose` plus `docker-compose.staging.yml`; the AD §13.2 cloud layout (k8s, Argo CD) comes later.

```
internet ──443──► caddy ─┬─ <site>          → web (nginx: SPA, /api → api, identity paths → identity)
                         ├─ s3.<site>       → storage (presigned uploads / media)
                         └─ mail.<site>     → mailpit UI (basic auth)
127.0.0.1 only: postgres, redis, api :8000, identity :8001, prometheus :9090, alertmanager :9093
workers: orchestrator, cpu-worker (also runs the GPU stages, slowly), ingest-worker
```

## No domain yet

Use `<public-ip-with-dashes>.sslip.io` as `PV_SITE` (for 203.0.113.7: `203-0-113-7.sslip.io`). sslip.io answers
every such name with the IP inside it, so Caddy gets real Let's Encrypt certificates for the site, `s3.` and `mail.`
without owning a domain. When the domain exists (M1-OPS-05), change `PV_SITE`, `PV_WEB_URL`, `PV_CORS_ORIGINS`,
`PV_STORAGE_PUBLIC_ENDPOINT` and redeploy; switch email to Resend (`PV_EMAIL_BACKEND=resend`, `PV_RESEND_API_KEY`).

## The VM (one time)

- Ubuntu 24.04, x86-64, **4 vCPU / 16 GB / 160 GB disk** at least (the worker image is the CUDA one, ~6 GB; S4 on
  CPU needs the RAM). E.g. Hetzner CCX23 or CPX41 in Germany (EU data, ADR-011).
- Open ports 22, 80, 443 (TCP) and 443/UDP; nothing else.
- Docker Engine + compose plugin; a deploy user in the `docker` group with your SSH key.
- `docker login ghcr.io` as that user with a GitHub token that can read packages (`read:packages`), because the
  images are private.
- `mkdir -p /opt/pitchvision/infra/staging` and copy `infra/staging/.env.example` to
  `/opt/pitchvision/infra/staging/.env` (`chmod 600`). Fill every `change-me`: `openssl rand -hex 32`.
  Mailpit password hash: `docker run --rm caddy:2.10-alpine caddy hash-password --plaintext '<pw>' | sed 's/\$/$$/g'`
  (compose reads `$$` as `$`).

## Deploy

From a checkout (needs `ssh` + `rsync`):

```bash
infra/staging/deploy.sh deploy@203.0.113.7               # the sha of origin/main
infra/staging/deploy.sh deploy@203.0.113.7 <sha> --models  # first time: also copy ~/.cache/pitchvision/models
```

or Actions → **deploy-staging** → Run workflow (secrets `STAGING_SSH_TARGET`, `STAGING_SSH_KEY`,
`STAGING_KNOWN_HOSTS` on the `staging` environment). The tag must be a commit CI already built (full git sha).

`deploy.sh` copies `infra/compose`, `infra/staging`, `infra/observability`, pulls the images, starts the stack (the
`migrate` and `migrate-identity` one-shots run first), waits for health checks and `/readyz` of api + identity,
then runs `smoke.sh` against the public URL. On failure it redeploys the previous tag (`staging/.deployed-tag`).
Migrations are not rolled back: they must stay additive.

`smoke.sh [https://<site>]` checks web, `/config.js`, JWKS, `/api/v1/meta`, both auth guards (401) and that the
bucket refuses anonymous reads. Run it any time.

## Observability

| What | Where |
|---|---|
| Errors | Sentry: `PV_SENTRY_DSN` (api, identity, workers; release = image tag, tag `service`), `PV_WEB_SENTRY_DSN` (browser, via `/config.js`). No PII. |
| Metrics | api `GET /metrics` (bearer `PV_METRICS_TOKEN`, not routed publicly): `pv_http_requests_total`, `pv_http_request_duration_seconds`, `pv_queue_depth{queue}`, `pv_ingest_total{op,outcome,reason}`, `pv_jobs{status}`, `pv_jobs_finished_last_hour{status}`, `pv_dependency_up{dependency}` |
| Alerts | `infra/observability/alerts.yml`: ApiDown, DependencyDown, Api5xxRate, YoutubeIngestFailing (> 20 % / 1 h), JobFailureRate (> 5 % / 1 h), GpuQueueBacklog (20 min), CpuQueueBacklog → Alertmanager → `PV_ALERT_WEBHOOK_URL` (Slack incoming webhook, or Discord webhook + `/slack`) |
| Dashboards | `ssh -L 9090:localhost:9090 -L 9093:localhost:9093 deploy@host` → Prometheus / Alertmanager |
| Logs | `docker compose … logs -f api cpu-worker` on the VM |

Not yet: OpenTelemetry traces (AD §17), Grafana dashboards, GPU metrics.

## Day-to-day

```bash
cd /opt/pitchvision/infra
alias dc='docker compose --env-file staging/.env -f compose/docker-compose.yml -f staging/docker-compose.staging.yml'
dc ps; dc logs -f --tail 100 api
dc exec postgres psql -U pitchvision -c "SELECT id, status FROM jobs WHERE status NOT IN ('done','failed','cancelled')"
```

Backups: none yet — staging data is disposable. `dc down` keeps volumes; `dc down -v` wipes everything.
