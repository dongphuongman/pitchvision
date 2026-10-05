"""Mint identity-style access tokens with a local RSA key and serve its JWKS through httpx.MockTransport."""
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

ISSUER = "https://id.test.pitchvision.ai"
JWKS_URL = "https://id.test.pitchvision.ai/.well-known/jwks.json"


@dataclass
class KeyPair:
    kid: str
    private: rsa.RSAPrivateKey = field(default_factory=lambda: rsa.generate_private_key(65537, 2048))

    def jwk(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(RSAAlgorithm.to_jwk(self.private.public_key()))
        return data | {"kid": self.kid, "use": "sig", "alg": "RS256"}


@dataclass
class FakeIdentity:
    """Issues tokens; `published` is what the JWKS endpoint serves (rotate by appending a key)."""

    keys: list[KeyPair] = field(default_factory=lambda: [KeyPair("k1")])
    published: list[KeyPair] = field(default_factory=list)
    hits: int = 0
    down: bool = False

    def __post_init__(self) -> None:
        if not self.published:
            self.published = list(self.keys)

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            assert str(request.url) == JWKS_URL
            self.hits += 1
            if self.down:
                return httpx.Response(503)
            return httpx.Response(200, json={"keys": [k.jwk() for k in self.published]})

        return httpx.MockTransport(handler)

    def token(
        self,
        *,
        org: uuid.UUID | str | None = "new",
        role: str | None = "owner",
        region: str | None = "eu",
        sub: uuid.UUID | None = None,
        email_verified: bool = True,
        key: KeyPair | None = None,
        ttl_s: int = 900,
        **overrides: Any,
    ) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": ISSUER,
            "aud": "pitchvision",
            "sub": str(sub or uuid.uuid4()),
            "email": "coach@example.com",
            "email_verified": email_verified,
            "org": str(uuid.uuid4()) if org == "new" else (str(org) if org else None),
            "role": role,
            "region": region,
            "iat": now,
            "exp": now + ttl_s,
            "jti": uuid.uuid4().hex,
        } | overrides
        k = key or self.keys[0]
        return jwt.encode(claims, k.private, algorithm="RS256", headers={"kid": k.kid})


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
