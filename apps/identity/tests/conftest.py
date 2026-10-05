"""Real-Postgres fixtures, same style as apps/api: a throwaway TimescaleDB container per session, or
PV_TEST_DATABASE_URL (a superuser DSN). Identity always gets its own database `pv_identity_test` on that server,
so it never shares tables or the Alembic version table with the api tests.
"""

import os
import shutil
import socket
import subprocess
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from identity_testkit import Db, Kit, Outbox, build_app
from pitchvision_identity.ratelimit import MemoryRateLimiter

IDENTITY_DIR = Path(__file__).resolve().parents[1]
IMAGE = os.environ.get("PV_TEST_PG_IMAGE", "timescale/timescaledb:latest-pg16")
TEST_DB = "pv_identity_test"
TABLES = "users, orgs, memberships, invites, refresh_tokens, email_tokens, oauth_accounts"


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
    """Superuser connection to a Postgres server (not yet the identity database)."""
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
    name = f"pv-identity-test-{uuid.uuid4().hex[:8]}"
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
    cfg = Config(str(IDENTITY_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", db.url())
    return cfg


@pytest.fixture(scope="session")
def identity_db(pg: Db) -> Db:
    """A fresh, empty `pv_identity_test` database on the same server."""
    with psycopg.connect(pg.conninfo(), autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        conn.execute(f"CREATE DATABASE {TEST_DB}")
    return Db(pg.host, pg.port, pg.user, pg.password, TEST_DB)


@pytest.fixture(scope="session")
def migrated(identity_db: Db) -> Db:
    command.upgrade(alembic_config(identity_db), "head")
    return identity_db


@pytest.fixture(scope="session")
def jwt_key_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


@pytest.fixture
def clean_db(migrated: Db) -> Db:
    with psycopg.connect(migrated.conninfo(), autocommit=True) as conn:
        conn.execute(f"TRUNCATE {TABLES} CASCADE")
    return migrated


@pytest.fixture
async def kit(clean_db: Db, jwt_key_pem: str) -> AsyncIterator[Kit]:
    outbox = Outbox()
    app = build_app(clean_db, jwt_key_pem, outbox, MemoryRateLimiter())
    k = Kit(app=app, outbox=outbox, db=clean_db)
    try:
        yield k
    finally:
        await k.aclose()
