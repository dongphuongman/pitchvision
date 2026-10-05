"""Passwords (argon2id), opaque tokens (SHA-256 at rest) and the RS256 signing key + JWKS."""

import asyncio
import base64
import hashlib
import json
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher as Argon2Hasher
from argon2 import Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from pitchvision_identity.settings import Settings

log = logging.getLogger(__name__)

ALGORITHM = "RS256"


def utcnow() -> datetime:
    return datetime.now(UTC)


def normalize_email(email: str) -> str:
    return email.strip().lower()


# ------------------------------------------------------------------ opaque tokens


def new_token() -> str:
    """256-bit URL-safe random token; only its hash is stored."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ------------------------------------------------------------------ passwords


class PasswordHasher:
    """argon2id; hashing runs in a thread so the event loop stays responsive."""

    def __init__(self, settings: Settings) -> None:
        self._h = Argon2Hasher(
            time_cost=settings.argon2_time_cost,
            memory_cost=settings.argon2_memory_kib,
            parallelism=settings.argon2_parallelism,
            type=Type.ID,
        )
        # verified against when the email is unknown, so timing does not reveal account existence
        self._dummy = self._h.hash(secrets.token_urlsafe(16))

    async def hash(self, password: str) -> str:
        return await asyncio.to_thread(self._h.hash, password)

    async def verify(self, password_hash: str | None, password: str) -> bool:
        def _verify() -> bool:
            try:
                return self._h.verify(password_hash or self._dummy, password) and password_hash is not None
            except (VerifyMismatchError, VerificationError, InvalidHashError):
                return False

        return await asyncio.to_thread(_verify)

    def needs_rehash(self, password_hash: str) -> bool:
        return self._h.check_needs_rehash(password_hash)


# ------------------------------------------------------------------ JWT signing key


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    email: str
    email_verified: bool
    org_id: uuid.UUID | None
    role: str | None
    region: str | None


class KeyRing:
    """The RS256 private key that signs access tokens, its kid and the public JWKS."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._private = _load_private_key(settings)
        public_jwk: dict[str, Any] = json.loads(RSAAlgorithm.to_jwk(self._private.public_key()))
        self.kid = settings.jwt_kid or _thumbprint(public_jwk)
        self.public_jwk = public_jwk | {"kid": self.kid, "use": "sig", "alg": ALGORITHM}

    def jwks(self) -> dict[str, Any]:
        return {"keys": [self.public_jwk]}

    def sign(self, payload: dict[str, Any]) -> str:
        return jwt.encode(payload, self._private, algorithm=ALGORITHM, headers={"kid": self.kid})

    def verify(self, token: str, audience: str | None = None) -> dict[str, Any]:
        claims: dict[str, Any] = jwt.decode(
            token,
            self._private.public_key(),
            algorithms=[ALGORITHM],
            audience=audience or self._settings.jwt_audience,
            issuer=self._settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub"]},
        )
        return claims

    def issue_access_token(self, c: AccessClaims) -> tuple[str, int]:
        ttl = self._settings.access_token_ttl_s
        now = utcnow()
        payload = {
            "iss": self._settings.jwt_issuer,
            "aud": self._settings.jwt_audience,
            "sub": str(c.user_id),
            "email": c.email,
            "email_verified": c.email_verified,
            "org": str(c.org_id) if c.org_id else None,
            "role": c.role,
            "region": c.region,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=ttl)).timestamp()),
            "jti": uuid.uuid4().hex,
        }
        return self.sign(payload), ttl


def _load_private_key(settings: Settings) -> rsa.RSAPrivateKey:
    pem: bytes | None = None
    if settings.jwt_private_key_pem:
        pem = settings.jwt_private_key_pem.replace("\\n", "\n").encode()
    elif settings.jwt_private_key_file:
        pem = settings.jwt_private_key_file.read_bytes()
    if pem is None:
        if not settings.is_dev:  # Settings validation already refuses this; belt and braces
            raise RuntimeError("no JWT signing key configured")
        log.warning(
            "PV_JWT_PRIVATE_KEY_* not set: using an EPHEMERAL RSA key (env=%s); tokens die on restart", settings.env
        )
        return rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
        raise RuntimeError("PV_JWT private key must be RSA >= 2048 bits")
    return key


def _thumbprint(jwk: dict[str, Any]) -> str:
    """RFC 7638 JWK thumbprint (base64url SHA-256 of the canonical e/kty/n members)."""
    canonical = json.dumps({k: jwk[k] for k in ("e", "kty", "n")}, separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(hashlib.sha256(canonical.encode()).digest()).rstrip(b"=").decode()
