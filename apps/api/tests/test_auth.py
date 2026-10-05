"""Regional JWT check (identity contract, "Regional API side"): JWKS, iss/aud/exp, claims, region, roles."""

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

import httpx
import pytest
from fastapi import APIRouter, Depends, FastAPI
from pitchvision_api.auth import JwksClient, Principal, require_role
from pitchvision_api.deps import CurrentPrincipal
from pitchvision_api.main import API_PREFIX, create_app
from pitchvision_api.settings import Settings

from .jwt_helpers import ISSUER, JWKS_URL, FakeIdentity, KeyPair, bearer
from .test_app import DEAD_DB, assert_problem

probe = APIRouter(prefix="/_auth")


@probe.get("/me")
async def me(p: CurrentPrincipal) -> dict[str, str]:
    return {"user_id": str(p.user_id), "org_id": str(p.org_id), "role": p.role.value, "region": p.region}


@probe.get("/editor")
async def editor(p: Annotated[Principal, Depends(require_role("editor"))]) -> dict[str, str]:
    return {"role": p.role.value}


def build(identity: FakeIdentity, **overrides: object) -> FastAPI:
    settings = Settings(**{"database_url": DEAD_DB, "jwks_url": JWKS_URL, "jwt_issuer": ISSUER, **overrides})  # type: ignore[arg-type]
    app = create_app(settings)
    app.state.jwks = JwksClient(JWKS_URL, transport=identity.transport())
    app.include_router(probe, prefix=API_PREFIX)
    return app


@pytest.fixture
def identity() -> FakeIdentity:
    return FakeIdentity()


@pytest.fixture
async def client(identity: FakeIdentity) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=build(identity), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_valid_token_yields_principal(client: httpx.AsyncClient, identity: FakeIdentity) -> None:
    org, sub = uuid.uuid4(), uuid.uuid4()
    r = await client.get("/api/v1/_auth/me", headers=bearer(identity.token(org=org, sub=sub, role="editor")))
    assert r.status_code == 200
    assert r.json() == {"user_id": str(sub), "org_id": str(org), "role": "editor", "region": "eu"}


async def test_jwks_is_cached(client: httpx.AsyncClient, identity: FakeIdentity) -> None:
    for _ in range(3):
        assert (await client.get("/api/v1/_auth/me", headers=bearer(identity.token()))).status_code == 200
    assert identity.hits == 1


async def test_unknown_kid_triggers_one_refetch(client: httpx.AsyncClient, identity: FakeIdentity) -> None:
    assert (await client.get("/api/v1/_auth/me", headers=bearer(identity.token()))).status_code == 200
    rotated = KeyPair("k2")
    identity.published.append(rotated)
    jwks = client._transport.app.state.jwks  # type: ignore[attr-defined]
    jwks._fetched_at -= 60  # past the refetch rate limit
    r = await client.get("/api/v1/_auth/me", headers=bearer(identity.token(key=rotated)))
    assert r.status_code == 200 and identity.hits == 2
    # a kid nobody publishes: refetch is rate limited, so a burst of junk costs at most one more fetch
    stranger = KeyPair("evil")
    for _ in range(3):
        assert_problem(await client.get("/api/v1/_auth/me", headers=bearer(identity.token(key=stranger))), 401,
                       "UNAUTHENTICATED")  # fmt: skip
    assert identity.hits == 2


@pytest.mark.parametrize(
    ("kwargs", "status", "code"),
    [
        ({"ttl_s": -120}, 401, "UNAUTHENTICATED"),  # expired beyond leeway
        ({"aud": "someone-else"}, 401, "UNAUTHENTICATED"),
        ({"iss": "https://evil.example"}, 401, "UNAUTHENTICATED"),
        ({"email_verified": False}, 403, "EMAIL_NOT_VERIFIED"),
        ({"org": None}, 403, "FORBIDDEN"),
        ({"role": None}, 403, "FORBIDDEN"),
        ({"role": "superuser"}, 401, "UNAUTHENTICATED"),
        ({"region": "us"}, 409, "REGION_MISMATCH"),
        ({"region": None}, 409, "REGION_MISMATCH"),
    ],
)
async def test_rejected_claims(
    client: httpx.AsyncClient, identity: FakeIdentity, kwargs: dict[str, object], status: int, code: str
) -> None:
    token = identity.token(**kwargs)  # type: ignore[arg-type]
    assert_problem(await client.get("/api/v1/_auth/me", headers=bearer(token)), status, code)


async def test_tampered_and_foreign_signatures(client: httpx.AsyncClient, identity: FakeIdentity) -> None:
    good = identity.token()
    head, body, sig = good.split(".")
    tampered = f"{head}.{body}.{sig[:-4]}AAAA"
    forged = identity.token(key=KeyPair("k1"))  # same kid, different private key
    for token in (tampered, forged, "not-a-jwt"):
        assert_problem(await client.get("/api/v1/_auth/me", headers=bearer(token)), 401, "UNAUTHENTICATED")
    assert_problem(await client.get("/api/v1/_auth/me"), 401, "UNAUTHENTICATED")


async def test_hs256_confusion_rejected(client: httpx.AsyncClient) -> None:
    import jwt as pyjwt

    token = pyjwt.encode({"sub": str(uuid.uuid4())}, "s" * 64, algorithm="HS256", headers={"kid": "k1"})
    assert_problem(await client.get("/api/v1/_auth/me", headers=bearer(token)), 401, "UNAUTHENTICATED")


async def test_jwks_down_is_503(identity: FakeIdentity) -> None:
    identity.down = True
    transport = httpx.ASGITransport(app=build(identity), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/v1/_auth/me", headers=bearer(identity.token()))
    assert_problem(r, 503, "INTERNAL")


@pytest.mark.parametrize(("role", "ok"), [("viewer", False), ("editor", True), ("admin", True), ("owner", True)])
async def test_require_role(client: httpx.AsyncClient, identity: FakeIdentity, role: str, ok: bool) -> None:
    r = await client.get("/api/v1/_auth/editor", headers=bearer(identity.token(role=role)))
    if ok:
        assert r.status_code == 200 and r.json() == {"role": role}
    else:
        assert_problem(r, 403, "FORBIDDEN")


async def test_region_setting_is_respected(identity: FakeIdentity) -> None:
    transport = httpx.ASGITransport(app=build(identity, region="us"), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get("/api/v1/_auth/me", headers=bearer(identity.token(region="us")))).status_code == 200
        assert_problem(await c.get("/api/v1/_auth/me", headers=bearer(identity.token(region="eu"))), 409,
                       "REGION_MISMATCH")  # fmt: skip
