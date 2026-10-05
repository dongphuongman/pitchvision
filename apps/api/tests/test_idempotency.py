"""Idempotency-Key behaviour on a test-only router (real /jobs and /exports land in W3/W5)."""
import asyncio
import uuid
from collections.abc import AsyncIterator

import httpx
import psycopg
import pytest
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pitchvision_api.db import make_engine, make_sessionmaker, org_session
from pitchvision_api.db.models import IdempotencyKey, Video
from pitchvision_api.deps import CurrentPrincipal, DbSession
from pitchvision_api.errors import ApiError
from pitchvision_api.main import API_PREFIX, create_app
from pitchvision_api.services.idempotency import Idem
from pitchvision_api.settings import Settings
from pitchvision_schemas import ErrorCode
from pydantic import BaseModel
from sqlalchemy import Select, func, select

from .conftest import Db

router = APIRouter(prefix="/_test")
URL = "/api/v1/_test/videos"
calls = {"n": 0}


class CreateIn(BaseModel):
    title: str
    fail: bool = False


@router.post("/videos", status_code=201)
async def create_video(body: CreateIn, idem: Idem, session: DbSession, principal: CurrentPrincipal) -> JSONResponse:
    calls["n"] += 1
    video = Video(org_id=principal.org_id, created_by=uuid.uuid4(), title=body.title)
    session.add(video)
    await session.flush()
    if body.fail:
        raise ApiError(ErrorCode.CONFLICT, 409, "simulated failure after write")
    await asyncio.sleep(0.05)  # widen the race window for the concurrency test
    return await idem.respond({"id": video.id, "title": body.title, "call": calls["n"]}, status_code=201)


@pytest.fixture
async def client(migrated: Db) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(env="local", dev_auth=True, database_url=migrated.app_url))
    app.include_router(router, prefix=API_PREFIX)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await app.state.engine.dispose()


def headers(org: uuid.UUID, key: str | None) -> dict[str, str]:
    return {"X-Dev-Org": str(org)} | ({"Idempotency-Key": key} if key else {})


async def count(db: Db, org: uuid.UUID, stmt: Select[tuple[int]]) -> int:
    engine = make_engine(db.app_url)
    try:
        async with org_session(make_sessionmaker(engine), org) as s:
            return int(await s.scalar(stmt) or 0)
    finally:
        await engine.dispose()


async def count_videos(db: Db, org: uuid.UUID, title: str) -> int:
    return await count(db, org, select(func.count()).select_from(Video).where(Video.title == title))


async def test_key_required(client: httpx.AsyncClient) -> None:
    r = await client.post(URL, json={"title": "a"}, headers=headers(uuid.uuid4(), None))
    assert r.status_code == 400 and r.headers["content-type"] == "application/problem+json"
    assert r.json()["code"] == "IDEMPOTENCY_KEY_REQUIRED"


async def test_same_key_same_body_replays(client: httpx.AsyncClient, migrated: Db) -> None:
    org, key, title = uuid.uuid4(), uuid.uuid4().hex, f"replay-{uuid.uuid4().hex[:6]}"
    first = await client.post(URL, json={"title": title, "fail": False}, headers=headers(org, key))
    assert first.status_code == 201, first.text
    # key order differs: fingerprint is over canonical JSON
    second = await client.post(
        URL, content=f'{{ "fail": false, "title": "{title}" }}',
        headers=headers(org, key) | {"Content-Type": "application/json"},
    )  # fmt: skip
    assert second.status_code == 201
    assert second.json() == first.json()
    assert second.headers["idempotent-replayed"] == "true"
    assert await count_videos(migrated, org, title) == 1


async def test_same_key_different_body_rejected(client: httpx.AsyncClient) -> None:
    org, key = uuid.uuid4(), uuid.uuid4().hex
    r = await client.post(URL, json={"title": "x"}, headers=headers(org, key))
    assert r.status_code == 201
    r = await client.post(URL, json={"title": "y"}, headers=headers(org, key))
    assert r.status_code == 422 and r.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


async def test_keys_are_scoped_per_org(client: httpx.AsyncClient) -> None:
    key = uuid.uuid4().hex
    a = await client.post(URL, json={"title": "x"}, headers=headers(uuid.uuid4(), key))
    b = await client.post(URL, json={"title": "y"}, headers=headers(uuid.uuid4(), key))
    assert a.status_code == b.status_code == 201
    assert a.json()["id"] != b.json()["id"] and "idempotent-replayed" not in b.headers


async def test_failure_rolls_back_and_frees_key(client: httpx.AsyncClient, migrated: Db) -> None:
    org, key, title = uuid.uuid4(), uuid.uuid4().hex, f"fail-{uuid.uuid4().hex[:6]}"
    r = await client.post(URL, json={"title": title, "fail": True}, headers=headers(org, key))
    assert r.status_code == 409
    assert await count_videos(migrated, org, title) == 0  # handler's write rolled back with the claim
    keys = select(func.count()).select_from(IdempotencyKey).where(IdempotencyKey.key == key)
    assert await count(migrated, org, keys) == 0
    r = await client.post(URL, json={"title": title, "fail": True}, headers=headers(org, key))
    assert r.status_code == 409  # re-executed, not replayed


async def test_concurrent_same_key_executes_once(client: httpx.AsyncClient, migrated: Db) -> None:
    org, key, title = uuid.uuid4(), uuid.uuid4().hex, f"race-{uuid.uuid4().hex[:6]}"
    rs = await asyncio.gather(
        *(client.post(URL, json={"title": title}, headers=headers(org, key)) for _ in range(4))
    )
    assert {r.status_code for r in rs} == {201}
    assert len({r.json()["id"] for r in rs}) == 1
    assert await count_videos(migrated, org, title) == 1


async def test_expired_key_is_reusable(client: httpx.AsyncClient, migrated: Db) -> None:
    org, key = uuid.uuid4(), uuid.uuid4().hex
    assert (await client.post(URL, json={"title": "old"}, headers=headers(org, key))).status_code == 201
    with psycopg.connect(migrated.conninfo()) as conn:  # superuser: age the row past the 24 h TTL
        conn.execute(
            "UPDATE idempotency_keys SET created_at = now() - interval '25 hours' WHERE org_id = %s AND key = %s",
            (org, key),
        )
    r = await client.post(URL, json={"title": "new"}, headers=headers(org, key))
    assert r.status_code == 201 and r.json()["title"] == "new" and "idempotent-replayed" not in r.headers
