"""Real-Postgres fixtures: a throwaway TimescaleDB container per session (or PV_TEST_DATABASE_URL, a superuser DSN)."""
import json
import os
import shutil
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
import pytest
from alembic import command
from alembic.config import Config

API_DIR = Path(__file__).resolve().parents[1]
IMAGE = os.environ.get("PV_TEST_PG_IMAGE", "timescale/timescaledb:latest-pg16")
APP_PASSWORD = "pv_app_test"


@dataclass(frozen=True)
class Db:
    host: str
    port: int
    user: str
    password: str
    dbname: str

    def url(self, driver: str = "psycopg", user: str | None = None, password: str | None = None) -> str:
        return (
            f"postgresql+{driver}://{user or self.user}:{password or self.password}"
            f"@{self.host}:{self.port}/{self.dbname}"
        )

    def conninfo(self) -> str:
        return f"host={self.host} port={self.port} user={self.user} password={self.password} dbname={self.dbname}"

    @property
    def app_url(self) -> str:
        """asyncpg DSN for the non-owner pv_app role — the one the API uses, so RLS applies."""
        return self.url("asyncpg", user="pv_app", password=APP_PASSWORD)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_ready(db: Db, timeout_s: float = 90.0) -> None:
    deadline = time.monotonic() + timeout_s
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(db.conninfo(), connect_timeout=2) as conn:
                conn.execute("SELECT 1")
                return
        except psycopg.OperationalError as e:  # init server only listens on the unix socket
            last = e
            time.sleep(0.5)
    raise RuntimeError(f"Postgres not ready after {timeout_s}s: {last}")


def _docker_ok() -> str | None:
    if shutil.which("docker") is None:
        return "docker CLI not found"
    probe = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=20)
    return None if probe.returncode == 0 else f"docker daemon unavailable: {probe.stderr.strip()[:200]}"


@pytest.fixture(scope="session")
def pg() -> Iterator[Db]:
    if dsn := os.environ.get("PV_TEST_DATABASE_URL"):
        info = psycopg.conninfo.conninfo_to_dict(dsn.replace("postgresql+psycopg://", "postgresql://"))
        db = Db(
            str(info.get("host", "localhost")),
            int(info.get("port", 5432)),
            str(info["user"]),
            str(info.get("password", "")),
            str(info["dbname"]),
        )
        _wait_ready(db)
        yield db
        return
    if reason := _docker_ok():
        pytest.skip(f"Postgres tests need Docker or PV_TEST_DATABASE_URL — {reason}")
    port = _free_port()
    name = f"pv-api-test-{uuid.uuid4().hex[:8]}"
    run = subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", name, "-e", "POSTGRES_PASSWORD=pv", "-e", "POSTGRES_DB=pv",
         "-p", f"127.0.0.1:{port}:5432", IMAGE],
        capture_output=True, text=True, timeout=300,
    )  # fmt: skip
    if run.returncode != 0:
        pytest.skip(f"could not start {IMAGE}: {run.stderr.strip()[:300]}")
    try:
        db = Db("127.0.0.1", port, "postgres", "pv", "pv")
        _wait_ready(db)
        yield db
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60)


def alembic_config(db: Db) -> Config:
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", db.url())
    return cfg


def enable_app_login(db: Db) -> None:
    """The migration creates pv_app NOLOGIN; deployments grant LOGIN out of band (secret stays out of git)."""
    with psycopg.connect(db.conninfo(), autocommit=True) as conn:
        conn.execute(f"ALTER ROLE pv_app LOGIN PASSWORD '{APP_PASSWORD}'")


@pytest.fixture(scope="session")
def migrated(pg: Db) -> Db:
    command.upgrade(alembic_config(pg), "head")
    enable_app_login(pg)
    return pg


# ---------------------------------------------------------------- S3 (SeaweedFS, same image as compose)
S3_IMAGE = os.environ.get("PV_TEST_S3_IMAGE", "chrislusf/seaweedfs:4.48")
S3_KEY, S3_SECRET, S3_BUCKET = "pvtest", "pvtest-secret-123", "pv-test-data"


@dataclass(frozen=True)
class S3:
    endpoint: str
    bucket: str = S3_BUCKET
    access_key: str = S3_KEY
    secret_key: str = S3_SECRET


def s3_client(endpoint: str) -> Any:
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3", endpoint_url=endpoint, aws_access_key_id=S3_KEY, aws_secret_access_key=S3_SECRET, region_name="us-east-1",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}, retries={"max_attempts": 1}),
    )  # fmt: skip


@pytest.fixture(scope="session")
def s3(tmp_path_factory: pytest.TempPathFactory) -> Iterator[S3]:
    """S3 gateway for upload tests: PV_TEST_S3_ENDPOINT (credentials pvtest/pvtest-secret-123) or a container."""
    if endpoint := os.environ.get("PV_TEST_S3_ENDPOINT"):
        yield S3(endpoint)
        return
    if reason := _docker_ok():
        pytest.skip(f"S3 tests need Docker or PV_TEST_S3_ENDPOINT — {reason}")
    cfg = tmp_path_factory.mktemp("s3") / "s3.json"
    identity = {"name": "t", "credentials": [{"accessKey": S3_KEY, "secretKey": S3_SECRET}],
                "actions": ["Admin", "Read", "Write", "List", "Tagging"]}  # fmt: skip
    cfg.write_text(json.dumps({"identities": [identity]}))
    port = _free_port()
    name = f"pv-api-s3-{uuid.uuid4().hex[:8]}"
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
        client = s3_client(endpoint)
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
        yield S3(endpoint)
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60)
