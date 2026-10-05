"""FastAPI dependencies: the app context, the bearer-token principal, client IP, refresh cookie helpers."""

import uuid
from typing import Annotated

import jwt
from fastapi import Depends, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from pitchvision_identity.context import Ctx
from pitchvision_identity.services.sessions import unauthenticated

REFRESH_COOKIE = "pv_refresh"
REFRESH_COOKIE_PATH = "/api/v1/auth"
OIDC_COOKIE = "pv_oidc"
OIDC_COOKIE_PATH = "/api/v1/auth/oidc"

_bearer = HTTPBearer(auto_error=False, description="Access token from a Session (RS256 JWT)")


def get_ctx(request: Request) -> Ctx:
    ctx: Ctx = request.app.state.ctx
    return ctx


AppCtx = Annotated[Ctx, Depends(get_ctx)]


def current_user_id(ctx: AppCtx, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]) -> uuid.UUID:
    """Subject of a valid access token. Services re-load the user, so a deleted account gets 401."""
    if creds is None or creds.scheme.lower() != "bearer":
        raise unauthenticated("Missing bearer token.")
    try:
        claims = ctx.keys.verify(creds.credentials)
        return uuid.UUID(claims["sub"])
    except (jwt.PyJWTError, ValueError, KeyError):
        raise unauthenticated("Invalid or expired access token.") from None


UserId = Annotated[uuid.UUID, Depends(current_user_id)]


def client_ip(request: Request) -> str:
    """Peer address (uvicorn --forwarded-allow-ips makes this the X-Forwarded-For client behind nginx)."""
    return request.client.host if request.client else "unknown"


def refresh_cookie(request: Request) -> str | None:
    return request.cookies.get(REFRESH_COOKIE)


def user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


def set_refresh_cookie(response: Response, ctx: Ctx, token: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=ctx.settings.refresh_token_ttl_s,
        path=REFRESH_COOKIE_PATH,
        secure=ctx.settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def clear_refresh_cookie(response: Response, ctx: Ctx) -> None:
    response.delete_cookie(
        REFRESH_COOKIE, path=REFRESH_COOKIE_PATH, secure=ctx.settings.cookie_secure, httponly=True, samesite="lax"
    )
