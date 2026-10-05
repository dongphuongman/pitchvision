"""Docker-backed fixtures for worker integration tests (skipped cleanly without Docker).

- `pg_app_url`: TimescaleDB migrated with apps/api's Alembic (the API owns the schema), pv_app login enabled.
- `redis_url`: a throwaway Redis for the Celery contract test.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parents[2] / "apps" / "api"
PG_IMAGE = os.environ.get("PV_TEST_PG_IMAGE", "timescale/timescaledb:latest-pg16")
REDIS_IMAGE = "redis:7-alpine"
APP_PASSWORD = "pv_app_test"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _docker_skip_reason() -> str | None:
    if shutil.which("docker") is None:
        return "docker CLI not found"
    probe = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=20)
    return None if probe.returncode == 0 else "docker daemon unavailable"


def _run(image: str, port: int, inner: int, *env: str) -> str:
    name = f"pv-worker-test-{uuid.uuid4().hex[:8]}"
    args = ["docker", "run", "-d", "--rm", "--name", name, "-p", f"127.0.0.1:{port}:{inner}"]
    for e in env:
        args += ["-e", e]
    run = subprocess.run([*args, image], capture_output=True, text=True, timeout=300)
    if run.returncode != 0:
        pytest.skip(f"could not start {image}: {run.stderr.strip()[:300]}")
    return name


@pytest.fixture(scope="session")
def pg_app_url() -> Iterator[str]:
    import psycopg
    from alembic import command
    from alembic.config import Config

    if reason := _docker_skip_reason():
        pytest.skip(f"needs Docker — {reason}")
    port = _free_port()
    name = _run(PG_IMAGE, port, 5432, "POSTGRES_PASSWORD=pv", "POSTGRES_DB=pv")
    try:
        conninfo = f"host=127.0.0.1 port={port} user=postgres password=pv dbname=pv"
        deadline = time.monotonic() + 90
        while True:
            try:
                with psycopg.connect(conninfo, connect_timeout=2) as conn:
                    conn.execute("SELECT 1")
                break
            except psycopg.OperationalError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)
        cfg = Config(str(API_DIR / "alembic.ini"))
        cfg.set_main_option("script_location", str(API_DIR / "pitchvision_api" / "db" / "migrations"))
        cfg.set_main_option("sqlalchemy.url", f"postgresql+psycopg://postgres:pv@127.0.0.1:{port}/pv")
        command.upgrade(cfg, "head")
        with psycopg.connect(conninfo, autocommit=True) as conn:
            conn.execute(f"ALTER ROLE pv_app LOGIN PASSWORD '{APP_PASSWORD}'")
        yield f"postgresql+psycopg://pv_app:{APP_PASSWORD}@127.0.0.1:{port}/pv"
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60)


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    if reason := _docker_skip_reason():
        pytest.skip(f"needs Docker — {reason}")
    port = _free_port()
    name = _run(REDIS_IMAGE, port, 6379)
    try:
        import redis

        client = redis.Redis(host="127.0.0.1", port=port, socket_connect_timeout=1)
        deadline = time.monotonic() + 30
        while True:
            try:
                client.ping()
                break
            except redis.ConnectionError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.3)
        yield f"redis://127.0.0.1:{port}/0"
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60)
