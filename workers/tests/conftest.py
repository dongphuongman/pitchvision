"""Docker-backed fixtures for worker integration tests (skipped cleanly without Docker).

- `pg_app_url`: TimescaleDB migrated with apps/api's Alembic (the API owns the schema), pv_app login enabled.
- `redis_url`: a throwaway Redis for the Celery contract tests.
- `s3_endpoint`: a throwaway SeaweedFS S3 gateway (bucket `pv-test-data`) for the compute backend round trip.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

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


# ---------------------------------------------------------------- S3 (SeaweedFS, same image as compose)
S3_IMAGE = os.environ.get("PV_TEST_S3_IMAGE", "chrislusf/seaweedfs:4.48")
S3_KEY, S3_SECRET, S3_BUCKET = "pvtest", "pvtest-secret-123", "pv-test-data"


def s3_test_client(endpoint: str) -> Any:
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3", endpoint_url=endpoint, aws_access_key_id=S3_KEY, aws_secret_access_key=S3_SECRET, region_name="us-east-1",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}, retries={"max_attempts": 1}),
    )  # fmt: skip


@pytest.fixture(scope="session")
def s3_endpoint(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """SeaweedFS S3 gateway with bucket `pv-test-data` (PV_TEST_S3_ENDPOINT, credentials pvtest/…, or a container)."""
    if endpoint := os.environ.get("PV_TEST_S3_ENDPOINT"):
        yield endpoint
        return
    if reason := _docker_skip_reason():
        pytest.skip(f"needs Docker — {reason}")
    cfg = tmp_path_factory.mktemp("s3") / "s3.json"
    identity = {"name": "t", "credentials": [{"accessKey": S3_KEY, "secretKey": S3_SECRET}],
                "actions": ["Admin", "Read", "Write", "List", "Tagging"]}  # fmt: skip
    cfg.write_text(json.dumps({"identities": [identity]}))
    port = _free_port()
    name = f"pv-worker-s3-{uuid.uuid4().hex[:8]}"
    run = subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", name, "-p", f"127.0.0.1:{port}:8333",
         "-v", f"{cfg}:/etc/seaweedfs/s3.json:ro", S3_IMAGE,
         "server", "-s3", "-s3.port=8333", "-s3.config=/etc/seaweedfs/s3.json", "-dir=/data"],
        capture_output=True, text=True, timeout=300,
    )  # fmt: skip
    if run.returncode != 0:
        pytest.skip(f"could not start {S3_IMAGE}: {run.stderr.strip()[:300]}")
    try:
        endpoint = f"http://127.0.0.1:{port}"
        client = s3_test_client(endpoint)
        deadline, last = time.monotonic() + 90, None
        while time.monotonic() < deadline:
            try:  # the gateway answers before master/volume servers are ready
                client.create_bucket(Bucket=S3_BUCKET)
                client.put_object(Bucket=S3_BUCKET, Key="_probe", Body=b"ok")
                break
            except Exception as e:
                last = e
                time.sleep(1)
        else:
            raise RuntimeError(f"SeaweedFS not ready: {last!r}")
        yield endpoint
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60)
