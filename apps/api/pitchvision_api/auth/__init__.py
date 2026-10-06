"""Authentication: identity-svc RS256 access tokens verified against its JWKS (dev header stub: local only)."""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request, WebSocket
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pitchvision_schemas import ErrorCode

from pitchvision_api.auth.dev import DEV_ORG_HEADER, DEV_ROLE_HEADER, DEV_USER_HEADER, dev_principal
from pitchvision_api.auth.jwks import JwksClient
from pitchvision_api.auth.jwt import unauthenticated, verify_access_token
from pitchvision_api.auth.principal import Principal, Role
from pitchvision_api.errors import ApiError
from pitchvision_api.settings import Settings

__all__ = [
    "DEV_ORG_HEADER",
    "DEV_ROLE_HEADER",
    "DEV_USER_HEADER",
    "JwksClient",
    "Principal",
    "Role",
    "current_principal",
    "require_role",
    "websocket_principal",
]

_bearer = HTTPBearer(auto_error=False, description="identity-svc access token (RS256, 15 min)")


async def current_principal(
    request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]
) -> Principal:
    settings: Settings = request.app.state.settings
    if (dev := dev_principal(request, settings)) is not None:
        return dev
    if credentials is None or not credentials.credentials:
        raise unauthenticated("Send `Authorization: Bearer <access token>`.")
    jwks: JwksClient = request.app.state.jwks
    return await verify_access_token(credentials.credentials, settings, jwks)


async def websocket_principal(websocket: WebSocket, token: str | None) -> Principal:
    """Same verification as REST; the token comes in `?token=` because browsers can't set WS headers (AD §10.3)."""
    settings: Settings = websocket.app.state.settings
    if (dev := dev_principal(websocket, settings)) is not None:
        return dev
    if not token:
        raise unauthenticated("Pass the access token as `?token=`.")
    jwks: JwksClient = websocket.app.state.jwks
    return await verify_access_token(token, settings, jwks)


def require_role(minimum: Role | str) -> Callable[..., Awaitable[Principal]]:
    """Dependency factory: `Depends(require_role("editor"))` → the principal, or 403 FORBIDDEN."""
    min_role = Role(minimum)

    async def check(principal: Annotated[Principal, Depends(current_principal)]) -> Principal:
        if not principal.has_role(min_role):
            raise ApiError(ErrorCode.FORBIDDEN, 403, "Insufficient role", f"Requires role {min_role.value} or higher.")
        return principal

    return check
