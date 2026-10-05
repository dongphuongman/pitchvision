"""Probes, problem+json, request id, OpenAPI file, migration 0001 (model parity, downgrade)."""

import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from identity_testkit import Db, Kit, Outbox, alembic_config, assert_problem, build_app
from pitchvision_identity.db.models import Base
from pitchvision_identity.openapi import render
from pitchvision_identity.ratelimit import MemoryRateLimiter
from sqlalchemy import create_engine

DEAD = Db("127.0.0.1", 1, "nobody", "x", "none")


@pytest.fixture
async def dead_client(jwt_key_pem: str) -> AsyncIterator[httpx.AsyncClient]:
    app = build_app(DEAD, jwt_key_pem, Outbox(), MemoryRateLimiter())
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="https://id.test") as c:
        yield c


async def test_probes(dead_client: httpx.AsyncClient, kit: Kit) -> None:
    assert (await dead_client.get("/healthz")).json() == {"status": "ok"}
    assert_problem(await dead_client.get("/readyz"), 503, "INTERNAL")
    assert (await kit.browser().get("/readyz")).status_code == 200


async def test_problem_json_and_request_id(dead_client: httpx.AsyncClient) -> None:
    r = await dead_client.get("/api/v1/nope", headers={"X-Request-ID": "abc-1"})
    assert_problem(r, 404, "NOT_FOUND")
    assert r.headers["x-request-id"] == "abc-1"
    r = await dead_client.post("/api/v1/auth/login", json={"email": "x"})
    body = assert_problem(r, 422, "VALIDATION_FAILED")
    assert {tuple(e["loc"]) for e in body["errors"]} == {("body", "email"), ("body", "password")}


def test_committed_openapi_is_current() -> None:
    committed = Path(__file__).resolve().parents[1] / "openapi.json"
    assert json.loads(committed.read_text()) == json.loads(render()), (
        "regenerate: uv run python -m pitchvision_identity.openapi > apps/identity/openapi.json"
    )


def test_openapi_covers_the_contract() -> None:
    paths = json.loads(render())["paths"]
    expected = {
        ("post", "/api/v1/auth/register"), ("post", "/api/v1/auth/login"), ("post", "/api/v1/auth/refresh"),
        ("post", "/api/v1/auth/logout"), ("post", "/api/v1/auth/verify-email"),
        ("post", "/api/v1/auth/verify-email/resend"), ("post", "/api/v1/auth/password/forgot"),
        ("post", "/api/v1/auth/password/reset"), ("get", "/api/v1/auth/oidc/google/start"),
        ("get", "/api/v1/auth/oidc/google/callback"), ("get", "/api/v1/me"), ("delete", "/api/v1/me/unverified"),
        ("post", "/api/v1/orgs"), ("get", "/api/v1/orgs/{org_id}"), ("patch", "/api/v1/orgs/{org_id}"),
        ("get", "/api/v1/orgs/{org_id}/members"), ("patch", "/api/v1/orgs/{org_id}/members/{member_id}"),
        ("delete", "/api/v1/orgs/{org_id}/members/{member_id}"), ("post", "/api/v1/orgs/{org_id}/invites"),
        ("get", "/api/v1/orgs/{org_id}/invites"), ("post", "/api/v1/orgs/{org_id}/invites/{invite_id}/resend"),
        ("delete", "/api/v1/orgs/{org_id}/invites/{invite_id}"), ("get", "/api/v1/invites/{token}"),
        ("post", "/api/v1/invites/{token}/accept"), ("get", "/.well-known/jwks.json"),
    }  # fmt: skip
    actual = {(m, p) for p, ops in paths.items() for m in ops}
    assert expected <= actual, expected - actual


def test_migration_matches_models_and_downgrades(migrated: Db) -> None:
    engine = create_engine(migrated.url())
    with engine.connect() as conn:
        diff = compare_metadata(
            MigrationContext.configure(conn, opts={"version_table": "alembic_version_identity"}), Base.metadata
        )
    engine.dispose()
    assert diff == [], diff
    cfg = alembic_config(migrated)
    command.downgrade(cfg, "base")
    tables = migrated.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1")
    assert tables == [("alembic_version_identity",)]
    command.upgrade(cfg, "head")
