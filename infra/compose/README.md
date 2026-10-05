# Local / self-host stack (Docker Compose)

Implements AD §13.1. One compose file, two modes:

| Command | What runs |
|---|---|
| `docker compose up -d` | CPU-only dev: postgres, redis, storage (+init), migrate, api, web, orchestrator, cpu-worker |
| `docker compose --profile gpu-local up -d` | everything above + `gpu-worker` on the NVIDIA GPU(s) (RTX 4090 box) |
| `docker compose --profile live up -d` | + `mediamtx` (RTSP/WebRTC ingest, M2) |

All commands below assume you are in `infra/compose/` (or pass `-f infra/compose/docker-compose.yml`).

## 1. One-time setup

```bash
cd infra/compose
cp .env.example .env
chmod 600 .env          # AD §14: the self-host secrets file is owner-read/write only
```

`.env` is gitignored. The values in `.env.example` are local-dev placeholders — change
`POSTGRES_PASSWORD`, `PV_STORAGE_ACCESS_KEY` and `PV_STORAGE_SECRET_KEY` on any machine
other people can reach. Never commit `.env`; cloud environments get secrets from AWS
Secrets Manager via External Secrets instead.

## 2. CPU-only dev (Mac, laptop, no NVIDIA)

Keep `PV_WORKER_BASE=python:3.12-slim` in `.env` (the default in `.env.example`): the worker
image is built without CUDA/TensorRT, so `orchestrator` and `cpu-worker` run anywhere.
GPU tasks go to RunPod when that backend is configured, otherwise they wait in the `gpu` queue.

```bash
docker compose up -d --build
docker compose ps            # every long-running service should become "healthy"
```

## 3. Full stack on a GPU box (RTX 4090)

Prerequisites: NVIDIA driver ≥ 550 (CUDA 12.4) and the NVIDIA Container Toolkit
(`nvidia-ctk runtime configure --runtime=docker`). Check with
`docker run --rm --gpus all nvidia/cuda:12.4.1-runtime-ubuntu22.04 nvidia-smi`.

```bash
# in .env
PV_WORKER_BASE=nvidia/cuda:12.4.1-runtime-ubuntu22.04

docker compose --profile gpu-local up -d --build
```

All workers share one image tag (AD D1), so after switching `PV_WORKER_BASE` rebuild with
`--build`. `gpu-worker` runs one process per container (`WORKER_CONCURRENCY` overrides).

## Ports (host side, bound to 127.0.0.1)

| Service | URL | Env var |
|---|---|---|
| web (nginx, SPA + `/api/` proxy) | http://localhost:3000 | `PV_WEB_PORT` |
| api (FastAPI, `GET /healthz`) | http://localhost:8000 | `PV_API_PORT` |
| storage (S3 API) | http://localhost:8333 | `PV_STORAGE_PORT` |
| postgres (TimescaleDB, pg16) | localhost:5432 | `PV_POSTGRES_PORT` |
| redis | localhost:6379 | `PV_REDIS_PORT` |
| mediamtx (profile `live`) | rtsp://localhost:8554, http://localhost:8889 (WebRTC) | — |

If a port is already taken on your machine, change it in `.env`.

## Services

- **migrate** — one-shot `alembic upgrade head` (api image, `apps/api/alembic.ini`). `api`
  starts only after it exits 0. Re-run after pulling new migrations:
  `docker compose run --rm migrate`.
- **storage** — SeaweedFS S3 gateway. **storage-init** (one-shot) creates
  `PV_STORAGE_BUCKET` and sets CORS (`PUT/GET/HEAD`, `ExposeHeaders: ETag`) for
  `PV_STORAGE_CORS_ORIGINS`, because browsers PUT multipart parts straight to storage
  (SP-07). It is idempotent: `docker compose up storage-init` re-applies CORS.
- **orchestrator / cpu-worker / gpu-worker** — the same `pitchvision-worker` image with
  `WORKER_ROLE` = `orchestrator` / `cpu` / `gpu`, consuming queues `orchestrate` / `cpu` / `gpu`.
  Health = Celery ping to the container's own node.
- **identity** — not started yet (TODO M1-BE); same api image with `command: identity`.

### Object storage choice (pending PO decision)

MinIO images are no longer publicly pullable (SP-07 finding), so the AD's "MinIO self-host"
is replaced **provisionally** by SeaweedFS (Apache-2.0). The PO still has to choose between
SeaweedFS and Garage. The app only talks plain S3: swapping the store means changing the
`storage` / `storage-init` services and `PV_STORAGE_ENDPOINT` — nothing in the app code.

Containers reach storage at `PV_STORAGE_ENDPOINT=http://storage:8333`; presigned URLs handed
to the browser must use `PV_STORAGE_PUBLIC_ENDPOINT` (default `http://localhost:8333`).

## Everyday commands

```bash
docker compose logs -f api cpu-worker      # follow logs
docker compose exec postgres psql -U pitchvision pitchvision
docker compose exec cpu-worker python -c \
  "from pitchvision_worker.celery_app import app; print(app.send_task('pitchvision_worker.ping', queue='cpu').get(timeout=10))"
docker compose down                        # stop, keep data
```

## Resetting volumes

Data lives in named volumes `pg-data`, `redis-data`, `storage-data`
(prefixed with the project name, `pitchvision_` by default).

```bash
docker compose --profile gpu-local --profile live down -v    # stop everything AND delete all data
docker compose rm -sf postgres && docker volume rm pitchvision_pg-data   # reset only the database
```

After a reset, `docker compose up -d` recreates the schema (migrate) and the bucket (storage-init).
