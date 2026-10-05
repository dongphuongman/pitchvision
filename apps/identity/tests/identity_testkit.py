"""Helpers shared by the identity tests (imported by conftest and test modules)."""

import base64
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import psycopg
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from jwt.algorithms import RSAAlgorithm
from pitchvision_identity.mail import EmailMessage
from pitchvision_identity.main import create_app
from pitchvision_identity.ratelimit import RateLimiter
from pitchvision_identity.settings import Settings

PASSWORD = "correct horse battery"
WEB = "https://app.test"
ISSUER = "https://id.test"
PROBLEM_KEYS = {"type", "title", "status", "code", "instance"}


@dataclass(frozen=True)
class Db:
    host: str
    port: int
    user: str
    password: str
    dbname: str

    def url(self, driver: str = "psycopg") -> str:
        return f"postgresql+{driver}://{self.user}:{self.password}@{self.host}:{self.port}/{self.dbname}"

    def conninfo(self) -> str:
        return f"host={self.host} port={self.port} user={self.user} password={self.password} dbname={self.dbname}"

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        with psycopg.connect(self.conninfo(), autocommit=True) as conn:
            cur = conn.execute(sql, params)  # type: ignore[arg-type]
            return cur.fetchall() if cur.description else []


class Outbox:
    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    async def send(self, msg: EmailMessage) -> None:
        self.sent.append(msg)

    def to(self, email: str) -> list[EmailMessage]:
        return [m for m in self.sent if m.to == email]

    def last_token(self, email: str) -> str:
        """Token from the newest link sent to `email` (query `token=` or the /invite/{token} path)."""
        link = self.to(email)[-1].link
        parts = urlsplit(link)
        if q := parse_qs(parts.query).get("token"):
            return q[0]
        return parts.path.rsplit("/", 1)[-1]


def settings_for(db: Db, pem: str, **overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "env": "test",
        "identity_database_url": db.url("asyncpg"),
        "jwt_private_key_pem": pem,
        "jwt_issuer": ISSUER,
        "web_url": WEB,
        "argon2_time_cost": 1,
        "argon2_memory_kib": 1024,
        "argon2_parallelism": 1,
    }
    return Settings(**(base | overrides))


def build_app(
    db: Db, pem: str, outbox: Outbox, limiter: RateLimiter, http: httpx.AsyncClient | None = None, **overrides: Any
) -> FastAPI:
    return create_app(settings_for(db, pem, **overrides), http=http, mailer=outbox, limiter=limiter)


@dataclass
class Kit:
    """One app + any number of "browsers" (httpx clients with their own cookie jar)."""

    app: FastAPI
    outbox: Outbox
    db: Db
    _clients: list[httpx.AsyncClient] = field(default_factory=list)

    def browser(self) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=self.app, raise_app_exceptions=False)
        # https base so the Secure refresh cookie round-trips like in a real browser
        c = httpx.AsyncClient(transport=transport, base_url="https://id.test", follow_redirects=False)
        self._clients.append(c)
        return c

    async def aclose(self) -> None:
        for c in self._clients:
            await c.aclose()
        await self.app.state.engine.dispose()

    async def register(self, c: httpx.AsyncClient, email: str, name: str = "Sam Coach", **extra: Any) -> httpx.Response:
        return await c.post("/api/v1/auth/register", json={"name": name, "email": email, "password": PASSWORD, **extra})

    async def verified(self, email: str, name: str = "Sam Coach") -> tuple[httpx.AsyncClient, dict[str, Any]]:
        """New browser with a registered + verified user; returns (browser, Session)."""
        c = self.browser()
        r = await self.register(c, email, name)
        assert r.status_code == 201, r.text
        r = await c.post("/api/v1/auth/verify-email", json={"token": self.outbox.last_token(email)})
        assert r.status_code == 200, r.text
        return c, r.json()

    async def org(self, c: httpx.AsyncClient, session: dict[str, Any], **body: Any) -> dict[str, Any]:
        payload = {"name": "Riverside FC", "kind": "club", "region": "eu"} | body
        r = await c.post("/api/v1/orgs", json=payload, headers=auth(session))
        assert r.status_code == 201, r.text
        org: dict[str, Any] = r.json()
        return org

    async def switch(self, c: httpx.AsyncClient, org_id: str) -> dict[str, Any]:
        r = await c.post("/api/v1/auth/refresh", json={"org_id": org_id})
        assert r.status_code == 200, r.text
        s: dict[str, Any] = r.json()
        return s

    async def claims(self, c: httpx.AsyncClient, session: dict[str, Any]) -> dict[str, Any]:
        """Validate the access token exactly like the regional API will: JWKS + RS256 + iss/aud/exp."""
        jwks = (await c.get("/.well-known/jwks.json")).json()
        token = session["access_token"]
        kid = jwt.get_unverified_header(token)["kid"]
        key = jwt.PyJWK(next(k for k in jwks["keys"] if k["kid"] == kid)).key
        claims: dict[str, Any] = jwt.decode(token, key, algorithms=["RS256"], audience="pitchvision", issuer=ISSUER)
        return claims


def auth(session: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {session['access_token']}"}


def assert_problem(r: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert r.status_code == status, r.text
    assert r.headers["content-type"] == "application/problem+json"
    body: dict[str, Any] = r.json()
    assert body.keys() >= PROBLEM_KEYS and body["status"] == status and body["code"] == code, body
    return body


# ------------------------------------------------------------------ fake Google (no network)


class FakeGoogle:
    """Discovery + JWKS + token endpoint served through httpx.MockTransport, signing with our own key."""

    ISSUER = "https://accounts.google.com"
    CLIENT_ID = "test-client.apps.googleusercontent.com"
    CLIENT_SECRET = "test-secret"

    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "fake-google-1"
        self.claims: dict[str, Any] = {}
        self.nonce_override: str | None = None
        self.token_requests: list[dict[str, list[str]]] = []
        self._nonce: str | None = None
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def remember_auth_request(self, location: str) -> dict[str, str]:
        params = {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}
        self._nonce = params["nonce"]
        return params

    def _id_token(self) -> str:
        now = int(time.time())
        payload = {
            "iss": self.ISSUER,
            "aud": self.CLIENT_ID,
            "sub": "g-" + uuid.uuid4().hex[:10],
            "email": "fan@gmail.com",
            "email_verified": True,
            "name": "Gina Google",
            "iat": now,
            "exp": now + 3600,
            "nonce": self.nonce_override or self._nonce,
        } | self.claims
        return jwt.encode(payload, self.key, algorithm="RS256", headers={"kid": self.kid})

    def _handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": self.ISSUER,
                    "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
                    "token_endpoint": "https://oauth2.googleapis.com/token",
                    "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
                },
            )
        if url == "https://www.googleapis.com/oauth2/v3/certs":
            jwk = json.loads(RSAAlgorithm.to_jwk(self.key.public_key())) | {"kid": self.kid, "alg": "RS256"}
            return httpx.Response(200, json={"keys": [jwk]})
        if url == "https://oauth2.googleapis.com/token":
            form = parse_qs(request.content.decode())
            self.token_requests.append(form)
            return httpx.Response(200, json={"id_token": self._id_token(), "access_token": "x", "token_type": "Bearer"})
        return httpx.Response(404)


def pkce_ok(verifier: str, challenge: str) -> bool:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == challenge
