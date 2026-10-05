"""Google sign-in: OIDC authorization code + PKCE (S256) + state + nonce; id_token checked against Google's JWKS.

The pending request (state, nonce, PKCE verifier, return_to) travels in a short-lived HttpOnly cookie signed
with our own RS256 key, so identity stays stateless across replicas.
"""

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from sqlalchemy import select

from pitchvision_identity.context import Ctx
from pitchvision_identity.db.models import OAuthAccount, User
from pitchvision_identity.security import KeyRing, utcnow
from pitchvision_identity.services.sessions import Issued, open_session, revoke_all_for_user
from pitchvision_identity.settings import Settings

PROVIDER = "google"
STATE_AUDIENCE = "pv-oidc-state"
STATE_TTL_S = 600
CACHE_TTL_S = 3600
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
CALLBACK_PATH = "/api/v1/auth/oidc/google/callback"


class OidcError(Exception):
    """Ends the flow with a redirect to the web callback carrying `#error=<code>`."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code


@dataclass(frozen=True)
class Pending:
    state: str
    nonce: str
    verifier: str
    return_to: str | None


@dataclass(frozen=True)
class GoogleIdentity:
    subject: str
    email: str
    name: str | None


def safe_return_to(value: str | None) -> str | None:
    """Only same-origin absolute paths: `/library?x=1`, never `//evil.com` or `https://…` (open redirect)."""
    if not value or len(value) > 512 or not value.startswith("/") or value.startswith(("//", "/\\")):
        return None
    return value


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class GoogleOidc:
    def __init__(self, settings: Settings, http: httpx.AsyncClient, keys: KeyRing) -> None:
        self._s, self._http, self._keys = settings, http, keys
        self._discovery: tuple[float, dict[str, Any]] | None = None
        self._jwks: tuple[float, dict[str, Any]] | None = None

    @property
    def redirect_uri(self) -> str:
        return self._s.identity_public_url + CALLBACK_PATH

    async def _get_json(self, url: str) -> dict[str, Any]:
        r = await self._http.get(url, timeout=10)
        r.raise_for_status()
        data: dict[str, Any] = r.json()
        return data

    async def discovery(self) -> dict[str, Any]:
        if self._discovery is None or self._discovery[0] < time.monotonic():
            self._discovery = (time.monotonic() + CACHE_TTL_S, await self._get_json(self._s.google_discovery_url))
        return self._discovery[1]

    async def _jwk_for(self, kid: str) -> dict[str, Any]:
        for force in (False, True):  # unknown kid -> Google rotated keys: refetch once
            if force or self._jwks is None or self._jwks[0] < time.monotonic():
                jwks_uri = (await self.discovery())["jwks_uri"]
                self._jwks = (time.monotonic() + CACHE_TTL_S, await self._get_json(jwks_uri))
            for k in self._jwks[1].get("keys", []):
                if k.get("kid") == kid:
                    return dict(k)
        raise OidcError("UNAUTHENTICATED", f"unknown signing key {kid!r}")

    # ---------------------------------------------------------------- start

    async def start(self, return_to: str | None) -> tuple[str, str]:
        """Returns (Google authorization URL, signed pending-state cookie value)."""
        p = Pending(
            state=secrets.token_urlsafe(24),
            nonce=secrets.token_urlsafe(24),
            verifier=secrets.token_urlsafe(48),
            return_to=safe_return_to(return_to),
        )
        challenge = _b64url(hashlib.sha256(p.verifier.encode()).digest())
        params = {
            "client_id": self._s.google_client_id,
            "response_type": "code",
            "scope": "openid email profile",
            "redirect_uri": self.redirect_uri,
            "state": p.state,
            "nonce": p.nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
        url = (await self.discovery())["authorization_endpoint"] + "?" + urlencode(params)
        now = int(time.time())
        cookie = self._keys.sign(
            {
                "aud": STATE_AUDIENCE,
                "iss": self._s.jwt_issuer,
                "sub": "oidc-state",
                "iat": now,
                "exp": now + STATE_TTL_S,
                "st": p.state,
                "nonce": p.nonce,
                "cv": p.verifier,
                "rt": p.return_to,
            }
        )
        return url, cookie

    # ---------------------------------------------------------------- callback

    def pending_from_cookie(self, cookie: str | None, state: str | None) -> Pending:
        if not cookie or not state:
            raise OidcError("UNAUTHENTICATED", "missing state")
        try:
            c = self._keys.verify(cookie, audience=STATE_AUDIENCE)
        except jwt.PyJWTError as e:
            raise OidcError("UNAUTHENTICATED", f"bad state cookie: {e}") from None
        if not secrets.compare_digest(str(c.get("st", "")), state):
            raise OidcError("UNAUTHENTICATED", "state mismatch")
        return Pending(state=c["st"], nonce=c["nonce"], verifier=c["cv"], return_to=c.get("rt"))

    async def exchange(self, code: str, pending: Pending) -> GoogleIdentity:
        token_endpoint = (await self.discovery())["token_endpoint"]
        r = await self._http.post(
            token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "client_id": self._s.google_client_id,
                "client_secret": self._s.google_client_secret,
                "code_verifier": pending.verifier,
            },
            timeout=10,
        )
        if r.status_code != 200 or "id_token" not in r.json():
            raise OidcError("UNAUTHENTICATED", f"token exchange failed ({r.status_code})")
        return await self.validate_id_token(r.json()["id_token"], pending.nonce)

    async def validate_id_token(self, id_token: str, nonce: str) -> GoogleIdentity:
        try:
            kid = jwt.get_unverified_header(id_token).get("kid") or ""
            key = jwt.PyJWK(await self._jwk_for(kid)).key
            disc_iss = (await self.discovery()).get("issuer")
            claims: dict[str, Any] = jwt.decode(
                id_token,
                key,
                algorithms=["RS256"],
                audience=self._s.google_client_id,
                issuer=[i for i in {disc_iss, *GOOGLE_ISSUERS} if i],
                leeway=60,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as e:
            raise OidcError("UNAUTHENTICATED", f"invalid id_token: {e}") from None
        if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise OidcError("UNAUTHENTICATED", "nonce mismatch")
        email = str(claims.get("email") or "").strip().lower()
        if not email or claims.get("email_verified") not in (True, "true"):
            raise OidcError("EMAIL_NOT_VERIFIED", "Google account email is not verified")
        name = claims.get("name")
        return GoogleIdentity(subject=str(claims["sub"]), email=email, name=str(name)[:120] if name else None)


async def sign_in(ctx: Ctx, g: GoogleIdentity, user_agent: str | None) -> tuple[Issued, bool]:
    """Find by linked Google subject, else link to the user with that (Google-verified) email, else create.

    Linking to an existing *unverified* password account drops its password and sessions: whoever registered
    it never proved the address, so they must not keep access to the real owner's account (pre-hijack).
    """
    created = False
    async with ctx.sm() as s, s.begin():
        link = await s.get(OAuthAccount, (PROVIDER, g.subject))
        user = await s.get(User, link.user_id) if link else None
        if user is None:
            user = await s.scalar(select(User).where(User.email == g.email).with_for_update())
            if user is None:
                user = User(name=g.name or g.email.split("@")[0], email=g.email, email_verified_at=utcnow())
                s.add(user)
                await s.flush()
                created = True
            elif user.email_verified_at is None:
                user.password_hash = None
                await revoke_all_for_user(s, user.id)
            s.add(OAuthAccount(provider=PROVIDER, subject=g.subject, user_id=user.id, email=g.email))
        if user.email_verified_at is None:
            user.email_verified_at = utcnow()
        issued = await open_session(s, ctx, user, user_agent=user_agent)
    return issued, created


def callback_url(web_url: str, **fragment: str) -> str:
    return f"{web_url}/auth/callback#{urlencode(fragment)}"
