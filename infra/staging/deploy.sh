#!/usr/bin/env bash
# Deploy PitchVision to the staging VM (M1-OPS-07).
#
#   infra/staging/deploy.sh <ssh-target> [tag] [--models]
#     ssh-target   user@host (key-based ssh; the user can run docker)
#     tag          image tag pushed by CI (full git sha); default: the sha of origin/main
#     --models     also rsync ~/.cache/pitchvision/models (ONNX weights, never in git) to /opt/pitchvision/models
#
# What it does: copy the compose files, pull the images, run migrations (the migrate / migrate-identity one-shots),
# start everything, wait for the probes, then smoke-test the public URL. If that fails it redeploys the previous
# tag. Migrations are not reverted (they are additive by convention); a broken migration needs a manual fix.
set -euo pipefail

target="${1:?usage: deploy.sh user@host [tag] [--models]}"
tag="${2:-}"
models=0
for a in "$@"; do [ "$a" = "--models" ] && models=1; done
[ "$tag" = "--models" ] && tag=""
root="$(cd "$(dirname "$0")/../.." && pwd)"
[ -n "$tag" ] || tag="$(git -C "$root" rev-parse origin/main)"
remote=/opt/pitchvision

echo "==> deploying $tag to $target"
ssh "$target" "mkdir -p $remote/infra $remote/models && test -f $remote/infra/staging/.env" || {
  echo "!! $remote/infra/staging/.env missing on $target — copy infra/staging/.env.example there and fill it" >&2
  exit 1
}
rsync -az --delete --exclude .env --exclude '.deployed-*' \
  "$root/infra/compose" "$root/infra/staging" "$root/infra/observability" "$target:$remote/infra/"
if [ "$models" = 1 ]; then
  rsync -az "${PV_MODEL_DIR:-$HOME/.cache/pitchvision/models}/" "$target:$remote/models/"
fi

ssh "$target" TAG="$tag" bash -s <<'REMOTE'
set -euo pipefail
cd /opt/pitchvision/infra
dc() { docker compose --env-file staging/.env -f compose/docker-compose.yml -f staging/docker-compose.staging.yml "$@"; }
prev="$(cat staging/.deployed-tag 2>/dev/null || true)"

up() {
  TAG="$1" dc pull --quiet
  TAG="$1" dc up -d --remove-orphans --wait --wait-timeout 600 \
    postgres redis storage mailpit api identity web caddy prometheus alertmanager
  TAG="$1" dc up -d --remove-orphans orchestrator cpu-worker ingest-worker
}
envval() { grep -E "^$1=" staging/.env | tail -1 | cut -d= -f2-; }  # .env is not shell: never source it
probe() {  # internal readiness (ports bound to 127.0.0.1 by the base compose file)
  local api identity
  api="$(envval PV_API_PORT)"; identity="$(envval PV_IDENTITY_PORT)"
  for _ in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:${api:-8000}/readyz" >/dev/null &&
       curl -fsS "http://127.0.0.1:${identity:-8001}/readyz" >/dev/null; then return 0; fi
    sleep 4
  done
  return 1
}

if up "$TAG" && probe && staging/smoke.sh; then
  echo "$TAG" > staging/.deployed-tag
  echo "==> staging is on $TAG"
  docker image prune -f >/dev/null
  exit 0
fi
echo "!! deploy of $TAG failed" >&2
dc ps >&2 || true
if [ -n "$prev" ] && [ "$prev" != "$TAG" ]; then
  echo "==> rolling back to $prev" >&2
  up "$prev" && probe && echo "==> back on $prev" >&2
fi
exit 1
REMOTE
