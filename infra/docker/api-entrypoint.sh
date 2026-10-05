#!/bin/sh
# Entrypoint for the pitchvision-api image.
#   api       uvicorn $PV_API_APP (default pitchvision_api.main:app) on $PORT
#   identity  uvicorn $PV_IDENTITY_APP (default pitchvision_identity.main:app) — identity-svc (AD §4)
#   migrate   alembic upgrade ${2:-head} using apps/api/alembic.ini
#   migrate-identity
#             create the identity database if missing (PV_IDENTITY_MIGRATE_DATABASE_URL must be able to
#             CREATE DATABASE, e.g. the compose superuser), then alembic upgrade ${2:-head} with
#             apps/identity/alembic.ini
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
    exec uvicorn "${PV_IDENTITY_APP:-pitchvision_identity.main:app}" \
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
  migrate-identity)
    python - <<'PY'
import os
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

dsn = os.environ.get("PV_IDENTITY_MIGRATE_DATABASE_URL") or os.environ["PV_IDENTITY_DATABASE_URL"]
dsn = dsn.replace("+psycopg", "").replace("+asyncpg", "")
dbname = conninfo_to_dict(dsn)["dbname"]
with psycopg.connect(make_conninfo(dsn, dbname="postgres"), autocommit=True) as conn:
    if conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)).fetchone():
        print(f"pv-api: database {dbname} exists")
    else:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
        print(f"pv-api: database {dbname} created")
PY
    cd /app/apps/identity
    alembic -c alembic.ini upgrade "${1:-head}"
    ;;
  *)
    exec "$cmd" "$@"
    ;;
esac
