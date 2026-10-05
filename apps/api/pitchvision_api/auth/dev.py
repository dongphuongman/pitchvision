"""!!! DEV STUB — NOT AUTHENTICATION !!!

Trusts `X-Dev-Org` (+ optional `X-Dev-User`, `X-Dev-Role`) ONLY when PV_ENV=local AND PV_DEV_AUTH=1
(Settings refuses PV_DEV_AUTH outside local). Everything else goes through identity-svc JWT verification.
"""

import uuid

from fastapi import Request

from pitchvision_api.auth.jwt import unauthenticated
from pitchvision_api.auth.principal import Principal, Role
from pitchvision_api.settings import Settings

DEV_ORG_HEADER = "X-Dev-Org"
DEV_USER_HEADER = "X-Dev-User"
DEV_ROLE_HEADER = "X-Dev-Role"
DEV_USER_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")


def dev_principal(request: Request, settings: Settings) -> Principal | None:
    """Principal from the dev headers, or None when the stub is off or no X-Dev-Org header was sent."""
    if not (settings.env == "local" and settings.dev_auth):
        return None
    raw_org = request.headers.get(DEV_ORG_HEADER)
    if not raw_org:
        return None
    try:
        org_id = uuid.UUID(raw_org)
        user_id = uuid.UUID(request.headers.get(DEV_USER_HEADER) or str(DEV_USER_ID))
        role = Role(request.headers.get(DEV_ROLE_HEADER) or Role.OWNER)
    except ValueError:
        raise unauthenticated(f"{DEV_ORG_HEADER}/{DEV_USER_HEADER} must be UUIDs, {DEV_ROLE_HEADER} a role.") from None
    return Principal(user_id=user_id, org_id=org_id, role=role, region=settings.region)
