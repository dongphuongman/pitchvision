"""M1-BE-05: Google OIDC (code + PKCE + state + nonce) against a fake provider — no network."""

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from identity_testkit import PASSWORD, WEB, Db, FakeGoogle, Kit, Outbox, assert_problem, build_app, pkce_ok
from pitchvision_identity.ratelimit import MemoryRateLimiter
from pitchvision_identity.services.oidc import safe_return_to

START = "/api/v1/auth/oidc/google/start"
CALLBACK = "/api/v1/auth/oidc/google/callback"


@pytest.fixture
async def google() -> AsyncIterator[FakeGoogle]:
    g = FakeGoogle()
    yield g
    await g.http.aclose()


@pytest.fixture
async def gkit(clean_db: Db, jwt_key_pem: str, google: FakeGoogle) -> AsyncIterator[Kit]:
    outbox = Outbox()
    app = build_app(
        clean_db,
        jwt_key_pem,
        outbox,
        MemoryRateLimiter(),
        http=google.http,
        google_client_id=FakeGoogle.CLIENT_ID,
        google_client_secret=FakeGoogle.CLIENT_SECRET,
        public_url="https://id.test",
    )
    k = Kit(app=app, outbox=outbox, db=clean_db)
    try:
        yield k
    finally:
        await k.aclose()


def _fragment(r: httpx.Response) -> dict[str, str]:
    assert r.status_code == 302, r.text
    loc = r.headers["location"]
    assert loc.startswith(f"{WEB}/auth/callback#"), loc
    return {k: v[0] for k, v in parse_qs(urlsplit(loc).fragment).items()}


async def _start(
    c: httpx.AsyncClient, google: FakeGoogle, return_to: str | None = "/library?new=upload"
) -> dict[str, Any]:
    r = await c.get(START, params={"return_to": return_to} if return_to else None)
    assert r.status_code == 302, r.text
    assert r.headers["location"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    cookie = r.headers["set-cookie"].lower()
    assert "pv_oidc=" in cookie and "httponly" in cookie and "path=/api/v1/auth/oidc" in cookie
    return google.remember_auth_request(r.headers["location"])


async def test_google_happy_path_creates_verified_user(gkit: Kit, google: FakeGoogle) -> None:
    c = gkit.browser()
    params = await _start(c, google)
    assert params["client_id"] == FakeGoogle.CLIENT_ID and params["response_type"] == "code"
    assert params["code_challenge_method"] == "S256" and params["scope"] == "openid email profile"
    assert params["redirect_uri"] == "https://id.test/api/v1/auth/oidc/google/callback"

    r = await c.get(CALLBACK, params={"code": "auth-code-1", "state": params["state"]})
    assert _fragment(r) == {"status": "ok", "new_user": "true", "return_to": "/library?new=upload"}
    assert "pv_refresh=" in r.headers.get("set-cookie", "")
    [form] = google.token_requests
    assert form["code"] == ["auth-code-1"] and form["client_secret"] == [FakeGoogle.CLIENT_SECRET]
    assert pkce_ok(form["code_verifier"][0], params["code_challenge"])

    s = (await c.post("/api/v1/auth/refresh")).json()
    assert s["user"]["email"] == "fan@gmail.com" and s["user"]["email_verified"] is True
    assert s["user"]["name"] == "Gina Google"
    assert (await gkit.claims(c, s))["email_verified"] is True
    assert gkit.outbox.sent == []

    # second sign-in with the same Google subject is the same user, not new
    sub = gkit.db.execute("SELECT subject FROM oauth_accounts")[0][0]
    google.claims = {"sub": sub}
    c2 = gkit.browser()
    p2 = await _start(c2, google, return_to=None)
    r = await c2.get(CALLBACK, params={"code": "auth-code-2", "state": p2["state"]})
    assert _fragment(r) == {"status": "ok", "new_user": "false"}
    assert gkit.db.execute("SELECT count(*) FROM users") == [(1,)]


async def test_google_links_existing_accounts(gkit: Kit, google: FakeGoogle) -> None:
    # verified password account: linked, password still works
    await gkit.verified("fan@gmail.com")
    c = gkit.browser()
    p = await _start(c, google)
    assert _fragment(await c.get(CALLBACK, params={"code": "c", "state": p["state"]}))["new_user"] == "false"
    r = await c.post("/api/v1/auth/login", json={"email": "fan@gmail.com", "password": PASSWORD})
    assert r.status_code == 200

    # unverified squatter account: linked, but the squatter's password and sessions are dropped
    squatter = gkit.browser()
    await gkit.register(squatter, "victim@gmail.com")
    google.claims = {"email": "victim@gmail.com", "sub": "g-victim"}
    c = gkit.browser()
    p = await _start(c, google)
    assert _fragment(await c.get(CALLBACK, params={"code": "c", "state": p["state"]}))["status"] == "ok"
    assert_problem(await squatter.post("/api/v1/auth/refresh"), 401, "UNAUTHENTICATED")
    r = await squatter.post("/api/v1/auth/login", json={"email": "victim@gmail.com", "password": PASSWORD})
    assert_problem(r, 401, "INVALID_CREDENTIALS")
    assert (await c.post("/api/v1/auth/refresh")).json()["user"]["email_verified"] is True


async def test_google_bad_nonce_is_rejected(gkit: Kit, google: FakeGoogle) -> None:
    c = gkit.browser()
    p = await _start(c, google)
    google.nonce_override = "not-the-nonce"
    r = await c.get(CALLBACK, params={"code": "c", "state": p["state"]})
    assert _fragment(r) == {"error": "UNAUTHENTICATED"}
    assert "pv_refresh=" not in r.headers.get("set-cookie", "")
    assert gkit.db.execute("SELECT count(*) FROM users") == [(0,)]


async def test_google_state_and_cookie_checks(gkit: Kit, google: FakeGoogle) -> None:
    c = gkit.browser()
    p = await _start(c, google)
    assert _fragment(await c.get(CALLBACK, params={"code": "c", "state": "forged"})) == {"error": "UNAUTHENTICATED"}
    # state cookie cleared after a callback; also: another browser has no cookie at all
    other = gkit.browser()
    r = await other.get(CALLBACK, params={"code": "c", "state": p["state"]})
    assert _fragment(r) == {"error": "UNAUTHENTICATED"}
    assert _fragment(await c.get(CALLBACK, params={"error": "access_denied"})) == {"error": "UNAUTHENTICATED"}
    assert google.token_requests == []


async def test_google_unverified_email_and_wrong_audience(gkit: Kit, google: FakeGoogle) -> None:
    c = gkit.browser()
    p = await _start(c, google)
    google.claims = {"email_verified": False}
    r = await c.get(CALLBACK, params={"code": "c", "state": p["state"]})
    assert _fragment(r) == {"error": "EMAIL_NOT_VERIFIED"}

    p = await _start(c, google)
    google.claims = {"aud": "someone-else"}
    assert _fragment(await c.get(CALLBACK, params={"code": "c", "state": p["state"]})) == {"error": "UNAUTHENTICATED"}


async def test_google_disabled_without_client(kit: Kit) -> None:
    assert_problem(await kit.browser().get(START), 404, "NOT_FOUND")


def test_safe_return_to() -> None:
    assert safe_return_to("/library?x=1") == "/library?x=1"
    for bad in ("//evil.com", "https://evil.com", "/\\evil.com", "library", "", None, "/" + "a" * 600):
        assert safe_return_to(bad) is None
