"""M1-BE-04: probes, meta, problem+json, request id, CORS, dev auth stub, OpenAPI file."""
import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated

import httpx
import pytest
from fastapi import APIRouter, FastAPI, Query
from pitchvision_api.deps import CurrentPrincipal
from pitchvision_api.main import API_PREFIX, create_app
from pitchvision_api.openapi import render
from pitchvision_api.settings import Settings
from pitchvision_schemas import SCHEMA_VERSION

from .conftest import Db

DEAD_DB = "postgresql+asyncpg://nobody:x@127.0.0.1:1/none"
PROBLEM_KEYS = {"type", "title", "status", "code", "instance"}

probe = APIRouter(prefix="/_probe")


@probe.get("/items")
async def items(limit: Annotated[int, Query(ge=1, le=100)]) -> dict[str, int]:
    return {"limit": limit}


@probe.get("/boom")
async def boom() -> None:
    raise RuntimeError("db password is hunter2")


@probe.get("/whoami")
async def whoami(principal: CurrentPrincipal) -> dict[str, str]:
    return {"org_id": str(principal.org_id)}


def build(**overrides: object) -> FastAPI:
    app = create_app(Settings(**{"database_url": DEAD_DB, **overrides}))  # type: ignore[arg-type]
    app.include_router(probe, prefix=API_PREFIX)
    return app


async def client_for(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async for c in client_for(build()):
        yield c


def assert_problem(r: httpx.Response, status: int, code: str) -> dict[str, object]:
    assert r.status_code == status
    assert r.headers["content-type"] == "application/problem+json"
    body: dict[str, object] = r.json()
    assert body.keys() >= PROBLEM_KEYS
    assert body["status"] == status and body["code"] == code
    assert body["type"] == "https://docs.pitchvision.ai/errors/" + code.lower().replace("_", "-")
    assert body["instance"] == r.request.url.path
    return body


async def test_healthz_has_no_dependencies(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


async def test_readyz_503_when_db_down(client: httpx.AsyncClient) -> None:
    assert_problem(await client.get("/readyz"), 503, "INTERNAL")


async def test_readyz_ok_with_db(migrated: Db) -> None:
    async for c in client_for(build(database_url=migrated.app_url)):
        r = await c.get("/readyz")
        assert r.status_code == 200, r.text


async def test_meta(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/meta")
    assert r.status_code == 200
    assert r.json()["schema_version"] == SCHEMA_VERSION and r.json()["version"]


async def test_404_is_problem(client: httpx.AsyncClient) -> None:
    assert_problem(await client.get("/api/v1/nope"), 404, "NOT_FOUND")


async def test_422_lists_field_errors(client: httpx.AsyncClient) -> None:
    body = assert_problem(await client.get("/api/v1/_probe/items?limit=0"), 422, "VALIDATION_FAILED")
    assert body["errors"] == [
        {"loc": ["query", "limit"], "msg": "Input should be greater than or equal to 1", "type": "greater_than_equal"}
    ]


async def test_500_does_not_leak(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/_probe/boom", headers={"X-Request-ID": "req-500"})
    body = assert_problem(r, 500, "INTERNAL")
    assert "hunter2" not in r.text and "Traceback" not in r.text
    assert body["request_id"] == "req-500" and r.headers["x-request-id"] == "req-500"


async def test_request_id_echoed_or_generated(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz", headers={"X-Request-ID": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"
    r = await client.get("/healthz")
    assert len(r.headers["x-request-id"]) == 32
    r = await client.get("/healthz", headers={"X-Request-ID": "bad id\twith spaces"})
    assert r.headers["x-request-id"] != "bad id\twith spaces"
    assert (await client.get("/api/v1/nope")).headers["x-request-id"]


async def test_cors_from_settings() -> None:
    async for c in client_for(build(cors_origins="https://app.pitchvision.ai")):
        pre = {"Origin": "https://app.pitchvision.ai", "Access-Control-Request-Method": "POST"}
        r = await c.options("/api/v1/meta", headers=pre)
        assert r.headers["access-control-allow-origin"] == "https://app.pitchvision.ai"
        r = await c.get("/api/v1/meta", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in r.headers


async def test_dev_auth_needs_local_and_flag() -> None:
    org = str(uuid.uuid4())
    async for c in client_for(build(env="local", dev_auth=True)):
        assert (await c.get("/api/v1/_probe/whoami", headers={"X-Dev-Org": org})).json() == {"org_id": org}
        assert_problem(await c.get("/api/v1/_probe/whoami"), 401, "UNAUTHENTICATED")
        assert_problem(await c.get("/api/v1/_probe/whoami", headers={"X-Dev-Org": "x"}), 401, "UNAUTHENTICATED")
    async for c in client_for(build(env="local")):  # default path is JWT, the header is ignored
        r = await c.get("/api/v1/_probe/whoami", headers={"X-Dev-Org": org})
        assert_problem(r, 401, "UNAUTHENTICATED")
        assert r.headers["www-authenticate"] == "Bearer"
    with pytest.raises(ValueError, match="PV_DEV_AUTH"):
        Settings(env="production", dev_auth=True)


def test_committed_openapi_is_current() -> None:
    committed = Path(__file__).resolve().parents[1] / "openapi.json"
    assert json.loads(committed.read_text()) == json.loads(render()), (
        "regenerate: uv run python -m pitchvision_api.openapi > apps/api/openapi.json"
    )


def test_openapi_errors_are_problem_json() -> None:
    spec = json.loads(render())
    assert "HTTPValidationError" not in spec["components"]["schemas"]
    resp = spec["paths"]["/api/v1/meta"]["get"]["responses"]["4XX"]
    assert resp["content"] == {"application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}}
