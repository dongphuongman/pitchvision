"""!!! DEV STUB — NOT AUTHENTICATION !!!

Trusts the `X-Dev-Org` header as the caller's org, and ONLY when PV_ENV=local. In every other env it
rejects all requests with 401, so a mis-deployed build fails closed instead of letting anyone pick an org.
Replaced by JWT verification in M1-BE-05; keep `current_principal`'s signature so routers don't change.
"""
import uuid
from dataclasses import dataclass

from fastapi import Request
from pitchvision_schemas import ErrorCode

from pitchvision_api.errors import ApiError
from pitchvision_api.settings import Settings

DEV_ORG_HEADER = "X-Dev-Org"


@dataclass(frozen=True)
class Principal:
    org_id: uuid.UUID
    user_id: uuid.UUID | None = None


def _unauthenticated(detail: str) -> ApiError:
    return ApiError(ErrorCode.UNAUTHENTICATED, 401, "Authentication required", detail)


async def current_principal(request: Request) -> Principal:
    settings: Settings = request.app.state.settings
    if settings.env != "local":
        raise _unauthenticated("JWT authentication is not implemented yet (M1-BE-05).")
    raw = request.headers.get(DEV_ORG_HEADER)
    if not raw:
        raise _unauthenticated(f"PV_ENV=local: send the org id in the {DEV_ORG_HEADER} header.")
    try:
        return Principal(org_id=uuid.UUID(raw))
    except ValueError:
        raise _unauthenticated(f"{DEV_ORG_HEADER} must be a UUID.") from None
