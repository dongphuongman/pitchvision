#!/bin/sh
# Entrypoint for the pitchvision-api image.
#   api       uvicorn $PV_API_APP (default pitchvision_api.main:app) on $PORT
#   identity  uvicorn $PV_IDENTITY_APP — identity-svc (AD §4). TODO(M1-BE): set the default
#             module once identity-svc lands; in self-host the api serves it for now.
#   migrate   alembic upgrade ${2:-head} using apps/api/alembic.ini
#   *         exec the given command (e.g. `bash`, `alembic current`)
set -eu

cmd="${1:-api}"
[ $# -gt 0 ] && shift

case "$cmd" in
  api)
    exec uvicorn "${PV_API_APP:-pitchvision_api.main:app}" \
      --host 0.0.0.0 --port "${PORT:-8000}" \
      --workers "${UVICORN_WORKERS:-1}" \
      --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}" "$@"
    ;;
  identity)
    if [ -z "${PV_IDENTITY_APP:-}" ]; then
      echo "pv-api: PV_IDENTITY_APP is not set (identity-svc not implemented yet)" >&2
      exit 2
    fi
    exec uvicorn "$PV_IDENTITY_APP" \
      --host 0.0.0.0 --port "${PORT:-8000}" \
      --workers "${UVICORN_WORKERS:-1}" \
      --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}" "$@"
    ;;
  migrate)
    cd /app/apps/api
    alembic -c alembic.ini upgrade "${1:-head}"
    # The migration creates pv_app NOLOGIN; grant LOGIN here so the password stays out of git.
    if [ -n "${PV_APP_DB_PASSWORD:-}" ]; then
      python - <<'PY'
import os
import psycopg
from psycopg import sql

dsn = os.environ.get("PV_MIGRATE_DATABASE_URL") or os.environ["PV_DATABASE_URL"]
dsn = dsn.replace("+psycopg", "").replace("+asyncpg", "")
with psycopg.connect(dsn, autocommit=True) as conn:
    conn.execute(sql.SQL("ALTER ROLE pv_app LOGIN PASSWORD {}").format(sql.Literal(os.environ["PV_APP_DB_PASSWORD"])))
print("pv-api: pv_app login enabled")
PY
    fi
    ;;
  *)
    exec "$cmd" "$@"
    ;;
esac
